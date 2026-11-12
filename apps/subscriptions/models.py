from datetime import timedelta

from django.db import models
from django.utils import timezone

from apps.core.config_defaults import get_setting
from apps.core.models import TimeStampedModel


class SubscriptionPlan(TimeStampedModel):
    name = models.CharField(max_length=50)
    code = models.SlugField(unique=True)
    description = models.TextField(blank=True)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    duration_days = models.PositiveIntegerField(default=30)
    commission_percent = models.DecimalField(max_digits=5, decimal_places=2)
    max_coworkers = models.PositiveIntegerField(null=True, blank=True, help_text="Empty = unlimited")
    max_services = models.PositiveIntegerField(null=True, blank=True)
    features = models.JSONField(default=list, blank=True)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "price"]

    def __str__(self):
        return f"{self.name} ({self.duration_days}d)"


class PartnerSubscription(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING_PAYMENT = "pending_payment", "Pending payment"
        ACTIVE = "active", "Active"
        CANCELLED = "cancelled", "Cancelled"
        EXPIRED = "expired", "Expired"

    partner = models.ForeignKey("partners.AreaPartner", on_delete=models.CASCADE, related_name="subscriptions")
    plan = models.ForeignKey(SubscriptionPlan, on_delete=models.PROTECT)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING_PAYMENT)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    auto_renew = models.BooleanField(default=False)
    invoice_no = models.CharField(max_length=30, blank=True)

    class Meta:
        ordering = ["-created_at"]

    @property
    def grace_end(self):
        if not self.end_date:
            return None
        return self.end_date + timedelta(days=int(get_setting("subscription_grace_days")))

    @property
    def in_grace(self):
        today = timezone.localdate()
        return bool(self.end_date and self.end_date < today <= self.grace_end)

    @property
    def days_left(self):
        return (self.end_date - timezone.localdate()).days if self.end_date else 0

    @classmethod
    def current_for(cls, partner):
        """BR-S1 active-subscription check (grace included). Cancelled subs run until end date."""
        today = timezone.localdate()
        grace = int(get_setting("subscription_grace_days"))
        return (
            cls.objects.filter(partner=partner, status__in=[cls.Status.ACTIVE, cls.Status.CANCELLED],
                               start_date__lte=today, end_date__gte=today - timedelta(days=grace))
            .select_related("plan").order_by("-end_date").first()
        )

    def activate(self):
        today = timezone.localdate()
        current = PartnerSubscription.current_for(self.partner)
        start = today
        if current and current.pk != self.pk and current.end_date >= today:
            start = current.end_date + timedelta(days=1)  # renewal extends after current period
        self.start_date = start
        self.end_date = start + timedelta(days=self.plan.duration_days - 1)
        self.status = self.Status.ACTIVE
        self.invoice_no = self.invoice_no or f"HS-SUB-{self.pk:06d}"
        self.save()
