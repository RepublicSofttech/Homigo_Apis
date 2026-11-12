from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("plans", views.PlanViewSet, basename="plan")
router.register("partner/subscriptions", views.PartnerSubscriptionViewSet, basename="partner-subscription")
router.register("admin/subscriptions", views.AdminSubscriptionViewSet, basename="admin-subscription")
urlpatterns = router.urls
