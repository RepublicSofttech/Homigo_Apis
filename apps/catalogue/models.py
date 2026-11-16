from django.db import models
from django.utils.text import slugify

from apps.core.models import TimeStampedModel

BOOKING_TYPES = [
    ("instant", "BT1 Instant / ASAP visit"),
    ("scheduled", "BT2 Scheduled visit"),
    ("hourly", "BT3 Hourly / shift staffing"),
    ("recurring", "BT4 Recurring subscription"),
    ("quote", "BT5 Inspection → Quote → Project"),
    ("rental", "BT6 Rental / Event"),
    ("delivery", "BT7 Delivery order"),
]

PRICING_MODELS = [
    ("fixed", "Fixed per item"),
    ("visit", "Visit charge + estimate"),
    ("hourly", "Hourly"),
    ("shift", "Per shift / day"),
    ("unit", "Per unit / area"),
    ("package", "Package"),
    ("monthly", "Monthly subscription"),
    ("quote", "Quote"),
]


class ServiceCategory(TimeStampedModel):
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=120, unique=True, blank=True)
    icon = models.CharField(max_length=50, blank=True, help_text="Icon key used by the frontend")
    image = models.ImageField(upload_to="categories/", null=True, blank=True)
    description = models.TextField(blank=True)
    sort_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort_order", "name"]
        verbose_name_plural = "service categories"

    def save(self, *args, **kwargs):
        self.slug = self.slug or slugify(self.name)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class Service(TimeStampedModel):
    category = models.ForeignKey(ServiceCategory, on_delete=models.PROTECT, related_name="services")
    name = models.CharField(max_length=100)
    slug = models.SlugField(max_length=120, unique=True, blank=True)
    synonyms = models.CharField(max_length=255, blank=True, help_text="Comma separated search words")
    icon = models.CharField(max_length=50, blank=True)
    image = models.ImageField(upload_to="services/", null=True, blank=True)
    description = models.TextField(blank=True)
    booking_types = models.JSONField(default=list, help_text="List of booking type codes")
    radius_km = models.DecimalField(max_digits=5, decimal_places=1, default=5)
    min_coworkers = models.PositiveSmallIntegerField(default=1)
    warranty_days = models.PositiveSmallIntegerField(default=7)
    commission_override = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    visit_charge = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    gender_preference_allowed = models.BooleanField(default=False)

    class SuppliesMode(models.TextChoices):
        NONE = "none", "Customer provides supplies"
        INCLUDED = "included", "Professional always brings the kit"
        OPTIONAL = "optional", "Customer can ask the professional to bring the kit"

    # Gap C13: professional brings a standard cleaning / tool kit
    supplies_mode = models.CharField(max_length=10, choices=SuppliesMode.choices, default=SuppliesMode.NONE)
    supplies_list = models.JSONField(default=list, blank=True, help_text='e.g. ["Mop & bucket", "Floor cleaner"]')
    supplies_charge = models.DecimalField(max_digits=10, decimal_places=2, default=0,
                                          help_text="Kit charge added to the bill (0 = free). No commission on it.")
    questions = models.JSONField(default=list, blank=True, help_text='[{"key":"ac_type","label":"AC type","options":["Split","Window"]}]')
    faqs = models.JSONField(default=list, blank=True)
    required_partner_documents = models.JSONField(default=list, blank=True)
    required_coworker_documents = models.JSONField(default=list, blank=True)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "name"]

    def save(self, *args, **kwargs):
        self.slug = self.slug or slugify(self.name)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class ServiceItem(TimeStampedModel):
    service = models.ForeignKey(Service, on_delete=models.CASCADE, related_name="items")
    name = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    pricing_model = models.CharField(max_length=20, choices=PRICING_MODELS, default="fixed")
    unit_label = models.CharField(max_length=30, default="job", help_text="job / hour / shift / sq.ft / can / month")
    default_price = models.DecimalField(max_digits=10, decimal_places=2)
    price_floor = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    price_ceiling = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    duration_minutes = models.PositiveIntegerField(default=60)
    min_qty = models.PositiveIntegerField(default=1)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["service", "default_price"]

    def __str__(self):
        return f"{self.service.name} - {self.name}"
