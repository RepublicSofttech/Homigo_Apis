from django.urls import path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("payments", views.MyPaymentsView, basename="payment")
router.register("admin/payments", views.AdminPaymentViewSet, basename="admin-payment")
router.register("admin/settlements", views.AdminSettlementViewSet, basename="admin-settlement")
router.register("admin/commissions", views.AdminCommissionViewSet, basename="admin-commission")
urlpatterns = [
    path("payments/initiate/", views.InitiatePaymentView.as_view()),
    path("payments/verify/", views.VerifyPaymentView.as_view()),
    path("payments/webhook/razorpay/", views.RazorpayWebhookView.as_view()),
    path("partner/earnings/", views.PartnerEarningsView.as_view()),
] + router.urls
