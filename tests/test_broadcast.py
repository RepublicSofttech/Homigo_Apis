"""Gap P1: broadcast a booking to the nearest partners; first to accept wins."""
from datetime import timedelta

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.bookings.models import Booking, BookingOffer
from apps.catalogue.models import Service
from apps.core.models import AppSetting
from apps.partners.models import AreaPartner

from .helpers import login, make_live_partner


@override_settings(OTP_DEBUG=True, PAYMENT_GATEWAY="mock")
class BroadcastTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", verbosity=0)
        AppSetting.objects.create(key="early_start_hours", value=72)
        cls.quickfix = AreaPartner.objects.get(display_name="QuickFix Home Services")  # 28.6315, 77.2167
        cls.near = make_live_partner("9111100001", "Near Plumbing", 28.6260, 77.2190)  # ~0.3 km from customer
        cls.far = make_live_partner("9111100002", "Far Plumbing", 28.6600, 77.2300)  # ~4 km

    def setUp(self):
        self.cust = login("9000000005")
        self.plumber = Service.objects.get(name="Plumber")
        self.item = self.plumber.items.order_by("default_price").first()
        self.addr = self.cust.get("/api/v1/addresses/").data[0]["id"]

    def slot(self, hour=11):
        t = timezone.localtime() + timedelta(days=1)
        return t.replace(hour=hour, minute=0, second=0, microsecond=0).isoformat()

    def book(self, **extra):
        body = {"service_id": self.plumber.id, "address_id": self.addr, "items": [{"service_item": self.item.id}],
                "scheduled_start": self.slot(), "payment_mode": "cash", **extra}
        r = self.cust.post("/api/v1/bookings/", body, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        return r.data

    def test_broadcast_goes_to_nearest_partners_and_first_accept_wins(self):
        b = self.book()
        self.assertEqual(b["status"], "searching")
        self.assertEqual(b["dispatch_mode"], "broadcast")
        self.assertIsNone(b["partner"])
        self.assertEqual(b["searching"]["partners_notified"], 3)
        offers = list(BookingOffer.objects.filter(booking_id=b["id"]).order_by("distance_km"))
        self.assertEqual(offers[0].partner, self.near)  # nearest first

        near = login("9111100001")
        reqs = near.get("/api/v1/partner/job-requests/").data
        self.assertEqual(len(reqs), 1)
        self.assertNotIn("12, Janpath", str(reqs[0]))  # exact address hidden before acceptance
        r = near.post(f"/api/v1/partner/job-requests/{reqs[0]['id']}/accept/")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["status"], "confirmed")
        self.assertEqual(r.data["partner_name"], "Near Plumbing")

        far = login("9111100002")
        far_req = BookingOffer.objects.get(booking_id=b["id"], partner=self.far)
        late = far.post(f"/api/v1/partner/job-requests/{far_req.id}/accept/")
        self.assertEqual(late.status_code, 409)
        self.assertEqual(late.data["code"], "OFFER_NOT_AVAILABLE")
        far_req.refresh_from_db()
        self.assertEqual(far_req.status, "lost")
        # winner can now see full booking, loser cannot
        self.assertEqual(near.get(f"/api/v1/bookings/{b['id']}/").status_code, 200)
        self.assertEqual(far.get(f"/api/v1/bookings/{b['id']}/").status_code, 404)

    def test_round_timeout_moves_to_next_batch_then_cancels(self):
        AppSetting.objects.create(key="broadcast_batch_size", value=1)
        AppSetting.objects.create(key="broadcast_max_rounds", value=2)
        b = self.book()
        first = BookingOffer.objects.get(booking_id=b["id"])
        self.assertEqual(first.partner, self.near)
        Booking.objects.filter(pk=b["id"]).update(sla_due_at=timezone.now() - timedelta(minutes=1))
        call_command("process_sla", verbosity=0)
        first.refresh_from_db()
        self.assertEqual(first.status, "expired")
        second = BookingOffer.objects.filter(booking_id=b["id"], round=2).get()
        self.assertEqual(second.partner, self.quickfix)
        Booking.objects.filter(pk=b["id"]).update(sla_due_at=timezone.now() - timedelta(minutes=1))
        call_command("process_sla", verbosity=0)
        self.assertEqual(Booking.objects.get(pk=b["id"]).status, "cancelled")

    def test_all_decline_moves_on_and_finally_cancels(self):
        b = self.book()
        for phone in ("9111100001", "9111100002", "9000000002"):
            c = login(phone)
            for req in c.get("/api/v1/partner/job-requests/").data:
                c.post(f"/api/v1/partner/job-requests/{req['id']}/decline/", {"reason": "busy"}, format="json")
        self.assertEqual(Booking.objects.get(pk=b["id"]).status, "cancelled")

    def test_direct_booking_rejected_is_broadcast_to_others(self):
        b = self.book(partner_id=self.quickfix.id)
        self.assertEqual(b["status"], "pending_acceptance")
        qf = login("9000000002")
        qf.post(f"/api/v1/bookings/{b['id']}/reject/", {"reason": "Busy"}, format="json")
        booking = Booking.objects.get(pk=b["id"])
        self.assertEqual(booking.status, "searching")
        notified = set(booking.offers.values_list("partner_id", flat=True))
        self.assertEqual(notified, {self.near.id, self.far.id})
        near = login("9111100001")
        req = near.get("/api/v1/partner/job-requests/").data[0]
        self.assertEqual(near.post(f"/api/v1/partner/job-requests/{req['id']}/accept/").data["status"], "confirmed")

    def test_online_broadcast_starts_after_payment(self):
        b = self.book(payment_mode="online")
        self.assertEqual(b["status"], "payment_pending")
        self.assertFalse(BookingOffer.objects.filter(booking_id=b["id"]).exists())
        pay = self.cust.post("/api/v1/payments/initiate/", {"purpose": "booking", "booking_id": b["id"]}, format="json").data
        self.cust.post("/api/v1/payments/verify/", {"payment_id": pay["payment_id"]}, format="json")
        self.assertEqual(Booking.objects.get(pk=b["id"]).status, "searching")
        self.assertEqual(BookingOffer.objects.filter(booking_id=b["id"]).count(), 3)

    def test_cash_booking_skips_partners_that_do_not_take_cash(self):
        online_only = make_live_partner("9111100003", "Online Only", 28.6252, 77.2181, accepts_cash=False)
        b = self.book()
        self.assertFalse(BookingOffer.objects.filter(booking_id=b["id"], partner=online_only).exists())

    def test_area_slots_and_platform_price_preview(self):
        day = (timezone.localdate() + timedelta(days=1)).isoformat()
        r = self.cust.get(f"/api/v1/slots/?service_id={self.plumber.id}&date={day}&lat=28.625&lng=77.218")
        self.assertTrue(any(s["available"] for s in r.data["slots"]))
        p = self.cust.post("/api/v1/bookings/price-preview/", {"service_id": self.plumber.id,
                                                               "items": [{"service_item": self.item.id}]}, format="json")
        self.assertEqual(p.status_code, 200, p.data)
        self.assertEqual(p.data["lines"][0]["unit_price"], self.item.default_price)

    def test_cancel_while_searching_closes_offers(self):
        b = self.book()
        r = self.cust.post(f"/api/v1/bookings/{b['id']}/cancel/", {"reason": "changed plan"}, format="json")
        self.assertEqual(r.data["status"], "cancelled")
        self.assertEqual(r.data["cancellation_fee"], "0.00")
        self.assertFalse(BookingOffer.objects.filter(booking_id=b["id"], status="pending").exists())
