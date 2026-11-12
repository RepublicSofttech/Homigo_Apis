from django.urls import path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("admin/settings", views.AppSettingViewSet, basename="admin-setting")
router.register("admin/audit-logs", views.AuditLogViewSet, basename="admin-audit")
urlpatterns = [path("health/", views.HealthView.as_view())] + router.urls
