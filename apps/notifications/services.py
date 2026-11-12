"""Single place to raise notifications. Push/SMS/WhatsApp providers plug in here later."""
import logging

from .models import Notification

log = logging.getLogger(__name__)


def notify(user, event, title, message, **data):
    if user is None:
        return None
    n = Notification.objects.create(user=user, event=event, title=title, message=message, data=data)
    log.info("notify %s [%s] %s", user.pk, event, title)
    return n
