"""Run every minute via cron / scheduler:
  - partner acceptance SLA breach -> fallback partner or cancel (BR-B4)
  - payment_pending bookings older than 15 min -> cancelled
  - subscriptions past grace -> expired
  - coworker documents expired -> coworker suspended from new jobs (BR-C4)
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.bookings.models import Booking
from apps.bookings.services import cancel, reject_or_fallback
from apps.core.config_defaults import get_setting
from apps.notifications.services import notify
from apps.partners.models import Coworker
from apps.subscriptions.models import PartnerSubscription


class Command(BaseCommand):
    help = "Process SLA timers, expiries and auto-blocks"

    def handle(self, *args, **opts):
        now = timezone.now()
        n = 0
        for b in Booking.objects.filter(status="pending_acceptance", sla_due_at__lt=now):
            reject_or_fallback(b, None, "Partner did not accept within SLA", auto=True)
            n += 1
        for b in Booking.objects.filter(status="payment_pending", created_at__lt=now - timedelta(minutes=15)):
            cancel(b, None, "system", "Payment not completed", waive_fee=True)
            n += 1
        grace = int(get_setting("subscription_grace_days"))
        expired = PartnerSubscription.objects.filter(status__in=["active", "cancelled"],
                                                     end_date__lt=timezone.localdate() - timedelta(days=grace))
        expired_count = 0
        for sub in expired:
            expired_count += 1
            sub.status = "expired"
            sub.save(update_fields=["status"])
            notify(sub.partner.owner, "subscription_expired", "Subscription expired",
                   "Renew now to keep receiving bookings.")
        today = timezone.localdate()
        for c in Coworker.objects.filter(status="active", documents__expiry_date__lt=today).distinct():
            c.status = "inactive"
            c.save(update_fields=["status"])
            notify(c.partner.owner, "document_expired", f"{c.name}: document expired",
                   "Coworker blocked from new jobs until the document is renewed.")
        self.stdout.write(f"bookings processed: {n}, subscriptions expired: {expired_count}")
