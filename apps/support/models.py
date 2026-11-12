from django.conf import settings
from django.db import models

from apps.core.models import TimeStampedModel


class Ticket(TimeStampedModel):
    CATEGORIES = [
        ("booking", "Booking issue"), ("quality", "Service quality"), ("payment", "Payment & refund"),
        ("safety", "Safety"), ("partner", "Partner support"), ("verification", "Verification query"), ("other", "Other"),
    ]
    STATUSES = [("open", "Open"), ("in_progress", "In progress"), ("resolved", "Resolved"), ("closed", "Closed")]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="tickets")
    booking = models.ForeignKey("bookings.Booking", null=True, blank=True, on_delete=models.SET_NULL, related_name="tickets")
    category = models.CharField(max_length=20, choices=CATEGORIES)
    subject = models.CharField(max_length=150)
    status = models.CharField(max_length=20, choices=STATUSES, default="open")
    priority = models.CharField(max_length=10, default="normal")
    assigned_to = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    resolution = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]


class TicketMessage(models.Model):
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name="messages")
    sender = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
