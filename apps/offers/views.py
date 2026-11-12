from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import generics, permissions, viewsets
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.catalogue.models import Service
from apps.core.exceptions import BusinessError
from apps.core.permissions import IsPartner, IsPlatformAdmin
from apps.partners.models import AreaPartner
from apps.partners.views import get_partner

from .models import Offer
from .serializers import CouponValidateSerializer, OfferSerializer, PartnerOfferSerializer
from .services import compute_discount


class PublicOfferListView(generics.ListAPIView):
    permission_classes = [permissions.AllowAny]
    serializer_class = OfferSerializer
    pagination_class = None

    def get_queryset(self):
        now = timezone.now()
        qs = Offer.objects.filter(is_active=True, valid_from__lte=now, valid_to__gte=now)
        svc = self.request.query_params.get("service_id")
        if svc:
            service = Service.objects.filter(pk=svc).first()
            qs = qs.filter(Q(service_id=svc) | Q(service__isnull=True, category__isnull=True)
                           | Q(category_id=getattr(service, "category_id", None)))
        return qs


class CouponValidateView(APIView):
    serializer_class = CouponValidateSerializer
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        s = CouponValidateSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        d = s.validated_data
        service = get_object_or_404(Service, pk=d["service_id"])
        partner = AreaPartner.objects.filter(pk=d.get("partner_id")).first()
        offer, discount, _ = compute_discount(d["code"], request.user, service, partner, d["order_value"])
        return Response({"valid": True, "code": offer.code, "title": offer.title, "discount": discount})


class AdminOfferViewSet(viewsets.ModelViewSet):
    permission_classes = [IsPlatformAdmin]
    serializer_class = OfferSerializer
    queryset = Offer.objects.all()
    filterset_fields = ["is_active", "funded_by", "partner"]
    search_fields = ["code", "title"]


class PartnerOfferViewSet(viewsets.ModelViewSet):
    permission_classes = [IsPartner]
    serializer_class = PartnerOfferSerializer
    pagination_class = None

    def get_queryset(self):
        return Offer.objects.filter(partner=get_partner(self.request))

    def perform_create(self, serializer):
        partner = get_partner(self.request)
        sub = partner.active_subscription()
        if not sub:
            raise BusinessError("SUBSCRIPTION_INACTIVE", "Active subscription required to create offers.", 403)
        serializer.save(partner=partner, funded_by="partner")
