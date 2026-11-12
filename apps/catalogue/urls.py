from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("categories", views.CategoryViewSet, basename="category")
router.register("services", views.ServiceViewSet, basename="service")
router.register("service-items", views.ServiceItemViewSet, basename="service-item")
urlpatterns = router.urls
