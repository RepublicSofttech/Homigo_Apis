import logging
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from apps.core.exceptions import BusinessError
from apps.core.utils import random_otp, sha256

from .models import OTPRequest

log = logging.getLogger(__name__)


def send_sms(phone, text):
    """SMS provider hook. Replace 'console' with a real provider (MSG91 / Twilio) in production."""
    if settings.SMS_BACKEND == "console":
        log.info("SMS to %s: %s", phone, text)
        return
    raise NotImplementedError(f"SMS backend {settings.SMS_BACKEND} not configured")


def issue_otp(phone):
    code = random_otp(4)
    OTPRequest.objects.filter(phone=phone, used=False).update(used=True)
    OTPRequest.objects.create(
        phone=phone, code_hash=sha256(code),
        expires_at=timezone.now() + timedelta(seconds=settings.OTP_TTL_SECONDS),
    )
    send_sms(phone, f"Your HomeServ login OTP is {code}. Valid for 5 minutes.")
    return code


def check_otp(phone, code):
    otp = OTPRequest.objects.filter(phone=phone, used=False).order_by("-created_at").first()
    if not otp or not otp.is_valid:
        raise BusinessError("OTP_EXPIRED", "OTP expired. Please request a new one.")
    if otp.attempts >= settings.OTP_MAX_ATTEMPTS:
        raise BusinessError("OTP_LOCKED", "Too many wrong attempts. Request a new OTP.")
    if otp.code_hash != sha256(code):
        otp.attempts += 1
        otp.save(update_fields=["attempts"])
        raise BusinessError("INVALID_OTP", "Invalid OTP.")
    otp.used = True
    otp.save(update_fields=["used"])
