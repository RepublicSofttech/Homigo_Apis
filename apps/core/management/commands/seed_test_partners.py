"""Bulk TEST partners for QA: fully live-ready (KYC verified, bank verified, subscription, rate card, verified coworkers).

    python manage.py seed_test_partners                     # 50 in Bhopal + 50 across the other cities
    python manage.py seed_test_partners --bhopal 50 --others 50
    python manage.py seed_test_partners --delete            # remove everything this command created

Phone ranges (test only, OTP login; SMS is not sent while SMS_BACKEND=console):
    partners  81000 00001 ...   coworkers  82000 00001 ...   Bhopal test customer 8300000001
Coordinates are approximate locality centres. Ratings are left empty (no fake reviews).
"""
import hashlib
import random
from datetime import timedelta
from decimal import Decimal

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import Address, User
from apps.catalogue.models import Service
from apps.partners.models import (
    AreaPartner, Coworker, CoworkerDocument, PartnerBankAccount, PartnerDocument, PartnerService, RateCardItem,
)
from apps.subscriptions.models import PartnerSubscription, SubscriptionPlan

PARTNER_PHONE0, COWORKER_PHONE0, CUSTOMER_PHONE = 8100000000, 8200000000, "8300000001"
TEST_TAG = "[TEST]"

# (locality, lat, lng, pincode) — approximate
BHOPAL = [
    ("MP Nagar", 23.2332, 77.4343, "462011"), ("New Market", 23.2366, 77.4011, "462003"), ("Arera Colony", 23.2155, 77.4316, "462016"),
    ("Kolar Road", 23.1700, 77.4170, "462042"), ("Hoshangabad Road", 23.1800, 77.4600, "462026"), ("Bairagarh", 23.2770, 77.3380, "462030"),
    ("Shahpura", 23.2010, 77.4200, "462039"), ("Indrapuri", 23.2580, 77.4650, "462022"), ("Ayodhya Nagar", 23.2680, 77.4710, "462041"),
    ("Awadhpuri", 23.2400, 77.4900, "462022"), ("TT Nagar", 23.2370, 77.3970, "462003"), ("Govindpura", 23.2550, 77.4500, "462023"),
    ("Karond", 23.3000, 77.4000, "462038"), ("Gulmohar", 23.1990, 77.4330, "462039"), ("Bawadiya Kalan", 23.1820, 77.4430, "462039"),
    ("Lalghati", 23.2800, 77.3700, "462030"), ("Kohefiza", 23.2700, 77.3800, "462001"), ("Saket Nagar", 23.2050, 77.4550, "462024"),
    ("Misrod", 23.1650, 77.4750, "462047"), ("Jahangirabad", 23.2450, 77.4150, "462008"),
]
OTHER_CITIES = {
    # city: (state, lat, lng, pincode, [localities])
    "New Delhi": ("Delhi", 28.6315, 77.2167, "110001", ["Karol Bagh", "Lajpat Nagar", "Rohini", "Dwarka", "Saket", "Janakpuri", "Mayur Vihar"]),
    "Mumbai": ("Maharashtra", 19.0760, 72.8777, "400001", ["Andheri", "Bandra", "Powai", "Dadar", "Borivali", "Chembur", "Malad"]),
    "Bengaluru": ("Karnataka", 12.9716, 77.5946, "560001", ["Indiranagar", "Koramangala", "Whitefield", "Jayanagar", "HSR Layout", "Malleshwaram"]),
    "Hyderabad": ("Telangana", 17.3850, 78.4867, "500001", ["Banjara Hills", "Gachibowli", "Kukatpally", "Madhapur", "Secunderabad", "Ameerpet"]),
    "Chennai": ("Tamil Nadu", 13.0827, 80.2707, "600001", ["T Nagar", "Adyar", "Velachery", "Anna Nagar", "Tambaram", "Mylapore"]),
    "Kolkata": ("West Bengal", 22.5726, 88.3639, "700001", ["Salt Lake", "Park Street", "Howrah", "New Town", "Behala", "Dum Dum"]),
    "Pune": ("Maharashtra", 18.5204, 73.8567, "411001", ["Kothrud", "Hinjewadi", "Baner", "Viman Nagar", "Hadapsar", "Wakad"]),
    "Lucknow": ("Uttar Pradesh", 26.8467, 80.9462, "226001", ["Gomti Nagar", "Hazratganj", "Aliganj", "Indira Nagar", "Alambagh", "Mahanagar"]),
}
FIRST = ["Ramesh", "Suresh", "Mahesh", "Rajesh", "Anil", "Sunil", "Vikas", "Amit", "Rahul", "Sanjay", "Deepak", "Manoj", "Arun", "Vijay", "Ashok",
         "Pooja", "Neha", "Kavita", "Sunita", "Rekha", "Imran", "Salim", "Farhan", "Arjun", "Rohit", "Nitin", "Gaurav", "Pankaj", "Dinesh", "Kiran"]
LAST = ["Sharma", "Verma", "Gupta", "Yadav", "Patel", "Singh", "Khan", "Mishra", "Tiwari", "Jain", "Chouhan", "Rathore", "Agrawal", "Pandey", "Dubey",
        "Malviya", "Saxena", "Shukla", "Thakur", "Qureshi"]
PREFIX = ["Shree", "Sai", "Om", "Royal", "Prime", "City", "Star", "Ganesh", "Maa Durga", "Shiv", "Galaxy", "Classic", "Perfect", "Smart",
          "Urban", "Quick", "Bright", "Super", "Elite", "Trusted"]
SUFFIX = ["Services", "Solutions", "Experts", "Care", "Hub", "Point", "Works"]
BANKS = [("State Bank of India", "SBIN"), ("HDFC Bank", "HDFC"), ("ICICI Bank", "ICIC"), ("Axis Bank", "UTIB"), ("Punjab National Bank", "PUNB"),
         ("Bank of Baroda", "BARB"), ("Kotak Mahindra Bank", "KKBK")]
ENTITY = ["individual", "proprietorship", "proprietorship", "partnership", "pvt_ltd"]


def doc(name):
    return ContentFile(b"%PDF-1.4 test document (seed_test_partners)", name=name)


def plan_for(n_services):
    return "basic" if n_services == 1 else "standard" if n_services == 2 else "premium"


class Command(BaseCommand):
    help = "Create (or --delete) bulk live-ready TEST partners with coworkers for QA"

    def add_arguments(self, parser):
        parser.add_argument("--bhopal", type=int, default=50)
        parser.add_argument("--others", type=int, default=50)
        parser.add_argument("--delete", action="store_true")
        parser.add_argument("--seed", type=int, default=2026)

    def handle(self, *args, **o):
        if o["delete"]:
            return self.delete()
        self.rng = random.Random(o["seed"])
        self.services = list(Service.objects.filter(is_active=True).prefetch_related("items").order_by("id"))
        if not self.services:
            self.stderr.write("No services. Run: python manage.py seed_demo --catalogue-only")
            return
        self.plans = {p.code: p for p in SubscriptionPlan.objects.all()}
        self.svc_cursor = {}
        self.coworker_no = 0
        created = 0
        cities = list(OTHER_CITIES)
        self.city_counts = {"Bhopal": o["bhopal"]}
        for i in range(o["others"]):
            self.city_counts[cities[i % len(cities)]] = self.city_counts.get(cities[i % len(cities)], 0) + 1
        with transaction.atomic():
            # Bhopal
            for i in range(o["bhopal"]):
                loc, lat, lng, pin = BHOPAL[i % len(BHOPAL)]
                created += self.make_partner(created + 1, "Bhopal", "Madhya Pradesh", loc, lat, lng, pin, radius=10)
            # Other cities (spread evenly)
            cities = list(OTHER_CITIES.items())
            for i in range(o["others"]):
                city, (state, clat, clng, pin, locs) = cities[i % len(cities)]
                loc = locs[(i // len(cities)) % len(locs)]
                lat = clat + self.rng.uniform(-0.06, 0.06)
                lng = clng + self.rng.uniform(-0.06, 0.06)
                created += self.make_partner(created + 1, city, state, loc, lat, lng, pin, radius=12)
            self.make_bhopal_customer()
        self.report()

    # ------------------------------------------------------------------
    def next_services(self, city, n):
        """Round-robin per city so every city covers every service."""
        cur = self.svc_cursor.get(city, 0)
        out = [self.services[(cur + k) % len(self.services)] for k in range(n)]
        self.svc_cursor[city] = cur + n
        return list({s.id: s for s in out}.values())

    def make_partner(self, n, city, state, locality, lat, lng, pincode, radius):
        phone = str(PARTNER_PHONE0 + n)
        if User.objects.filter(phone=phone).exists():
            return 1  # already created on a previous run (idempotent)
        rng = self.rng
        # Bhopal: 1–3 services each. Smaller cities: enough per partner to cover the whole catalogue.
        per_city = self.city_counts.get(city, 1)
        min_needed = -(-len(self.services) // max(per_city, 1))
        services = self.next_services(city, max(min_needed, rng.choice([1, 2, 2, 3])) if city != "Bhopal" else rng.choice([1, 2, 2, 3]))
        first, last = rng.choice(FIRST), rng.choice(LAST)
        main = services[0].name.replace("Home ", "")
        biz = f"{rng.choice(PREFIX)} {main} {rng.choice(SUFFIX)}"
        owner = User.objects.create(username=phone, phone=phone, first_name=first, last_name=last, user_type="partner",
                                    email=f"partner{n}@test.homigo.local", is_phone_verified=True, language="hi" if rng.random() < 0.5 else "en")
        p = AreaPartner.objects.create(
            owner=owner, legal_name=f"{biz} {TEST_TAG}", display_name=biz, entity_type=rng.choice(ENTITY),
            description=f"{TEST_TAG} {', '.join(s.name for s in services)} in {locality}, {city}. Test partner for QA.",
            years_in_business=rng.randint(1, 15), gstin="" if rng.random() < 0.5 else f"23ABCDE{n:04d}F1Z{rng.randint(1, 9)}",
            pan_masked=f"XXXXXX{rng.randint(100, 999)}F", address_line=f"Shop {rng.randint(1, 120)}, {locality}",
            city=city, state=state, pincode=pincode, latitude=Decimal(str(round(lat, 6))), longitude=Decimal(str(round(lng, 6))),
            service_radius_km=Decimal(radius), working_hours_start="08:00", working_hours_end="20:00",
            languages=rng.choice(["Hindi, English", "Hindi", "Hindi, English, Urdu"]), accepts_cash=True,
            verification_status="approved", account_status="active",
        )
        for dt, num in [("pan", p.pan_masked), ("aadhaar", f"XXXX XXXX {rng.randint(1000, 9999)}"), ("business_proof", ""),
                        ("address_proof", ""), ("cancelled_cheque", ""), ("agreement", "")]:
            PartnerDocument.objects.create(partner=p, doc_type=dt, file=doc(f"{dt}.pdf"), number_masked=num, status="verified")
        bank, code = rng.choice(BANKS)
        last4 = f"{rng.randint(1000, 9999)}"
        PartnerBankAccount.objects.create(partner=p, account_holder=biz, account_masked=f"XXXXXXXX{last4}",
                                          account_hash=hashlib.sha256(f"test-{phone}".encode()).hexdigest(),
                                          ifsc=f"{code}0{rng.randint(100000, 999999)}", bank_name=bank, upi_id=f"{phone}@upi", is_verified=True)
        plan = self.plans.get(plan_for(len(services))) or next(iter(self.plans.values()))
        sub = PartnerSubscription.objects.create(partner=p, plan=plan, amount=plan.price)
        sub.activate()
        PartnerSubscription.objects.filter(pk=sub.pk).update(end_date=timezone.localdate() + timedelta(days=365))
        for svc in services:
            PartnerService.objects.create(partner=p, service=svc)
            for item in svc.items.filter(is_active=True):
                lo = item.price_floor or item.default_price
                hi = item.price_ceiling or item.default_price
                price = Decimal(round(rng.uniform(float(lo), float(hi)) / 10) * 10) if hi > lo else item.default_price
                price = min(max(price, lo), hi)
                RateCardItem.objects.create(partner=p, service_item=item, price=price)
        # coworkers: at least max(min_coworkers) per service, 2–4 in total
        need = max([s.min_coworkers or 1 for s in services] + [rng.randint(2, 4)])
        for _ in range(need):
            self.coworker_no += 1
            cphone = str(COWORKER_PHONE0 + self.coworker_no)
            while User.objects.filter(phone=cphone).exists():
                self.coworker_no += 1
                cphone = str(COWORKER_PHONE0 + self.coworker_no)
            cf, cl = rng.choice(FIRST), rng.choice(LAST)
            u = User.objects.create(username=cphone, phone=cphone, first_name=cf, last_name=cl, user_type="coworker", is_phone_verified=True)
            c = Coworker.objects.create(
                partner=p, user=u, name=f"{cf} {cl}", phone=cphone, gender=rng.choice(["male", "male", "female"]),
                languages="Hindi", experience_years=Decimal(rng.randint(1, 12)), skill_level=rng.choice(["skilled", "skilled", "expert"]),
                id_number_masked=f"XXXX XXXX {rng.randint(1000, 9999)}", emergency_contact=str(9100000000 + rng.randint(1, 99999)),
                verification_status="approved", status="active",
            )
            c.skills.set(services)
            for dt in ("id_proof", "police_verification", "address_proof"):
                CoworkerDocument.objects.create(coworker=c, doc_type=dt, file=doc(f"{dt}.pdf"), status="verified")
        live = p.go_live_checklist()[1]
        if not live:
            self.stderr.write(f"WARNING: {biz} ({phone}) is not live: {[c for c, ok in p.go_live_checklist()[0] if not ok]}")
        return 1

    def make_bhopal_customer(self):
        u, _ = User.objects.get_or_create(phone=CUSTOMER_PHONE, defaults={
            "username": CUSTOMER_PHONE, "first_name": "Test", "last_name": "Customer Bhopal", "user_type": "customer", "is_phone_verified": True})
        Address.objects.get_or_create(user=u, label="Home", defaults={
            "line1": "E-5, Arera Colony", "city": "Bhopal", "state": "Madhya Pradesh", "pincode": "462016",
            "latitude": Decimal("23.215500"), "longitude": Decimal("77.431600"), "is_default": True})
        Address.objects.get_or_create(user=u, label="Office", defaults={
            "line1": "Zone-II, MP Nagar", "city": "Bhopal", "state": "Madhya Pradesh", "pincode": "462011",
            "latitude": Decimal("23.233200"), "longitude": Decimal("77.434300"), "is_default": False})

    def report(self):
        qs = AreaPartner.objects.filter(owner__phone__startswith="81000")
        live = sum(1 for p in qs if p.go_live_checklist()[1])
        self.stdout.write(self.style.SUCCESS(
            f"Test partners: {qs.count()} (live: {live}) · coworkers: {Coworker.objects.filter(partner__in=qs).count()} · "
            f"Bhopal: {qs.filter(city='Bhopal').count()}"))
        for city in ["Bhopal"] + list(OTHER_CITIES):
            cq = qs.filter(city=city)
            covered = Service.objects.filter(partners__in=cq).distinct().count()
            self.stdout.write(f"  {city}: {cq.count()} partners, services covered {covered}/{len(self.services)}")
        first = qs.filter(city="Bhopal").order_by("owner__phone").first()
        if first:
            cw = first.coworkers.first()
            self.stdout.write(f"Login samples (OTP): Bhopal partner {first.owner.phone} ({first.display_name}), "
                              f"its coworker {cw.phone if cw else '-'}, Bhopal customer {CUSTOMER_PHONE}")

    @transaction.atomic
    def delete(self):
        users = User.objects.filter(phone__regex=r"^(8100|8200)\d{6}$") | User.objects.filter(phone=CUSTOMER_PHONE)
        n_p = AreaPartner.objects.filter(owner__in=users).count()
        n = users.count()
        users.delete()  # cascades to partners, coworkers, documents, rate cards, subscriptions, addresses
        self.stdout.write(self.style.SUCCESS(f"Deleted {n_p} test partners and {n} test users."))
