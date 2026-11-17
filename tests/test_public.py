from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIClient


class PublicEndpointsTest(TestCase):
    def setUp(self):
        cache.clear()
        self.c = APIClient()

    def test_stats_public_and_shape(self):
        r = self.c.get("/api/v1/public/stats/")
        self.assertEqual(r.status_code, 200)
        for k in ["bookings_completed", "verified_partners", "verified_professionals", "cities", "services", "average_rating", "reviews_count"]:
            self.assertIn(k, r.data)

    def test_reviews_public_empty(self):
        r = self.c.get("/api/v1/public/reviews/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data, [])


class TestimonialsTest(TestCase):
    def setUp(self):
        cache.clear()
        from apps.accounts.models import User

        User.objects.create_superuser(phone="9111111111", password="Admin@12345")
        self.c = APIClient()

    def _admin_client(self):
        c = APIClient()
        r = c.post("/api/v1/auth/password-login/", {"phone": "9111111111", "password": "Admin@12345"}, format="json")
        c.credentials(HTTP_AUTHORIZATION=f"Token {r.data['token']}")
        return c

    def test_publish_requires_consent_and_public_lists_only_published(self):
        a = self._admin_client()
        r = a.post("/api/v1/admin/testimonials/", {"name": "Priya S.", "quote": "Great!", "is_published": True}, format="json")
        self.assertEqual(r.status_code, 400)
        r = a.post("/api/v1/admin/testimonials/", {"name": "Priya S.", "location": "Delhi", "role": "Deep cleaning", "quote": "Great!", "consent": True, "is_published": True}, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        a.post("/api/v1/admin/testimonials/", {"name": "Hidden", "quote": "Draft", "consent": True}, format="json")
        a.post("/api/v1/admin/testimonials/", {"name": "Ramesh", "quote": "More jobs", "consent": True, "is_published": True, "audience": "partner"}, format="json")
        cust = self.c.get("/api/v1/public/testimonials/").data
        self.assertEqual([t["name"] for t in cust], ["Priya S."])
        part = self.c.get("/api/v1/public/testimonials/?audience=partner").data
        self.assertEqual([t["name"] for t in part], ["Ramesh"])
        self.assertEqual(self.c.post("/api/v1/admin/testimonials/", {"name": "x", "quote": "y"}, format="json").status_code, 401)
