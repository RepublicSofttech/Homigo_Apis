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
