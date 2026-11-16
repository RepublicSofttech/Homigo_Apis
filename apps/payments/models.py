from django.conf import settings
from django.db import models

from apps.core.models import TimeStampedModel


class Payment(TimeStampedModel):
    class Purpose(models.TextChoices):
        BOOKING = "booking", "Booking"
        SUBSCRIPTION = "subscription", "Partner subscription"
        WALLET_TOPUP = "wallet_topup", "Wallet top-up"
        TIP = "tip", "Tip for professional"

    class Status(models.TextChoices):
        CREATED = "created", "Created"
        SUCCESS = "success", "Success"
        FAILED = "failed", "Failed"
        REFUNDED = "refunded", "Refunded"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="payments")
    purpose = models.CharField(max_length=20, choices=Purpose.choices)
    booking = models.ForeignKey("bookings.Booking", null=True, blank=True, on_delete=models.PROTECT, related_name="payments")
    subscription = models.ForeignKey("subscriptions.PartnerSubscription", null=True, blank=True, on_delete=models.PROTECT, related_name="payments")
    tip = models.ForeignKey("payments.Tip", null=True, blank=True, on_delete=models.PROTECT, related_name="payments")
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    method = models.CharField(max_length=20, default="online")
    gateway = models.CharField(max_length=20, default="mock")
    gateway_order_id = models.CharField(max_length=100, blank=True, db_index=True)
    gateway_payment_id = models.CharField(max_length=100, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.CREATED)
    raw = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]


class Refund(TimeStampedModel):
    payment = models.ForeignKey(Payment, on_delete=models.PROTECT, related_name="refunds")
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    reason = models.CharField(max_length=255)
    status = models.CharField(max_length=20, default="processed")
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)


class PartnerWallet(models.Model):
    partner = models.OneToOneField("partners.AreaPartner", on_delete=models.CASCADE, related_name="wallet")
    balance = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    updated_at = models.DateTimeField(auto_now=True)


class WalletTransaction(models.Model):
    wallet = models.ForeignKey(PartnerWallet, on_delete=models.CASCADE, related_name="transactions")
    amount = models.DecimalField(max_digits=12, decimal_places=2, help_text="+ credit / - debit")
    kind = models.CharField(max_length=30)
    description = models.CharField(max_length=255)
    booking = models.ForeignKey("bookings.Booking", null=True, blank=True, on_delete=models.SET_NULL)
    balance_after = models.DecimalField(max_digits=12, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class CommissionLedger(models.Model):
    booking = models.OneToOneField("bookings.Booking", on_delete=models.PROTECT, related_name="commission")
    partner = models.ForeignKey("partners.AreaPartner", on_delete=models.PROTECT, related_name="commissions")
    service_value = models.DecimalField(max_digits=10, decimal_places=2)
    commission_percent = models.DecimalField(max_digits=5, decimal_places=2)
    commission_amount = models.DecimalField(max_digits=10, decimal_places=2)
    gst_on_commission = models.DecimalField(max_digits=10, decimal_places=2)
    partner_net = models.DecimalField(max_digits=10, decimal_places=2)
    payment_mode = models.CharField(max_length=10)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class Settlement(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending approval"
        PAID = "paid", "Paid"

    partner = models.ForeignKey("partners.AreaPartner", on_delete=models.PROTECT, related_name="settlements")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    utr = models.CharField(max_length=50, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-created_at"]


class Tip(TimeStampedModel):
    """Gap C1: customer tips the professional after a completed job. 100% goes to the coworker
    (paid out through the partner's wallet, no commission)."""

    class Status(models.TextChoices):
        PENDING = "pending", "Awaiting payment"
        PAID = "paid", "Paid"

    booking = models.ForeignKey("bookings.Booking", on_delete=models.PROTECT, related_name="tips")
    customer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="tips_given")
    partner = models.ForeignKey("partners.AreaPartner", on_delete=models.PROTECT, related_name="tips")
    coworker = models.ForeignKey("partners.Coworker", null=True, blank=True, on_delete=models.SET_NULL, related_name="tips")
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
