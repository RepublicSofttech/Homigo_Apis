from datetime import timedelta

from django.utils import timezone
from rest_framework import generics, mixins, permissions, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.bookings.models import Booking
from apps.core.config_defaults import get_setting
from apps.core.exceptions import BusinessError
from apps.core.permissions import IsCustomer, IsPartner, IsPlatformAdmin
from apps.core.utils import audit
from apps.notifications.services import notify
from apps.partners.views import get_partner

from .models import Review
from .serializers import ReviewSerializer
from .services import refresh_ratings


class CustomerReviewViewSet(mixins.CreateModelMixin, mixins.ListModelMixin, viewsets.GenericViewSet):
    """BR-R1: only completed bookings, one review per booking, within the review window."""

    permission_classes = [IsCustomer]
    serializer_class = ReviewSerializer

    def get_queryset(self):
        return Review.objects.filter(customer=self.request.user)

    def perform_create(self, serializer):
        booking = serializer.validated_data["booking"]
        if booking.customer_id != self.request.user.id:
            raise BusinessError("NOT_YOUR_BOOKING", "Booking not found.", 404)
        if booking.status != "completed":
            raise BusinessError("REVIEW_NOT_ALLOWED", "You can review only completed bookings.")
        window = int(get_setting("review_window_days"))
        if booking.completed_at and booking.completed_at < timezone.now() - timedelta(days=window):
            raise BusinessError("REVIEW_WINDOW_CLOSED", f"Reviews are allowed within {window} days of completion.")
        if hasattr(booking, "review"):
            raise BusinessError("ALREADY_REVIEWED", "You have already reviewed this booking.", 409)
        coworkers = booking.active_coworkers
        review = serializer.save(customer=self.request.user, partner=booking.partner,
                                 coworker=coworkers[0] if coworkers else None)
        refresh_ratings(booking.partner, review.coworker)
        notify(booking.partner.owner, "review_received", f"New {review.rating_partner}★ review",
               review.comment[:120] or booking.booking_no, booking_id=booking.id)


class PartnerPublicReviews(generics.ListAPIView):
    permission_classes = [permissions.AllowAny]
    serializer_class = ReviewSerializer

    def get_queryset(self):
        return Review.objects.filter(partner_id=self.kwargs["pk"], is_published=True).select_related("customer", "booking__service")


class PartnerReviewViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    permission_classes = [IsPartner]
    serializer_class = ReviewSerializer

    def get_queryset(self):
        return Review.objects.filter(partner=get_partner(self.request))

    @action(detail=True, methods=["post"])
    def reply(self, request, pk=None):
        r = self.get_object()
        if r.reply:
            raise BusinessError("ALREADY_REPLIED", "You can reply only once.")
        text = request.data.get("reply", "").strip()
        if not text:
            raise BusinessError("REPLY_REQUIRED", "Reply cannot be empty.")
        r.reply, r.replied_at = text, timezone.now()
        r.save(update_fields=["reply", "replied_at"])
        return Response(ReviewSerializer(r).data)

    @action(detail=True, methods=["post"])
    def report(self, request, pk=None):
        r = self.get_object()
        r.reported = True
        r.save(update_fields=["reported"])
        return Response(ReviewSerializer(r).data)


class AdminReviewViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    permission_classes = [IsPlatformAdmin]
    serializer_class = ReviewSerializer
    queryset = Review.objects.select_related("customer", "partner", "booking__service").all()
    filterset_fields = ["reported", "is_published", "partner"]

    @action(detail=True, methods=["post"])
    def moderate(self, request, pk=None):
        r = self.get_object()
        note = request.data.get("note", "").strip()
        if not note:
            raise BusinessError("REASON_REQUIRED", "Moderation needs a policy reason.")
        r.is_published = bool(request.data.get("publish", False))
        r.moderation_note, r.reported = note, False
        r.save()
        refresh_ratings(r.partner, r.coworker)
        audit(request.user, "review_moderate", r, publish=r.is_published, note=note)
        return Response(ReviewSerializer(r).data)
