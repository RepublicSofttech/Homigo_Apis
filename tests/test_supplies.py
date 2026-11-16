"""Gap C13: professional brings the cleaning / tool kit."""
from datetime import timedelta
from decimal import Decimal

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.bookings.models import Booking
from apps.catalogue.models import Service
from apps.core.models import AppSetting
from apps.payments.models import CommissionLedger

from .helpers import login, make_live_partner


@override_settings(OTP_DEBUG=True, PAYMENT_GATEWAY="mock")
class SuppliesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", verbosity=0)
        AppSetting.objects.create(key="early_start_hours", value=72)
        cls.cleaner = make_live_partner("9222200001", "Sparkle Cleaners", 28.6260, 77.2190,
                                        service_names=("Home Deep Cleaning", "Maid"))
        cls.deep = Service.objects.get(name="Home Deep Cleaning")
        cls.maid = Service.objects.get(name="Maid")

    def setUp(self):
        self.cust = login("9000000005")
        self.addr = self.cust.get("/api/v1/addresses/").data[0]["id"]

    def slot(self):
        t = timezone.localtime() + timedelta(days=1)
        return t.replace(hour=11, minute=0, second=0, microsecond=0).isoformat()

    def preview(self, service, **extra):
        return self.cust.post("/api/v1/bookings/price-preview/", {
            "service_id": service.id, "items": [{"service_item": service.items.first().id}], **extra}, format="json").data

    def test_included_kit_is_always_brought_and_listed(self):
        p = self.preview(self.deep)
        self.assertTrue(p["bring_supplies"])
        self.assertIn("Mop & bucket", p["supplies_list"])
        self.assertEqual(Decimal(p["supplies_charge"]), Decimal("0.00"))

    def test_optional_kit_adds_charge_only_when_asked(self):
        without = self.preview(self.maid)
        with_kit = self.preview(self.maid, bring_supplies=True)
        self.assertFalse(without["bring_supplies"])
        self.assertTrue(with_kit["bring_supplies"])
        self.assertEqual(Decimal(with_kit["total"]) - Decimal(without["total"]), Decimal("49.00"))

    def test_kit_shown_to_partner_checked_at_start_and_not_commissioned(self):
        b = self.cust.post("/api/v1/bookings/", {
            "service_id": self.maid.id, "address_id": self.addr, "items": [{"service_item": self.maid.items.first().id}],
            "scheduled_start": self.slot(), "bring_supplies": True}, format="json").data
        self.assertTrue(b["bring_supplies"])
        self.assertEqual(Decimal(b["supplies_charge"]), Decimal("49.00"))
        partner = login("9222200001")
        req = partner.get("/api/v1/partner/job-requests/").data[0]
        self.assertTrue(req["bring_supplies"])
        self.assertIn("Floor cleaner", req["supplies_list"])
        partner.post(f"/api/v1/partner/job-requests/{req['id']}/accept/")
        cw_id = self.cleaner.coworkers.first().id
        partner.post(f"/api/v1/bookings/{b['id']}/assign/", {"coworker_ids": [cw_id]}, format="json")
        cw = login(self.cleaner.coworkers.first().phone)
        for a in ("on-the-way", "arrived"):
            cw.post(f"/api/v1/bookings/{b['id']}/{a}/")
        bk = Booking.objects.get(pk=b["id"])
        r = cw.post(f"/api/v1/bookings/{bk.id}/start/", {"otp": bk.start_otp}, format="json")
        self.assertEqual(r.data["code"], "KIT_NOT_CONFIRMED")
        r = cw.post(f"/api/v1/bookings/{bk.id}/start/", {"otp": bk.start_otp, "kit_confirmed": True}, format="json")
        self.assertEqual(r.data["status"], "in_progress")
        self.assertTrue(r.data["supplies_confirmed"])
        cw.post(f"/api/v1/bookings/{bk.id}/complete/", {"otp": bk.end_otp}, format="json")
        led = CommissionLedger.objects.get(booking=bk)
        expected_value = bk.subtotal + bk.visit_charge
        self.assertEqual(led.service_value, expected_value)  # kit charge excluded from commission
        self.assertEqual(led.partner_net, expected_value + Decimal("49.00") - led.commission_amount - led.gst_on_commission)

    def test_service_without_kit_ignores_request(self):
        plumber = Service.objects.get(name="Plumber")
        p = self.preview(plumber, bring_supplies=True)
        self.assertFalse(p["bring_supplies"])
        self.assertEqual(Decimal(p["supplies_charge"]), Decimal("0.00"))
