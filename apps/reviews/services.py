from django.db.models import Avg, Count


def refresh_ratings(partner, coworker=None):
    from .models import Review

    agg = Review.objects.filter(partner=partner, is_published=True).aggregate(a=Avg("rating_partner"), c=Count("id"))
    partner.rating_avg, partner.rating_count = round(agg["a"] or 0, 2), agg["c"]
    partner.save(update_fields=["rating_avg", "rating_count"])
    if coworker:
        agg = Review.objects.filter(coworker=coworker, is_published=True, rating_coworker__isnull=False).aggregate(
            a=Avg("rating_coworker"), c=Count("id"))
        coworker.rating_avg, coworker.rating_count = round(agg["a"] or 0, 2), agg["c"]
        coworker.save(update_fields=["rating_avg", "rating_count"])
