from django.urls import path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("admin/offers", views.AdminOfferViewSet, basename="admin-offer")
router.register("partner/offers", views.PartnerOfferViewSet, basename="partner-offer")
urlpatterns = [
    path("offers/", views.PublicOfferListView.as_view()),
    path("coupons/validate/", views.CouponValidateView.as_view()),
] + router.urls
