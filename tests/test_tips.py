"""Gap C1: tip the worker after a completed job; 100% to the coworker, no commission."""
from datetime import timedelta
from decimal import Decimal

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.bookings.models import Booking
from apps.catalogue.models import Service
from apps.core.models import AppSetting
from apps.partners.models import AreaPartner, Coworker
from apps.payments.models import CommissionLedger, PartnerWallet, Tip

from .helpers import login


@override_settings(OTP_DEBUG=True, PAYMENT_GATEWAY="mock")
class TipTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", verbosity=0)
        AppSetting.objects.create(key="early_start_hours", value=72)

    def completed_booking(self):
        cust, partner, cw = login("9000000005"), login("9000000002"), login("9000000003")
        svc = Service.objects.get(name="Plumber")
        qf = AreaPartner.objects.get(display_name="QuickFix Home Services")
        item = svc.items.order_by("default_price").first()
        t = (timezone.localtime() + timedelta(days=1)).replace(hour=11, minute=0, second=0, microsecond=0)
        b = cust.post("/api/v1/bookings/", {"service_id": svc.id, "partner_id": qf.id,
                                            "address_id": cust.get("/api/v1/addresses/").data[0]["id"],
                                            "items": [{"service_item": item.id}], "scheduled_start": t.isoformat()},
                      format="json").data
        partner.post(f"/api/v1/bookings/{b['id']}/accept/")
        partner.post(f"/api/v1/bookings/{b['id']}/assign/", {"coworker_ids": [Coworker.objects.get(phone="9000000003").id]}, format="json")
        bk = Booking.objects.get(pk=b["id"])
        for a in ("on-the-way", "arrived"):
            cw.post(f"/api/v1/bookings/{bk.id}/{a}/")
        cw.post(f"/api/v1/bookings/{bk.id}/start/", {"otp": bk.start_otp}, format="json")
        cw.post(f"/api/v1/bookings/{bk.id}/complete/", {"otp": bk.end_otp}, format="json")
        return cust, cw, bk

    def test_tip_paid_goes_to_partner_wallet_without_commission(self):
        cust, cw, bk = self.completed_booking()
        wallet_before = PartnerWallet.objects.get(partner=bk.partner).balance
        commission_before = CommissionLedger.objects.get(booking=bk).commission_amount
        r = cust.post(f"/api/v1/bookings/{bk.id}/tip/", {"amount": 50}, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data["coworker"], "Suresh Yadav")
        pay = cust.post("/api/v1/payments/initiate/", {"purpose": "tip", "tip_id": r.data["tip_id"]}, format="json").data
        self.assertEqual(Decimal(str(pay["amount"])), Decimal("50.00"))
        cust.post("/api/v1/payments/verify/", {"payment_id": pay["payment_id"]}, format="json")
        tip = Tip.objects.get(pk=r.data["tip_id"])
        self.assertEqual(tip.status, "paid")
        self.assertEqual(PartnerWallet.objects.get(partner=bk.partner).balance, wallet_before + Decimal("50"))
        self.assertEqual(CommissionLedger.objects.get(booking=bk).commission_amount, commission_before)
        self.assertEqual(Decimal(cust.get(f"/api/v1/bookings/{bk.id}/").data["tip_paid"]), Decimal("50.00"))
        self.assertEqual(Decimal(str(cw.get("/api/v1/coworker/jobs/").data["profile"][0]["tips_total"])), Decimal("50.00"))
        again = cust.post(f"/api/v1/bookings/{bk.id}/tip/", {"amount": 20}, format="json")
        self.assertEqual(again.data["code"], "ALREADY_TIPPED")

    def test_tip_rules(self):
        cust, _, bk = self.completed_booking()
        self.assertEqual(cust.post(f"/api/v1/bookings/{bk.id}/tip/", {"amount": 5}, format="json").data["code"], "INVALID_TIP")
        self.assertEqual(cust.post(f"/api/v1/bookings/{bk.id}/tip/", {"amount": 5000}, format="json").data["code"], "INVALID_TIP")
        Booking.objects.filter(pk=bk.id).update(completed_at=timezone.now() - timedelta(days=30))
        self.assertEqual(cust.post(f"/api/v1/bookings/{bk.id}/tip/", {"amount": 50}, format="json").data["code"], "TIP_WINDOW_CLOSED")

    def test_cannot_tip_unfinished_booking(self):
        cust = login("9000000005")
        svc = Service.objects.get(name="Plumber")
        t = (timezone.localtime() + timedelta(days=1)).replace(hour=15, minute=0, second=0, microsecond=0)
        b = cust.post("/api/v1/bookings/", {"service_id": svc.id, "address_id": cust.get("/api/v1/addresses/").data[0]["id"],
                                            "items": [{"service_item": svc.items.first().id}], "scheduled_start": t.isoformat()},
                      format="json").data
        r = cust.post(f"/api/v1/bookings/{b['id']}/tip/", {"amount": 50}, format="json")
        self.assertEqual(r.data["code"], "TIP_NOT_ALLOWED")
