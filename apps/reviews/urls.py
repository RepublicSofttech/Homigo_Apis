from django.urls import path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("reviews", views.CustomerReviewViewSet, basename="review")
router.register("partner/reviews", views.PartnerReviewViewSet, basename="partner-review")
router.register("admin/reviews", views.AdminReviewViewSet, basename="admin-review")
router.register("admin/testimonials", views.AdminTestimonialViewSet, basename="admin-testimonial")
urlpatterns = [
    path("partners/<int:pk>/reviews/", views.PartnerPublicReviews.as_view()),
    path("public/reviews/", views.FeaturedReviews.as_view()),
    path("public/testimonials/", views.PublicTestimonials.as_view()),
] + router.urls
