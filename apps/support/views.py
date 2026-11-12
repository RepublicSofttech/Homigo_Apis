from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.core.exceptions import BusinessError
from apps.notifications.services import notify

from .models import Ticket, TicketMessage
from .serializers import TicketSerializer


def is_admin(u):
    return u.user_type == "admin" or u.is_superuser


class TicketViewSet(viewsets.ModelViewSet):
    serializer_class = TicketSerializer
    filterset_fields = ["status", "category"]
    http_method_names = ["get", "post", "head", "options"]

    def get_queryset(self):
        qs = Ticket.objects.select_related("user", "booking").prefetch_related("messages__sender")
        return qs if is_admin(self.request.user) else qs.filter(user=self.request.user)

    def perform_create(self, serializer):
        booking = serializer.validated_data.get("booking")
        u = self.request.user
        if booking and not (booking.customer_id == u.id or booking.partner.owner_id == u.id):
            raise BusinessError("NOT_YOUR_BOOKING", "Booking not found.", 404)
        msg = serializer.validated_data.pop("first_message")
        priority = "urgent" if serializer.validated_data["category"] == "safety" else "normal"
        t = serializer.save(user=u, priority=priority)
        TicketMessage.objects.create(ticket=t, sender=u, message=msg)

    @action(detail=True, methods=["post"])
    def messages(self, request, pk=None):
        t = self.get_object()
        text = request.data.get("message", "").strip()
        if not text:
            raise BusinessError("MESSAGE_REQUIRED", "Message cannot be empty.")
        TicketMessage.objects.create(ticket=t, sender=request.user, message=text)
        if is_admin(request.user):
            if t.status == "open":
                t.status = "in_progress"
                t.save(update_fields=["status"])
            notify(t.user, "ticket_reply", f"Support replied: {t.subject}", text[:150], ticket_id=t.id)
        return Response(TicketSerializer(self.get_queryset().get(pk=t.pk)).data)

    @action(detail=True, methods=["post"], url_path="set-status")
    def set_status(self, request, pk=None):
        if not is_admin(request.user):
            raise BusinessError("FORBIDDEN", "Admin only.", 403)
        t = self.get_object()
        status = request.data.get("status")
        if status not in dict(Ticket.STATUSES):
            raise BusinessError("INVALID_STATUS", "Invalid status.")
        t.status, t.resolution = status, request.data.get("resolution", t.resolution)
        t.assigned_to = t.assigned_to or request.user
        t.save()
        notify(t.user, "ticket_status", f"Ticket {status.replace('_', ' ')}", t.subject, ticket_id=t.id)
        return Response(TicketSerializer(t).data)
