from django.conf import settings
from django.db import models

from apps.core.models import TimeStampedModel


class Offer(TimeStampedModel):
    class DiscountType(models.TextChoices):
        FLAT = "flat", "Flat amount"
        PERCENT = "percent", "Percentage"

    class FundedBy(models.TextChoices):
        PLATFORM = "platform", "Platform"
        PARTNER = "partner", "Partner"
        SHARED = "shared", "Shared"

    code = models.CharField(max_length=30, unique=True)
    title = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    discount_type = models.CharField(max_length=10, choices=DiscountType.choices)
    value = models.DecimalField(max_digits=10, decimal_places=2)
    max_discount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    min_order_value = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    funded_by = models.CharField(max_length=10, choices=FundedBy.choices, default=FundedBy.PLATFORM)
    partner_share_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0,
                                                help_text="For shared offers: % of discount paid by partner")
    service = models.ForeignKey("catalogue.Service", null=True, blank=True, on_delete=models.CASCADE)
    category = models.ForeignKey("catalogue.ServiceCategory", null=True, blank=True, on_delete=models.CASCADE)
    partner = models.ForeignKey("partners.AreaPartner", null=True, blank=True, on_delete=models.CASCADE,
                                related_name="offers", help_text="Partner-created offer (own bookings only)")
    first_booking_only = models.BooleanField(default=False)
    valid_from = models.DateTimeField()
    valid_to = models.DateTimeField()
    usage_limit = models.PositiveIntegerField(null=True, blank=True)
    per_user_limit = models.PositiveIntegerField(default=1)
    used_count = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    banner_color = models.CharField(max_length=20, blank=True, default="#4F46E5")

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.code


class Redemption(models.Model):
    offer = models.ForeignKey(Offer, on_delete=models.CASCADE, related_name="redemptions")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    booking = models.OneToOneField("bookings.Booking", on_delete=models.CASCADE, related_name="redemption")
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)
