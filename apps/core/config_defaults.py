"""Default values for admin-configurable settings (BRD section 18)."""
from decimal import Decimal

DEFAULTS = {
    "default_service_radius_km": 5,
    "fallback_radius_km": 8,
    "convenience_fee": 29,
    "gst_percent": 18,
    "default_commission_percent": 20,
    "acceptance_sla_minutes_instant": 10,
    "acceptance_sla_minutes_other": 60,
    "slot_minutes": 60,
    "working_hours_start": "08:00",
    "working_hours_end": "20:00",
    "free_cancel_hours": 4,
    "late_cancel_fee": 99,
    "subscription_grace_days": 7,
    "cash_limit": 5000,
    "dispute_window_hours": 48,
    "review_window_days": 7,
    "min_coworkers_default": 1,
}


def get_setting(key):
    from .models import AppSetting

    obj = AppSetting.objects.filter(key=key).first()
    value = obj.value if obj else DEFAULTS.get(key)
    if isinstance(value, float):
        return Decimal(str(value))
    return value
