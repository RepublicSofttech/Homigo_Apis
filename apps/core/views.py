from rest_framework import mixins, permissions, viewsets
from rest_framework.response import Response
from rest_framework.views import APIView

from .config_defaults import DEFAULTS
from .models import AppSetting, AuditLog
from .permissions import IsPlatformAdmin
from .serializers import AppSettingSerializer, AuditLogSerializer


class HealthView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def get(self, request):
        return Response({"status": "ok", "service": "homigo-api"})


class AppSettingViewSet(viewsets.ModelViewSet):
    permission_classes = [IsPlatformAdmin]
    serializer_class = AppSettingSerializer
    queryset = AppSetting.objects.all().order_by("key")
    pagination_class = None
    lookup_field = "key"

    def list(self, request, *args, **kwargs):
        stored = {s.key: s for s in self.get_queryset()}
        data = []
        for key, default in DEFAULTS.items():
            s = stored.get(key)
            data.append({"key": key, "value": s.value if s else default, "default": default,
                         "description": s.description if s else ""})
        return Response(data)

    def update(self, request, *args, **kwargs):
        key = kwargs["key"]
        obj, _ = AppSetting.objects.update_or_create(key=key, defaults={"value": request.data.get("value")})
        AuditLog.objects.create(actor=request.user, action="config_change", entity="AppSetting", entity_id=key,
                                data={"value": obj.value})
        return Response(AppSettingSerializer(obj).data)


class AuditLogViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    permission_classes = [IsPlatformAdmin]
    serializer_class = AuditLogSerializer
    queryset = AuditLog.objects.select_related("actor").all()
    filterset_fields = ["action", "entity"]


class PublicStatsView(APIView):
    """Real platform numbers for the public website (cached 10 min)."""

    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def get(self, request):
        from django.core.cache import cache

        data = cache.get("public_stats")
        if data is None:
            from django.db.models import Avg, Count

            from apps.bookings.models import Booking, BookingStatus
            from apps.catalogue.models import Service
            from apps.partners.models import AreaPartner, Coworker, VerificationStatus
            from apps.reviews.models import Review

            partners = AreaPartner.objects.filter(
                verification_status=VerificationStatus.APPROVED, account_status=AreaPartner.AccountStatus.ACTIVE
            )
            reviews = Review.objects.filter(is_published=True).aggregate(avg=Avg("rating_partner"), n=Count("id"))
            data = {
                "bookings_completed": Booking.objects.filter(status=BookingStatus.COMPLETED).count(),
                "verified_partners": partners.count(),
                "verified_professionals": Coworker.objects.filter(
                    verification_status=VerificationStatus.APPROVED, status=Coworker.Status.ACTIVE
                ).count(),
                "cities": partners.values("city").distinct().count(),
                "services": Service.objects.filter(is_active=True).count(),
                "average_rating": round(float(reviews["avg"]), 1) if reviews["avg"] else None,
                "reviews_count": reviews["n"],
            }
            cache.set("public_stats", data, 600)
        return Response(data)
