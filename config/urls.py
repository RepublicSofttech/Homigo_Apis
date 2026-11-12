from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

api_v1 = [
    path("", include("apps.core.urls")),
    path("", include("apps.accounts.urls")),
    path("", include("apps.catalogue.urls")),
    path("", include("apps.bookings.urls")),
    path("", include("apps.partners.urls")),
    path("", include("apps.subscriptions.urls")),
    path("", include("apps.payments.urls")),
    path("", include("apps.offers.urls")),
    path("", include("apps.reviews.urls")),
    path("", include("apps.support.urls")),
    path("", include("apps.notifications.urls")),
]

urlpatterns = [
    path("django-admin/", admin.site.urls),
    path("api/v1/", include(api_v1)),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger"),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
