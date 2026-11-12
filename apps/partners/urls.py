from django.urls import path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("partner/services", views.PartnerServiceViewSet, basename="partner-service")
router.register("partner/documents", views.PartnerDocumentViewSet, basename="partner-document")
router.register("partner/rate-card", views.RateCardViewSet, basename="partner-rate-card")
router.register("partner/coworkers", views.CoworkerViewSet, basename="partner-coworker")
router.register("partner/coworker-documents", views.CoworkerDocumentViewSet, basename="partner-coworker-document")
router.register("admin/partners", views.AdminPartnerViewSet, basename="admin-partner")
router.register("admin/coworkers", views.AdminCoworkerViewSet, basename="admin-coworker")
router.register("admin/verification-cases", views.VerificationCaseViewSet, basename="admin-verification")

urlpatterns = [
    path("partner/profile/", views.PartnerProfileView.as_view()),
    path("partner/go-live-status/", views.GoLiveStatusView.as_view()),
    path("partner/submit-verification/", views.SubmitVerificationView.as_view()),
    path("partner/bank-account/", views.BankAccountView.as_view()),
    path("partners/<int:pk>/", views.PublicPartnerView.as_view()),
    path("partners/<int:pk>/coworkers/", views.PublicPartnerCoworkersView.as_view()),
] + router.urls
