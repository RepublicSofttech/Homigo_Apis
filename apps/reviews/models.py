from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from apps.core.models import TimeStampedModel

RATING = [MinValueValidator(1), MaxValueValidator(5)]


class Review(TimeStampedModel):
    booking = models.OneToOneField("bookings.Booking", on_delete=models.CASCADE, related_name="review")
    customer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="reviews")
    partner = models.ForeignKey("partners.AreaPartner", on_delete=models.CASCADE, related_name="reviews")
    coworker = models.ForeignKey("partners.Coworker", null=True, blank=True, on_delete=models.SET_NULL, related_name="reviews")
    rating_partner = models.PositiveSmallIntegerField(validators=RATING)
    rating_coworker = models.PositiveSmallIntegerField(validators=RATING, null=True, blank=True)
    tags = models.JSONField(default=list, blank=True)
    comment = models.TextField(blank=True)
    is_published = models.BooleanField(default=True)
    moderation_note = models.CharField(max_length=255, blank=True)
    reply = models.TextField(blank=True)
    replied_at = models.DateTimeField(null=True, blank=True)
    reported = models.BooleanField(default=False)

    class Meta:
        ordering = ["-created_at"]
