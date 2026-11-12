from django.db.models import Q
from rest_framework import viewsets

from apps.core.permissions import IsAdminOrReadOnly

from .models import Service, ServiceCategory, ServiceItem
from .serializers import (
    ServiceCategorySerializer, ServiceDetailSerializer, ServiceItemSerializer, ServiceSerializer,
)


def is_admin(request):
    u = request.user
    return u.is_authenticated and (u.user_type == "admin" or u.is_superuser)


class CategoryViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAdminOrReadOnly]
    serializer_class = ServiceCategorySerializer
    pagination_class = None
    search_fields = ["name"]

    def get_queryset(self):
        qs = ServiceCategory.objects.all()
        return qs if is_admin(self.request) else qs.filter(is_active=True)


class ServiceViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAdminOrReadOnly]
    filterset_fields = ["category", "category__slug", "is_active"]

    def get_serializer_class(self):
        return ServiceDetailSerializer if self.action == "retrieve" else ServiceSerializer

    def get_queryset(self):
        qs = Service.objects.select_related("category").prefetch_related("items")
        if not is_admin(self.request):
            qs = qs.filter(is_active=True, category__is_active=True)
        q = self.request.query_params.get("q")
        if q:
            qs = qs.filter(Q(name__icontains=q) | Q(synonyms__icontains=q) | Q(category__name__icontains=q)
                           | Q(items__name__icontains=q)).distinct()
        return qs


class ServiceItemViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAdminOrReadOnly]
    serializer_class = ServiceItemSerializer
    filterset_fields = ["service", "pricing_model", "is_active"]
    pagination_class = None

    def get_queryset(self):
        qs = ServiceItem.objects.select_related("service")
        return qs if is_admin(self.request) else qs.filter(is_active=True)
