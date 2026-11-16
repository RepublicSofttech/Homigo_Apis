from django.conf import settings
from django.db import models

from apps.core.models import TimeStampedModel


class BookingStatus(models.TextChoices):
    PAYMENT_PENDING = "payment_pending", "Payment Pending"
    SEARCHING = "searching", "Finding a Professional"
    PENDING_ACCEPTANCE = "pending_acceptance", "Pending Acceptance"
    CONFIRMED = "confirmed", "Confirmed"
    ASSIGNED = "assigned", "Coworker Assigned"
    ON_THE_WAY = "on_the_way", "On the Way"
    ARRIVED = "arrived", "Arrived"
    IN_PROGRESS = "in_progress", "In Progress"
    COMPLETION_REQUESTED = "completion_requested", "Completion Requested"
    COMPLETED = "completed", "Completed"
    CANCELLED = "cancelled", "Cancelled"
    NO_SHOW = "no_show", "No-show"
    DISPUTED = "disputed", "Disputed"


OPEN_STATUSES = [
    BookingStatus.PAYMENT_PENDING, BookingStatus.SEARCHING, BookingStatus.PENDING_ACCEPTANCE, BookingStatus.CONFIRMED,
    BookingStatus.ASSIGNED, BookingStatus.ON_THE_WAY, BookingStatus.ARRIVED, BookingStatus.IN_PROGRESS,
    BookingStatus.COMPLETION_REQUESTED,
]


class Booking(TimeStampedModel):
    class PaymentMode(models.TextChoices):
        ONLINE = "online", "Pay online"
        CASH = "cash", "Cash after service"

    class PaymentStatus(models.TextChoices):
        UNPAID = "unpaid", "Unpaid"
        PAID = "paid", "Paid"
        REFUNDED = "refunded", "Refunded"

    booking_no = models.CharField(max_length=20, unique=True, blank=True)
    customer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="bookings")
    class DispatchMode(models.TextChoices):
        DIRECT = "direct", "Customer chose the provider"
        BROADCAST = "broadcast", "Sent to nearest providers"

    # Empty while a broadcast booking is still searching for a partner.
    partner = models.ForeignKey("partners.AreaPartner", null=True, blank=True, on_delete=models.PROTECT, related_name="bookings")
    dispatch_mode = models.CharField(max_length=10, choices=DispatchMode.choices, default=DispatchMode.DIRECT)
    broadcast_round = models.PositiveSmallIntegerField(default=0)
    service = models.ForeignKey("catalogue.Service", on_delete=models.PROTECT, related_name="bookings")
    booking_type = models.CharField(max_length=20, default="scheduled")
    address = models.JSONField(help_text="Address snapshot")
    latitude = models.DecimalField(max_digits=9, decimal_places=6)
    longitude = models.DecimalField(max_digits=9, decimal_places=6)
    distance_km = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    scheduled_start = models.DateTimeField()
    scheduled_end = models.DateTimeField()
    notes = models.TextField(blank=True)
    answers = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=30, choices=BookingStatus.choices, default=BookingStatus.PENDING_ACCEPTANCE)
    payment_mode = models.CharField(max_length=10, choices=PaymentMode.choices, default=PaymentMode.CASH)
    payment_status = models.CharField(max_length=10, choices=PaymentStatus.choices, default=PaymentStatus.UNPAID)
    # Price snapshot (BR-B2) - all server calculated
    subtotal = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    visit_charge = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    addons_total = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    discount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    partner_discount_share = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    convenience_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    tax = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    amount_paid = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    coupon_code = models.CharField(max_length=30, blank=True)
    start_otp = models.CharField(max_length=6)
    end_otp = models.CharField(max_length=6)
    sla_due_at = models.DateTimeField(null=True, blank=True)
    accepted_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    cancelled_by = models.CharField(max_length=20, blank=True)
    cancel_reason = models.TextField(blank=True)
    cancellation_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    fallback_count = models.PositiveSmallIntegerField(default=0)
    rejected_partner_ids = models.JSONField(default=list, blank=True)
    cash_collected = models.BooleanField(default=False)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.booking_no

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if not self.booking_no:
            self.booking_no = f"HG{self.created_at:%y%m}{self.pk:05d}"
            super().save(update_fields=["booking_no"])

    @property
    def active_coworkers(self):
        return [a.coworker for a in self.assignments.filter(is_active=True).select_related("coworker")]


class BookingItem(models.Model):
    class AddonStatus(models.TextChoices):
        NA = "na", "Booked item"
        PENDING = "pending", "Awaiting customer approval"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    booking = models.ForeignKey(Booking, on_delete=models.CASCADE, related_name="items")
    service_item = models.ForeignKey("catalogue.ServiceItem", null=True, blank=True, on_delete=models.SET_NULL)
    name = models.CharField(max_length=150)
    quantity = models.PositiveIntegerField(default=1)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    total = models.DecimalField(max_digits=10, decimal_places=2)
    is_addon = models.BooleanField(default=False)
    is_material = models.BooleanField(default=False)
    addon_status = models.CharField(max_length=10, choices=AddonStatus.choices, default=AddonStatus.NA)
    created_at = models.DateTimeField(auto_now_add=True)


class BookingAssignment(models.Model):
    booking = models.ForeignKey(Booking, on_delete=models.CASCADE, related_name="assignments")
    coworker = models.ForeignKey("partners.Coworker", on_delete=models.PROTECT, related_name="assignments")
    assigned_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    is_active = models.BooleanField(default=True)
    assigned_at = models.DateTimeField(auto_now_add=True)


class BookingStatusHistory(models.Model):
    booking = models.ForeignKey(Booking, on_delete=models.CASCADE, related_name="history")
    from_status = models.CharField(max_length=30, blank=True)
    to_status = models.CharField(max_length=30)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    note = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]


class BookingOffer(models.Model):
    """Gap P1: a broadcast booking is offered to the nearest partners; the first to accept wins."""

    class Status(models.TextChoices):
        PENDING = "pending", "Waiting for reply"
        ACCEPTED = "accepted", "Accepted"
        DECLINED = "declined", "Declined"
        EXPIRED = "expired", "Expired"
        LOST = "lost", "Taken by another partner"

    booking = models.ForeignKey(Booking, on_delete=models.CASCADE, related_name="offers")
    partner = models.ForeignKey("partners.AreaPartner", on_delete=models.CASCADE, related_name="booking_offers")
    round = models.PositiveSmallIntegerField(default=1)
    distance_km = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    expires_at = models.DateTimeField()
    responded_at = models.DateTimeField(null=True, blank=True)
    decline_reason = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [models.UniqueConstraint(fields=["booking", "partner"], name="uniq_offer_per_partner")]

    def __str__(self):
        return f"{self.booking} -> {self.partner} ({self.status})"
