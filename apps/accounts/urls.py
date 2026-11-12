from django.urls import path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("addresses", views.AddressViewSet, basename="address")
router.register("admin/users", views.AdminUserViewSet, basename="admin-user")

urlpatterns = [
    path("auth/otp/send/", views.OTPSendView.as_view()),
    path("auth/otp/verify/", views.OTPVerifyView.as_view()),
    path("auth/password-login/", views.PasswordLoginView.as_view()),
    path("auth/logout/", views.LogoutView.as_view()),
    path("me/", views.MeView.as_view()),
    path("me/avatar/", views.AvatarView.as_view()),
] + router.urls
