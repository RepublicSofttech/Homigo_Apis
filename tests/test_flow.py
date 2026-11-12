"""End-to-end API tests covering the main business rules."""
from datetime import timedelta
from decimal import Decimal

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.bookings.models import Booking
from apps.partners.models import AreaPartner, Coworker
from apps.payments.models import CommissionLedger, PartnerWallet


@override_settings(OTP_DEBUG=True, PAYMENT_GATEWAY="mock")
class FlowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", verbosity=0)

    def setUp(self):
        from django.core.cache import cache
        cache.clear()  # reset OTP throttle between tests

    def login(self, phone, user_type="customer"):
        c = APIClient()
        r = c.post("/api/v1/auth/otp/send/", {"phone": phone}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        r = c.post("/api/v1/auth/otp/verify/", {"phone": phone, "otp": r.data["debug_otp"], "user_type": user_type}, format="json")
        self.assertIn(r.status_code, (200, 201), r.data)
        c.credentials(HTTP_AUTHORIZATION=f"Token {r.data['token']}")
        return c

    def next_slot(self):
        t = timezone.localtime() + timedelta(days=1)
        return t.replace(hour=11, minute=0, second=0, microsecond=0).isoformat()

    def test_otp_wrong_code(self):
        c = APIClient()
        c.post("/api/v1/auth/otp/send/", {"phone": "9876543210"}, format="json")
        r = c.post("/api/v1/auth/otp/verify/", {"phone": "9876543210", "otp": "0000"}, format="json")
        if r.status_code != 200:  # (1 in 10000 chance the random OTP is 0000)
            self.assertEqual(r.data["code"], "INVALID_OTP")

    def test_availability_nearest_partner(self):
        plumber = self.svc("Plumber")
        r = APIClient().get(f"/api/v1/services/{plumber}/availability/?lat=28.625&lng=77.218")
        self.assertTrue(r.data["available"])
        self.assertEqual(r.data["partner"]["display_name"], "QuickFix Home Services")
        far = APIClient().get(f"/api/v1/services/{plumber}/availability/?lat=19.07&lng=72.87")
        self.assertFalse(far.data["available"])

    def svc(self, name):
        from apps.catalogue.models import Service
        return Service.objects.get(name=name).id

    def test_full_booking_lifecycle_cash(self):
        cust = self.login("9000000005")
        partner = self.login("9000000002")
        cw = self.login("9000000003")
        plumber = self.svc("Plumber")
        avail = cust.get(f"/api/v1/services/{plumber}/availability/?lat=28.625&lng=77.218").data
        item = avail["partner"]["rate_card"][0]["service_item"]
        addr = cust.get("/api/v1/addresses/").data[0]["id"]
        r = cust.post("/api/v1/bookings/", {"service_id": plumber, "address_id": addr, "items": [{"service_item": item, "quantity": 2}],
                                            "scheduled_start": self.next_slot(), "payment_mode": "cash", "coupon_code": "WELCOME100"}, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        b = r.data
        self.assertEqual(b["status"], "pending_acceptance")
        self.assertEqual(Decimal(b["discount"]), Decimal("100.00"))
        bid = b["id"]
        # coworker cannot see unassigned booking
        self.assertEqual(cw.get(f"/api/v1/bookings/{bid}/").status_code, 404)
        self.assertEqual(partner.post(f"/api/v1/bookings/{bid}/accept/").status_code, 200)
        coworker = Coworker.objects.get(phone="9000000003")
        r = partner.post(f"/api/v1/bookings/{bid}/assign/", {"coworker_ids": [coworker.id]}, format="json")
        self.assertEqual(r.data["status"], "assigned", r.data)
        self.assertNotIn("start_otp", r.data)  # partner never sees OTP
        cw.post(f"/api/v1/bookings/{bid}/on-the-way/")
        cw.post(f"/api/v1/bookings/{bid}/arrived/")
        bad = cw.post(f"/api/v1/bookings/{bid}/start/", {"otp": "xx"}, format="json")
        self.assertEqual(bad.data["code"], "INVALID_OTP")
        otp = Booking.objects.get(pk=bid)
        r = cw.post(f"/api/v1/bookings/{bid}/start/", {"otp": otp.start_otp}, format="json")
        self.assertEqual(r.data["status"], "in_progress")
        r = cw.post(f"/api/v1/bookings/{bid}/addons/", {"name": "PVC pipe", "price": "120", "is_material": True}, format="json")
        addon = [i for i in r.data["items"] if i["is_addon"]][0]
        blocked = cw.post(f"/api/v1/bookings/{bid}/complete/", {"otp": otp.end_otp}, format="json")
        self.assertEqual(blocked.data["code"], "ADDON_PENDING")
        r = cust.post(f"/api/v1/bookings/{bid}/addons/{addon['id']}/approve/")
        self.assertEqual(Decimal(r.data["addons_total"]), Decimal("120.00"))
        r = cw.post(f"/api/v1/bookings/{bid}/complete/", {"otp": otp.end_otp}, format="json")
        self.assertEqual(r.data["status"], "completed", r.data)
        ledger = CommissionLedger.objects.get(booking_id=bid)
        self.assertEqual(ledger.commission_percent, Decimal("15.00"))  # Standard plan
        wallet = PartnerWallet.objects.get(partner__display_name="QuickFix Home Services")
        self.assertLess(wallet.balance, 0)  # cash job -> commission owed
        # review
        r = cust.post("/api/v1/reviews/", {"booking": bid, "rating_partner": 5, "rating_coworker": 4, "comment": "Great"}, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        dup = cust.post("/api/v1/reviews/", {"booking": bid, "rating_partner": 5}, format="json")
        self.assertIn(dup.status_code, (400, 409))
        self.assertEqual(AreaPartner.objects.get(pk=ledger.partner_id).rating_avg, Decimal("5.00"))

    def test_online_payment_and_cancel_refund(self):
        cust = self.login("9000000005")
        elec = self.svc("Electrician")
        avail = cust.get(f"/api/v1/services/{elec}/availability/?lat=28.625&lng=77.218").data
        item = avail["partner"]["rate_card"][0]["service_item"]
        addr = cust.get("/api/v1/addresses/").data[0]["id"]
        r = cust.post("/api/v1/bookings/", {"service_id": elec, "address_id": addr, "items": [{"service_item": item}],
                                            "scheduled_start": self.next_slot(), "payment_mode": "online"}, format="json")
        self.assertEqual(r.data["status"], "payment_pending")
        pay = cust.post("/api/v1/payments/initiate/", {"purpose": "booking", "booking_id": r.data["id"]}, format="json").data
        v = cust.post("/api/v1/payments/verify/", {"payment_id": pay["payment_id"]}, format="json")
        self.assertEqual(v.data["status"], "success")
        b = cust.get(f"/api/v1/bookings/{r.data['id']}/").data
        self.assertEqual(b["status"], "pending_acceptance")
        self.assertEqual(b["payment_status"], "paid")
        c = cust.post(f"/api/v1/bookings/{b['id']}/cancel/", {"reason": "Plan changed"}, format="json")
        self.assertEqual(c.data["status"], "cancelled")
        self.assertEqual(c.data["payment_status"], "refunded")

    def test_reject_without_fallback_cancels(self):
        cust = self.login("9000000005")
        partner = self.login("9000000002")
        plumber = self.svc("Plumber")
        avail = cust.get(f"/api/v1/services/{plumber}/availability/?lat=28.625&lng=77.218").data
        addr = cust.get("/api/v1/addresses/").data[0]["id"]
        r = cust.post("/api/v1/bookings/", {"service_id": plumber, "address_id": addr,
                                            "items": [{"service_item": avail["partner"]["rate_card"][0]["service_item"]}],
                                            "scheduled_start": self.next_slot()}, format="json")
        partner.post(f"/api/v1/bookings/{r.data['id']}/reject/", {"reason": "Busy"}, format="json")
        self.assertEqual(Booking.objects.get(pk=r.data["id"]).status, "cancelled")

    def test_partner_onboarding_and_verification(self):
        p = self.login("9111111111", "partner")
        r = p.post("/api/v1/partner/profile/", {
            "legal_name": "Sharma Electricals", "display_name": "Sharma Electricals", "address_line": "Karol Bagh",
            "city": "New Delhi", "pincode": "110005", "latitude": "28.6519", "longitude": "77.1909", "pan_number": "ABCDE1234F"}, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data["pan_masked"], "XXXXXX234F")
        blocked = p.post("/api/v1/partner/submit-verification/")
        self.assertEqual(blocked.data["code"], "INCOMPLETE_PROFILE")
        from django.core.files.uploadedfile import SimpleUploadedFile
        p.post("/api/v1/partner/documents/", {"doc_type": "pan", "file": SimpleUploadedFile("pan.pdf", b"x"), "number": "ABCDE1234F"})
        p.put("/api/v1/partner/bank-account/", {"account_holder": "Sharma", "account_number": "123456789012", "ifsc": "SBIN0000001"}, format="json")
        p.post("/api/v1/partner/services/", {"service": self.svc("Electrician")}, format="json")
        r = p.post("/api/v1/partner/submit-verification/")
        self.assertEqual(r.status_code, 200, r.data)
        admin = APIClient()
        tok = admin.post("/api/v1/auth/password-login/", {"phone": "9000000001", "password": "Admin@12345"}, format="json").data["token"]
        admin.credentials(HTTP_AUTHORIZATION=f"Token {tok}")
        case = admin.get("/api/v1/admin/verification-cases/?status=open").data["results"][0]
        r = admin.post(f"/api/v1/admin/verification-cases/{case['id']}/approve/")
        self.assertEqual(r.data["status"], "approved")
        status = p.get("/api/v1/partner/go-live-status/").data
        self.assertFalse(status["is_live"])  # no subscription / coworkers yet
        # coworker uniqueness BR-C1
        dup = p.post("/api/v1/partner/coworkers/", {"name": "Suresh Yadav", "phone": "9000000003"}, format="json")
        self.assertEqual(dup.data["code"], "COWORKER_ALREADY_LINKED")
        # subscribe with mock payment
        plan = p.get("/api/v1/plans/").data[0]["id"]
        sub = p.post("/api/v1/partner/subscriptions/", {"plan": plan}, format="json").data
        pay = p.post("/api/v1/payments/initiate/", {"purpose": "subscription", "subscription_id": sub["id"]}, format="json").data
        p.post("/api/v1/payments/verify/", {"payment_id": pay["payment_id"]}, format="json")
        self.assertTrue(p.get("/api/v1/partner/subscriptions/current/").data)

    def test_rate_card_guard_rails(self):
        p = self.login("9000000002")
        from apps.catalogue.models import ServiceItem
        item = ServiceItem.objects.filter(service__name="AC Mechanic").first()
        r = p.post("/api/v1/partner/rate-card/", {"service_item": item.id, "price": "1"}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("PRICE_OUT_OF_RANGE", str(r.data))

    def test_admin_endpoints_forbidden_for_customer(self):
        cust = self.login("9000000005")
        self.assertEqual(cust.get("/api/v1/admin/dashboard/").status_code, 403)
        self.assertEqual(User.objects.get(phone="9000000005").user_type, "customer")

    def test_admin_creates_staff_with_password(self):
        admin = APIClient()
        tok = admin.post("/api/v1/auth/password-login/", {"phone": "9000000001", "password": "Admin@12345"}, format="json").data["token"]
        admin.credentials(HTTP_AUTHORIZATION=f"Token {tok}")
        r = admin.post("/api/v1/admin/users/", {"phone": "9333333333", "first_name": "Vera", "user_type": "admin",
                                                "admin_role": "verification_officer", "password": "Verify@1234"}, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertNotIn("password", r.data)
        login = APIClient().post("/api/v1/auth/password-login/", {"phone": "9333333333", "password": "Verify@1234"}, format="json")
        self.assertEqual(login.status_code, 200)
