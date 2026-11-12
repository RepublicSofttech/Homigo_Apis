from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.catalogue.models import Service, ServiceItem
from apps.core.models import TimeStampedModel


class VerificationStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    SUBMITTED = "submitted", "Submitted"
    UNDER_REVIEW = "under_review", "Under Review"
    RESUBMISSION = "resubmission_required", "Resubmission Required"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"


class DocumentStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    VERIFIED = "verified", "Verified"
    REJECTED = "rejected", "Rejected"


class AreaPartner(TimeStampedModel):
    class EntityType(models.TextChoices):
        INDIVIDUAL = "individual", "Individual"
        PROPRIETORSHIP = "proprietorship", "Proprietorship"
        PARTNERSHIP = "partnership", "Partnership"
        LLP = "llp", "LLP"
        PVT_LTD = "pvt_ltd", "Pvt Ltd"

    class AccountStatus(models.TextChoices):
        ACTIVE = "active", "Active"
        SUSPENDED = "suspended", "Suspended"
        TERMINATED = "terminated", "Terminated"

    owner = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="partner")
    legal_name = models.CharField(max_length=150)
    display_name = models.CharField(max_length=100)
    entity_type = models.CharField(max_length=20, choices=EntityType.choices, default=EntityType.INDIVIDUAL)
    description = models.TextField(blank=True)
    logo = models.ImageField(upload_to="partners/logos/", null=True, blank=True)
    years_in_business = models.PositiveSmallIntegerField(default=0)
    gstin = models.CharField(max_length=15, blank=True)
    pan_masked = models.CharField(max_length=10, blank=True)
    # Base location (not shown publicly)
    address_line = models.CharField(max_length=255)
    city = models.CharField(max_length=100)
    state = models.CharField(max_length=100, blank=True)
    pincode = models.CharField(max_length=10)
    latitude = models.DecimalField(max_digits=9, decimal_places=6)
    longitude = models.DecimalField(max_digits=9, decimal_places=6)
    service_radius_km = models.DecimalField(max_digits=5, decimal_places=1, default=5)
    working_hours_start = models.TimeField(default="08:00")
    working_hours_end = models.TimeField(default="20:00")
    languages = models.CharField(max_length=100, blank=True, default="Hindi, English")
    accepts_cash = models.BooleanField(default=True)
    verification_status = models.CharField(max_length=30, choices=VerificationStatus.choices, default=VerificationStatus.DRAFT)
    account_status = models.CharField(max_length=20, choices=AccountStatus.choices, default=AccountStatus.ACTIVE)
    rejection_reason = models.TextField(blank=True)
    holiday_until = models.DateField(null=True, blank=True)
    rating_avg = models.DecimalField(max_digits=3, decimal_places=2, default=0)
    rating_count = models.PositiveIntegerField(default=0)
    jobs_completed = models.PositiveIntegerField(default=0)
    services = models.ManyToManyField(Service, through="PartnerService", related_name="partners")

    def __str__(self):
        return self.display_name

    @property
    def is_verified(self):
        return self.verification_status == VerificationStatus.APPROVED

    @property
    def on_holiday(self):
        return bool(self.holiday_until and self.holiday_until >= timezone.localdate())

    def active_subscription(self):
        from apps.subscriptions.models import PartnerSubscription
        return PartnerSubscription.current_for(self)

    def go_live_checklist(self, service=None):
        """BR-P1 go-live gate. Returns list of (check, ok) and overall bool."""
        sub = self.active_subscription()
        bank = getattr(self, "bank_account", None)
        checks = [
            ("Partner verification approved", self.is_verified),
            ("Account active", self.account_status == self.AccountStatus.ACTIVE),
            ("Not on holiday", not self.on_holiday),
            ("Subscription active", sub is not None),
            ("Bank account verified", bool(bank and bank.is_verified)),
        ]
        services = [service] if service else list(self.services.filter(partnerservice__is_active=True))
        for svc in services:
            has_rate = self.rate_card.filter(service_item__service=svc, is_active=True).exists()
            approved = self.coworkers.filter(
                verification_status=VerificationStatus.APPROVED, status=Coworker.Status.ACTIVE, skills=svc
            ).count()
            checks.append((f"{svc.name}: rate card has an active item", has_rate))
            checks.append((f"{svc.name}: approved coworkers {approved}/{svc.min_coworkers}", approved >= svc.min_coworkers))
        if not services:
            checks.append(("At least one service selected", False))
        return checks, all(ok for _, ok in checks)


class PartnerService(models.Model):
    partner = models.ForeignKey(AreaPartner, on_delete=models.CASCADE)
    service = models.ForeignKey(Service, on_delete=models.CASCADE)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("partner", "service")


class PartnerDocument(TimeStampedModel):
    DOC_TYPES = [
        ("pan", "PAN card"), ("aadhaar", "Aadhaar (masked)"), ("gst", "GST certificate"),
        ("business_proof", "Business registration / Udyam"), ("address_proof", "Address proof"),
        ("cancelled_cheque", "Cancelled cheque"), ("service_licence", "Service licence"),
        ("agreement", "Signed partner agreement"), ("other", "Other"),
    ]
    partner = models.ForeignKey(AreaPartner, on_delete=models.CASCADE, related_name="documents")
    doc_type = models.CharField(max_length=30, choices=DOC_TYPES)
    file = models.FileField(upload_to="private/partner_docs/")
    number_masked = models.CharField(max_length=30, blank=True)
    expiry_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=DocumentStatus.choices, default=DocumentStatus.PENDING)
    reason = models.CharField(max_length=255, blank=True)


class PartnerBankAccount(TimeStampedModel):
    partner = models.OneToOneField(AreaPartner, on_delete=models.CASCADE, related_name="bank_account")
    account_holder = models.CharField(max_length=100)
    account_masked = models.CharField(max_length=30)
    account_hash = models.CharField(max_length=64)
    ifsc = models.CharField(max_length=11)
    bank_name = models.CharField(max_length=100, blank=True)
    upi_id = models.CharField(max_length=60, blank=True)
    is_verified = models.BooleanField(default=False)


class RateCardItem(TimeStampedModel):
    partner = models.ForeignKey(AreaPartner, on_delete=models.CASCADE, related_name="rate_card")
    service_item = models.ForeignKey(ServiceItem, on_delete=models.CASCADE, related_name="partner_rates")
    price = models.DecimalField(max_digits=10, decimal_places=2)
    is_active = models.BooleanField(default=True)

    class Meta:
        unique_together = ("partner", "service_item")


class Coworker(TimeStampedModel):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        INACTIVE = "inactive", "Inactive"
        SUSPENDED = "suspended", "Suspended"
        BLACKLISTED = "blacklisted", "Blacklisted"
        RELEASED = "released", "Released"

    class SkillLevel(models.TextChoices):
        HELPER = "helper", "Helper"
        SKILLED = "skilled", "Skilled"
        EXPERT = "expert", "Expert"

    partner = models.ForeignKey(AreaPartner, on_delete=models.CASCADE, related_name="coworkers")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="coworker_profiles")
    name = models.CharField(max_length=100)
    phone = models.CharField(max_length=15)
    gender = models.CharField(max_length=10, blank=True)
    date_of_birth = models.DateField(null=True, blank=True)
    photo = models.ImageField(upload_to="coworkers/", null=True, blank=True)
    languages = models.CharField(max_length=100, blank=True)
    experience_years = models.DecimalField(max_digits=4, decimal_places=1, default=0)
    skill_level = models.CharField(max_length=10, choices=SkillLevel.choices, default=SkillLevel.SKILLED)
    skills = models.ManyToManyField(Service, related_name="coworkers", blank=True)
    id_number_hash = models.CharField(max_length=64, blank=True)
    id_number_masked = models.CharField(max_length=20, blank=True)
    emergency_contact = models.CharField(max_length=15, blank=True)
    max_jobs_per_day = models.PositiveSmallIntegerField(default=6)
    verification_status = models.CharField(max_length=30, choices=VerificationStatus.choices, default=VerificationStatus.DRAFT)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.INACTIVE)
    rejection_reason = models.TextField(blank=True)
    rating_avg = models.DecimalField(max_digits=3, decimal_places=2, default=0)
    rating_count = models.PositiveIntegerField(default=0)
    jobs_completed = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.partner})"

    @property
    def is_assignable(self):
        """BR-C2: only approved + active coworkers get jobs."""
        return self.verification_status == VerificationStatus.APPROVED and self.status == self.Status.ACTIVE


class CoworkerDocument(TimeStampedModel):
    DOC_TYPES = [
        ("id_proof", "Government ID"), ("police_verification", "Police verification"),
        ("address_proof", "Address proof"), ("driving_licence", "Driving licence"),
        ("skill_certificate", "Skill / trade certificate"), ("medical_fitness", "Medical fitness"),
        ("other", "Other"),
    ]
    coworker = models.ForeignKey(Coworker, on_delete=models.CASCADE, related_name="documents")
    doc_type = models.CharField(max_length=30, choices=DOC_TYPES)
    file = models.FileField(upload_to="private/coworker_docs/")
    number_masked = models.CharField(max_length=30, blank=True)
    expiry_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=DocumentStatus.choices, default=DocumentStatus.PENDING)
    reason = models.CharField(max_length=255, blank=True)


class VerificationCase(TimeStampedModel):
    class Subject(models.TextChoices):
        PARTNER = "partner", "Partner"
        COWORKER = "coworker", "Coworker"

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        IN_REVIEW = "in_review", "In Review"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        RESUBMISSION = "resubmission_required", "Resubmission Required"

    subject_type = models.CharField(max_length=10, choices=Subject.choices)
    partner = models.ForeignKey(AreaPartner, null=True, blank=True, on_delete=models.CASCADE, related_name="verification_cases")
    coworker = models.ForeignKey(Coworker, null=True, blank=True, on_delete=models.CASCADE, related_name="verification_cases")
    status = models.CharField(max_length=30, choices=Status.choices, default=Status.OPEN)
    assigned_to = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    decided_at = models.DateTimeField(null=True, blank=True)
    note = models.TextField(blank=True)
    sla_due = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["created_at"]
