import hashlib
import math
import random

from .models import AuditLog


def haversine_km(lat1, lng1, lat2, lng2):
    """Great-circle distance in km between two lat/lng points."""
    lat1, lng1, lat2, lng2 = map(lambda v: math.radians(float(v)), (lat1, lng1, lat2, lng2))
    dlat, dlng = lat2 - lat1, lng2 - lng1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(a))


def bounding_box(lat, lng, radius_km):
    """Cheap pre-filter box so we only run haversine on nearby rows."""
    dlat = radius_km / 111.0
    dlng = radius_km / (111.0 * max(math.cos(math.radians(float(lat))), 0.01))
    return float(lat) - dlat, float(lat) + dlat, float(lng) - dlng, float(lng) + dlng


def mask_number(value, keep=4):
    value = (value or "").strip()
    if not value:
        return ""
    return "X" * max(len(value) - keep, 0) + value[-keep:]


def sha256(value):
    return hashlib.sha256((value or "").encode()).hexdigest()


def random_otp(length=4):
    return "".join(random.SystemRandom().choice("0123456789") for _ in range(length))


def audit(actor, action, obj, **data):
    AuditLog.objects.create(
        actor=actor if getattr(actor, "is_authenticated", False) else None,
        action=action,
        entity=obj.__class__.__name__,
        entity_id=str(obj.pk),
        data=data,
    )
