from django.urls import path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("bookings", views.BookingViewSet, basename="booking")
router.register("partner/job-requests", views.JobRequestViewSet, basename="partner-job-request")
urlpatterns = [
    path("services/<int:pk>/availability/", views.AvailabilityView.as_view()),
    path("slots/", views.SlotsView.as_view()),
    path("bookings/price-preview/", views.PricePreviewView.as_view()),
    path("coworker/jobs/", views.CoworkerJobsView.as_view()),
    path("partner/dashboard/", views.PartnerDashboardView.as_view()),
    path("admin/dashboard/", views.AdminDashboardView.as_view()),
] + router.urls
