from decimal import ROUND_HALF_UP, Decimal

from django.utils import timezone

from apps.core.exceptions import BusinessError

from .models import Offer

TWO = Decimal("0.01")


def compute_discount(code, user, service, partner, order_value):
    """Returns (offer, discount, partner_share). Raises COUPON_NOT_APPLICABLE when rules fail."""
    now = timezone.now()
    offer = Offer.objects.filter(code__iexact=code.strip(), is_active=True).first()

    def fail(msg):
        raise BusinessError("COUPON_NOT_APPLICABLE", msg)

    if not offer:
        fail("Invalid coupon code.")
    if not (offer.valid_from <= now <= offer.valid_to):
        fail("This coupon has expired or is not active yet.")
    if offer.usage_limit and offer.used_count >= offer.usage_limit:
        fail("This coupon has reached its usage limit.")
    if offer.service_id and offer.service_id != service.id:
        fail(f"This coupon is valid only for {offer.service.name}.")
    if offer.category_id and offer.category_id != service.category_id:
        fail("This coupon is not valid for this service.")
    if offer.partner_id and partner and offer.partner_id != partner.id:
        fail("This coupon is not valid for this provider.")
    if order_value < offer.min_order_value:
        fail(f"Minimum order value is ₹{offer.min_order_value}.")
    if user and user.is_authenticated:
        used = offer.redemptions.filter(user=user).count()
        if used >= offer.per_user_limit:
            fail("You have already used this coupon.")
        if offer.first_booking_only:
            from apps.bookings.models import Booking
            if Booking.objects.filter(customer=user, status="completed").exists():
                fail("This coupon is valid on your first booking only.")
    if offer.discount_type == Offer.DiscountType.FLAT:
        discount = offer.value
    else:
        discount = order_value * offer.value / Decimal(100)
        if offer.max_discount:
            discount = min(discount, offer.max_discount)
    discount = min(discount, order_value).quantize(TWO, ROUND_HALF_UP)
    if offer.funded_by == Offer.FundedBy.PARTNER:
        partner_share = discount
    elif offer.funded_by == Offer.FundedBy.SHARED:
        partner_share = (discount * offer.partner_share_percent / Decimal(100)).quantize(TWO, ROUND_HALF_UP)
    else:
        partner_share = Decimal("0")
    return offer, discount, partner_share
