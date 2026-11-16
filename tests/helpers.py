"""Shared helpers for API tests."""
from decimal import Decimal

from django.core.cache import cache
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.catalogue.models import Service
from apps.partners.models import AreaPartner, Coworker, PartnerBankAccount, PartnerService, RateCardItem
from apps.subscriptions.models import PartnerSubscription, SubscriptionPlan


def login(phone, user_type="customer"):
    cache.clear()
    c = APIClient()
    otp = c.post("/api/v1/auth/otp/send/", {"phone": phone}, format="json").data["debug_otp"]
    r = c.post("/api/v1/auth/otp/verify/", {"phone": phone, "otp": otp, "user_type": user_type}, format="json")
    c.credentials(HTTP_AUTHORIZATION=f"Token {r.data['token']}")
    return c


def make_live_partner(phone, name, lat, lng, service_names=("Plumber",), coworker_phone=None, accepts_cash=True):
    """Create a fully live partner (approved, subscribed, bank verified, rate card, 1 approved coworker)."""
    owner = User.objects.create_user(phone=phone, user_type="partner", first_name=name)
    p = AreaPartner.objects.create(
        owner=owner, legal_name=name, display_name=name, address_line="Test", city="New Delhi", pincode="110001",
        latitude=Decimal(str(lat)), longitude=Decimal(str(lng)), verification_status="approved", accepts_cash=accepts_cash)
    PartnerBankAccount.objects.create(partner=p, account_holder=name, account_masked="XXXX1111", account_hash="x",
                                      ifsc="HDFC0000001", is_verified=True)
    plan = SubscriptionPlan.objects.get(code="basic")
    PartnerSubscription.objects.create(partner=p, plan=plan, amount=plan.price).activate()
    services = list(Service.objects.filter(name__in=service_names))
    for s in services:
        PartnerService.objects.create(partner=p, service=s)
        for item in s.items.all():
            RateCardItem.objects.create(partner=p, service_item=item, price=item.default_price)
    cw_phone = coworker_phone or ("8" + phone[1:])
    cw_user = User.objects.create_user(phone=cw_phone, user_type="coworker", first_name=f"{name} Pro")
    cw = Coworker.objects.create(partner=p, user=cw_user, name=f"{name} Pro", phone=cw_phone,
                                 verification_status="approved", status="active")
    cw.skills.set(services)
    return p
