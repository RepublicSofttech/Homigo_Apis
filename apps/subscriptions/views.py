from rest_framework import mixins, permissions, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.exceptions import BusinessError
from apps.core.permissions import IsAdminOrReadOnly, IsPartner, IsPlatformAdmin
from apps.partners.views import get_partner

from .models import PartnerSubscription, SubscriptionPlan
from .serializers import PartnerSubscriptionSerializer, PlanSerializer


class PlanViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAdminOrReadOnly]
    serializer_class = PlanSerializer
    pagination_class = None

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [permissions.AllowAny()]
        return super().get_permissions()

    def get_queryset(self):
        u = self.request.user
        qs = SubscriptionPlan.objects.all()
        if u.is_authenticated and (u.user_type == "admin" or u.is_superuser):
            return qs
        return qs.filter(is_active=True)


class PartnerSubscriptionViewSet(mixins.ListModelMixin, mixins.CreateModelMixin, viewsets.GenericViewSet):
    """Partner subscribes -> pending_payment; payment success (payments app) activates it."""

    permission_classes = [IsPartner]
    serializer_class = PartnerSubscriptionSerializer
    pagination_class = None

    def get_queryset(self):
        return PartnerSubscription.objects.filter(partner=get_partner(self.request)).select_related("plan")

    def perform_create(self, serializer):
        partner = get_partner(self.request)
        plan = serializer.validated_data["plan"]
        if not plan.is_active:
            raise BusinessError("PLAN_INACTIVE", "This plan is not available.")
        PartnerSubscription.objects.filter(partner=partner, status="pending_payment").delete()
        serializer.save(partner=partner, amount=plan.price)

    @action(detail=False, methods=["get"])
    def current(self, request):
        sub = PartnerSubscription.current_for(get_partner(request))
        return Response(PartnerSubscriptionSerializer(sub).data if sub else None)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        sub = self.get_object()
        if sub.status != "active":
            raise BusinessError("NOT_ACTIVE", "Only active subscriptions can be cancelled.")
        sub.status, sub.auto_renew = "cancelled", False
        sub.save(update_fields=["status", "auto_renew"])
        return Response(PartnerSubscriptionSerializer(sub).data)


class AdminSubscriptionViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    permission_classes = [IsPlatformAdmin]
    serializer_class = PartnerSubscriptionSerializer
    queryset = PartnerSubscription.objects.select_related("plan", "partner").all()
    filterset_fields = ["status", "plan", "partner"]

    @action(detail=True, methods=["post"], url_path="grant")
    def grant(self, request, pk=None):
        """Admin can activate a subscription without payment (trial / offline payment)."""
        sub = self.get_object()
        sub.activate()
        return Response(PartnerSubscriptionSerializer(sub).data)


class SubscriptionStatusView(APIView):
    permission_classes = [IsPartner]

    def get(self, request):
        sub = PartnerSubscription.current_for(get_partner(request))
        return Response({"active": bool(sub), "subscription": PartnerSubscriptionSerializer(sub).data if sub else None})
