"""Payment gateway adapter. 'mock' for development, 'razorpay' for production."""
import base64
import hashlib
import hmac
import json
import urllib.request

from django.conf import settings

from apps.core.exceptions import BusinessError


def create_order(payment):
    if settings.PAYMENT_GATEWAY == "mock":
        return f"mock_order_{payment.pk}"
    if settings.PAYMENT_GATEWAY == "razorpay":
        body = json.dumps({"amount": int(payment.amount * 100), "currency": "INR", "receipt": f"pay_{payment.pk}"}).encode()
        auth = base64.b64encode(f"{settings.RAZORPAY_KEY_ID}:{settings.RAZORPAY_KEY_SECRET}".encode()).decode()
        req = urllib.request.Request("https://api.razorpay.com/v1/orders", data=body,
                                     headers={"Authorization": f"Basic {auth}", "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as r:  # noqa: S310 - fixed https URL
            return json.loads(r.read())["id"]
    raise BusinessError("GATEWAY_NOT_CONFIGURED", "Payment gateway is not configured.", 503)


def verify_checkout_signature(order_id, payment_id, signature):
    msg = f"{order_id}|{payment_id}".encode()
    expected = hmac.new(settings.RAZORPAY_KEY_SECRET.encode(), msg, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature or "")


def verify_webhook_signature(body: bytes, signature):
    if not settings.RAZORPAY_WEBHOOK_SECRET:
        return False
    expected = hmac.new(settings.RAZORPAY_WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature or "")
