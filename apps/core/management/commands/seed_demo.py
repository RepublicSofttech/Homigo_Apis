"""Load catalogue, plans, offers and (optionally) demo accounts.

    python manage.py seed_demo            # catalogue + plans + offers + demo users
    python manage.py seed_demo --catalogue-only
"""
from datetime import timedelta
from decimal import Decimal

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import Address, User
from apps.catalogue.models import Service, ServiceCategory, ServiceItem
from apps.offers.models import Offer
from apps.partners.models import AreaPartner, Coworker, CoworkerDocument, PartnerBankAccount, PartnerDocument, PartnerService, RateCardItem
from apps.subscriptions.models import PartnerSubscription, SubscriptionPlan

CATALOGUE = {
    ("Home Repair", "wrench"): [
        ("Plumber", "tap leak,pipe,drain,nal", ["instant", "scheduled"], 99,
         [("Tap repair / replace", 149), ("Drain blockage removal", 249), ("Wash basin installation", 499), ("Water tank cleaning", 699)]),
        ("Electrician", "light,fan,switch,wiring,bijli", ["instant", "scheduled"], 99,
         [("Switch / socket repair", 99), ("Fan installation", 199), ("Light fitting", 149), ("MCB / fuse repair", 249)]),
        ("AC Mechanic", "ac service,cooling,gas", ["instant", "scheduled"], 149,
         [("Split AC service", 599), ("Window AC service", 499), ("AC gas refill", 2499), ("AC installation", 1499)]),
        ("Carpenter", "furniture,door,wood", ["scheduled"], 99,
         [("Door lock repair", 199), ("Furniture repair (per hour)", 299), ("Curtain rod fitting", 149)]),
    ],
    ("Cleaning & Domestic Help", "sparkles"): [
        ("Home Deep Cleaning", "cleaning,safai,deep clean", ["scheduled"], 0,
         [("1 BHK deep cleaning", 2499), ("2 BHK deep cleaning", 3499), ("Bathroom cleaning", 499), ("Kitchen cleaning", 999)]),
        ("Maid", "maid,bai,house help", ["hourly", "recurring"], 0,
         [("Maid - 2 hours", 299), ("Maid - monthly 2h daily", 6000)]),
        ("Cook", "cook,khana,chef", ["hourly", "recurring"], 0,
         [("Cook - one meal", 349), ("Cook - monthly (2 meals daily)", 8000)]),
    ],
    ("Security", "shield"): [
        ("Security Guard", "guard,chowkidar", ["hourly", "recurring"], 0,
         [("Guard - 12 hour shift", 900), ("Guard - monthly", 18000)]),
    ],
    ("Health at Home", "heart"): [
        ("Physiotherapy", "physio,back pain", ["scheduled"], 0,
         [("Physio session (45 min)", 699), ("3 session package", 1899)]),
    ],
    ("Events", "party"): [
        ("Tent Service", "tent,shamiana,wedding", ["rental", "quote"], 0,
         [("Tent 30x40 ft per day", 8000), ("Chairs (per 50)", 1500)]),
    ],
    ("Daily Essentials", "droplet"): [
        ("Water Can", "water,bisleri,20 litre", ["delivery", "recurring"], 0,
         [("20L water can", 40)]),
    ],
}

PLANS = [
    ("Basic", "basic", 1499, 20, 5, 1, ["1 service", "Up to 5 coworkers", "Weekly settlement"]),
    ("Standard", "standard", 2999, 15, 20, 2, ["Up to 2 services", "Up to 20 coworkers", "Partner offers", "1 banner / month"]),
    ("Premium", "premium", 5999, 10, None, 5, ["Up to 5 services", "Unlimited coworkers", "T+2 settlement", "Featured placement"]),
]

DEMO_LAT, DEMO_LNG = Decimal("28.631500"), Decimal("77.216700")  # Connaught Place, New Delhi (sample)


def fake_file(name):
    return ContentFile(b"%PDF-1.4 demo document", name=name)


class Command(BaseCommand):
    help = "Seed catalogue, plans, offers and demo accounts"

    def add_arguments(self, parser):
        parser.add_argument("--catalogue-only", action="store_true")

    @transaction.atomic
    def handle(self, *args, **opts):
        for order, ((cat_name, icon), services) in enumerate(CATALOGUE.items()):
            cat, _ = ServiceCategory.objects.get_or_create(name=cat_name, defaults={"icon": icon, "sort_order": order})
            for s_order, (name, syn, bts, visit, items) in enumerate(services):
                svc, _ = Service.objects.get_or_create(name=name, defaults={
                    "category": cat, "synonyms": syn, "booking_types": bts, "visit_charge": visit, "sort_order": s_order,
                    "description": f"Verified {name.lower()} professionals at your doorstep.",
                    "min_coworkers": 1,
                })
                for item_name, price in items:
                    ServiceItem.objects.get_or_create(service=svc, name=item_name, defaults={
                        "default_price": price, "price_floor": round(price * 0.7), "price_ceiling": round(price * 1.5),
                    })
        for i, (name, code, price, comm, maxc, maxs, feats) in enumerate(PLANS):
            SubscriptionPlan.objects.get_or_create(code=code, defaults={
                "name": name, "price": price, "commission_percent": comm, "max_coworkers": maxc,
                "max_services": maxs, "features": feats, "sort_order": i,
            })
        now = timezone.now()
        Offer.objects.get_or_create(code="WELCOME100", defaults={
            "title": "₹100 off on your first booking", "discount_type": "flat", "value": 100, "min_order_value": 299,
            "first_booking_only": True, "valid_from": now, "valid_to": now + timedelta(days=365)})
        Offer.objects.get_or_create(code="CLEAN15", defaults={
            "title": "15% off on deep cleaning (up to ₹500)", "discount_type": "percent", "value": 15, "max_discount": 500,
            "service": Service.objects.get(name="Home Deep Cleaning"), "valid_from": now, "valid_to": now + timedelta(days=90)})
        self.stdout.write(self.style.SUCCESS("Catalogue, plans and offers loaded."))
        if opts["catalogue_only"]:
            return
        self.seed_demo_accounts()

    def seed_demo_accounts(self):
        admin, created = User.objects.get_or_create(phone="9000000001", defaults={
            "username": "9000000001", "first_name": "Super", "last_name": "Admin", "user_type": "admin",
            "admin_role": "super_admin", "is_staff": True, "is_superuser": True})
        if created:
            admin.set_password("Admin@12345")
            admin.save()
        owner, _ = User.objects.get_or_create(phone="9000000002", defaults={
            "username": "9000000002", "first_name": "Ramesh", "last_name": "Kumar", "user_type": "partner"})
        partner, _ = AreaPartner.objects.get_or_create(owner=owner, defaults={
            "legal_name": "QuickFix Home Services", "display_name": "QuickFix Home Services", "years_in_business": 6,
            "description": "Trusted plumbing and electrical experts in Central Delhi.", "address_line": "Block B, Connaught Place",
            "city": "New Delhi", "state": "Delhi", "pincode": "110001", "latitude": DEMO_LAT, "longitude": DEMO_LNG,
            "verification_status": "approved", "pan_masked": "XXXXXX123F"})
        PartnerBankAccount.objects.get_or_create(partner=partner, defaults={
            "account_holder": "QuickFix Home Services", "account_masked": "XXXXXXXX4321", "account_hash": "demo",
            "ifsc": "HDFC0000001", "bank_name": "HDFC Bank", "is_verified": True})
        if not partner.documents.exists():
            PartnerDocument.objects.create(partner=partner, doc_type="pan", file=fake_file("pan.pdf"), status="verified", number_masked="XXXXXX123F")
        plan = SubscriptionPlan.objects.get(code="standard")
        if not PartnerSubscription.current_for(partner):
            sub = PartnerSubscription.objects.create(partner=partner, plan=plan, amount=plan.price)
            sub.activate()
        services = list(Service.objects.filter(name__in=["Plumber", "Electrician"]))
        for svc in services:
            PartnerService.objects.get_or_create(partner=partner, service=svc)
            for item in svc.items.all():
                RateCardItem.objects.get_or_create(partner=partner, service_item=item, defaults={"price": item.default_price})
        for phone, name in [("9000000003", "Suresh Yadav"), ("9000000004", "Mohit Sharma")]:
            u, _ = User.objects.get_or_create(phone=phone, defaults={"username": phone, "first_name": name.split()[0],
                                                                     "last_name": name.split()[1], "user_type": "coworker"})
            c, made = Coworker.objects.get_or_create(partner=partner, phone=phone, defaults={
                "user": u, "name": name, "experience_years": 5, "verification_status": "approved", "status": "active",
                "languages": "Hindi"})
            if made:
                c.skills.set(services)
                CoworkerDocument.objects.create(coworker=c, doc_type="police_verification", file=fake_file("pv.pdf"), status="verified")
        cust, _ = User.objects.get_or_create(phone="9000000005", defaults={
            "username": "9000000005", "first_name": "Anita", "last_name": "Verma", "user_type": "customer"})
        Address.objects.get_or_create(user=cust, label="Home", defaults={
            "line1": "12, Janpath Road", "city": "New Delhi", "state": "Delhi", "pincode": "110001",
            "latitude": Decimal("28.625000"), "longitude": Decimal("77.218000"), "is_default": True})
        self.stdout.write(self.style.SUCCESS(
            "Demo accounts: admin 9000000001 / Admin@12345 (password login); partner 9000000002, "
            "coworkers 9000000003-04, customer 9000000005 (OTP login)."))
