"""Booking domain logic: discovery, slots, pricing, state machine, cancellation and fallback."""
from datetime import datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.utils import timezone

from apps.catalogue.models import ServiceItem
from apps.core.config_defaults import get_setting
from apps.core.exceptions import BusinessError
from apps.core.utils import audit, bounding_box, haversine_km, random_otp
from apps.notifications.services import notify
from apps.partners.models import AreaPartner, Coworker

from .models import OPEN_STATUSES, Booking, BookingAssignment, BookingItem, BookingStatus, BookingStatusHistory

TWO = Decimal("0.01")
S = BookingStatus


def money(v):
    return Decimal(v).quantize(TWO, ROUND_HALF_UP)


# ---------------- Discovery ----------------
def find_partners(service, lat, lng, exclude_ids=(), extra_radius_km=0, limit=10):
    """Nearest eligible partners for a service. Eligibility = BR-P1 go-live gate + within service radius."""
    max_r = float(get_setting("fallback_radius_km")) + 50
    lat_min, lat_max, lng_min, lng_max = bounding_box(lat, lng, max_r)
    candidates = (
        AreaPartner.objects.filter(
            partnerservice__service=service, partnerservice__is_active=True,
            verification_status="approved", account_status="active",
            latitude__range=(lat_min, lat_max), longitude__range=(lng_min, lng_max),
        ).exclude(id__in=list(exclude_ids)).select_related("bank_account")
    )
    results = []
    for p in candidates:
        d = haversine_km(lat, lng, p.latitude, p.longitude)
        if d > float(p.service_radius_km) + extra_radius_km:
            continue
        _, live = p.go_live_checklist(service)
        if live:
            results.append((p, round(d, 2)))
    results.sort(key=lambda r: (r[1], -float(r[0].rating_avg)))
    return results[:limit]


def eligible_coworkers(partner, service, start=None, end=None):
    qs = Coworker.objects.filter(partner=partner, verification_status="approved", status="active", skills=service)
    if start and end:
        busy = BookingAssignment.objects.filter(
            is_active=True, booking__status__in=OPEN_STATUSES,
            booking__scheduled_start__lt=end, booking__scheduled_end__gt=start,
        ).values_list("coworker_id", flat=True)
        qs = qs.exclude(id__in=busy)
    return qs


def slot_capacity(partner, service, start, end):
    capacity = Coworker.objects.filter(partner=partner, verification_status="approved", status="active", skills=service).count()
    booked = Booking.objects.filter(partner=partner, status__in=OPEN_STATUSES,
                                    scheduled_start__lt=end, scheduled_end__gt=start).count()
    return max(capacity - booked, 0)


def get_slots(partner, service, day, duration_minutes=60):
    tz = timezone.get_current_timezone()
    step = int(get_setting("slot_minutes"))
    start_dt = timezone.make_aware(datetime.combine(day, partner.working_hours_start or time(8)), tz)
    end_dt = timezone.make_aware(datetime.combine(day, partner.working_hours_end or time(20)), tz)
    earliest = timezone.now() + timedelta(hours=1)
    slots, cur = [], start_dt
    while cur + timedelta(minutes=duration_minutes) <= end_dt:
        slot_end = cur + timedelta(minutes=duration_minutes)
        free = slot_capacity(partner, service, cur, slot_end) if cur >= earliest else 0
        slots.append({"start": cur.isoformat(), "end": slot_end.isoformat(), "available": free > 0, "capacity": free})
        cur += timedelta(minutes=step)
    return slots


# ---------------- Pricing ----------------
def price_items(partner, service, items):
    """items: [{'service_item': id, 'quantity': n}] -> list of dict lines using partner rate card."""
    if not items:
        raise BusinessError("NO_ITEMS", "Select at least one item.")
    lines = []
    rates = {r.service_item_id: r.price for r in partner.rate_card.filter(is_active=True, service_item__service=service)}
    for it in items:
        item = ServiceItem.objects.filter(pk=it["service_item"], service=service, is_active=True).first()
        if not item:
            raise BusinessError("INVALID_ITEM", "Selected item is not part of this service.")
        if item.id not in rates:
            raise BusinessError("ITEM_NOT_OFFERED", f"'{item.name}' is not offered by this provider.")
        qty = max(int(it.get("quantity", 1)), item.min_qty)
        price = rates[item.id]
        lines.append({"service_item": item, "name": item.name, "quantity": qty, "unit_price": price,
                      "total": money(price * qty), "duration": item.duration_minutes * qty})
    return lines


def build_quote(user, partner, service, items, coupon_code=""):
    from apps.offers.services import compute_discount

    lines = price_items(partner, service, items)
    subtotal = sum((l["total"] for l in lines), Decimal("0"))
    visit = money(service.visit_charge)
    discount = partner_share = Decimal("0")
    offer = None
    if coupon_code:
        offer, discount, partner_share = compute_discount(coupon_code, user, service, partner, subtotal + visit)
    fee = money(get_setting("convenience_fee"))
    tax = money(fee * Decimal(str(get_setting("gst_percent"))) / 100)  # GST on platform fee; service GST per CA advice
    total = money(subtotal + visit - discount + fee + tax)
    return {
        "lines": lines, "subtotal": money(subtotal), "visit_charge": visit, "discount": money(discount),
        "partner_discount_share": money(partner_share), "convenience_fee": fee, "tax": tax, "total": total,
        "duration_minutes": max(sum(l["duration"] for l in lines), 30), "offer": offer,
    }


# ---------------- State machine (BR-B1) ----------------
TRANSITIONS = {
    S.PAYMENT_PENDING: {S.PENDING_ACCEPTANCE, S.CANCELLED},
    S.PENDING_ACCEPTANCE: {S.CONFIRMED, S.CANCELLED, S.PENDING_ACCEPTANCE},
    S.CONFIRMED: {S.ASSIGNED, S.CANCELLED},
    S.ASSIGNED: {S.ASSIGNED, S.ON_THE_WAY, S.CANCELLED},
    S.ON_THE_WAY: {S.ARRIVED, S.CANCELLED},
    S.ARRIVED: {S.IN_PROGRESS, S.NO_SHOW, S.CANCELLED},
    S.IN_PROGRESS: {S.COMPLETION_REQUESTED},
    S.COMPLETION_REQUESTED: {S.COMPLETED, S.IN_PROGRESS},
    S.COMPLETED: {S.DISPUTED},
}


def transition(booking, to_status, actor=None, note=""):
    allowed = TRANSITIONS.get(booking.status, set())
    if to_status not in allowed:
        raise BusinessError("INVALID_STATUS_TRANSITION",
                            f"Cannot move booking from {booking.get_status_display()} to {S(to_status).label}.")
    BookingStatusHistory.objects.create(booking=booking, from_status=booking.status, to_status=to_status,
                                        actor=actor if getattr(actor, "is_authenticated", False) else None, note=note[:255])
    booking.status = to_status
    booking.save()
    return booking


def sla_minutes(booking):
    key = "acceptance_sla_minutes_instant" if booking.booking_type == "instant" else "acceptance_sla_minutes_other"
    return int(get_setting(key))


@transaction.atomic
def create_booking(user, service, partner, address, items, scheduled_start, booking_type, payment_mode,
                   notes="", answers=None, coupon_code="", distance_km=0):
    # Lock partner row so two checkouts can't take the last slot at the same time.
    AreaPartner.objects.select_for_update().get(pk=partner.pk)
    if payment_mode == "cash" and not partner.accepts_cash:
        raise BusinessError("CASH_NOT_ALLOWED", "This provider accepts online payment only.")
    quote = build_quote(user, partner, service, items, coupon_code)
    end = scheduled_start + timedelta(minutes=quote["duration_minutes"])
    if scheduled_start < timezone.now() - timedelta(minutes=5):
        raise BusinessError("SLOT_IN_PAST", "Selected time is in the past.")
    if booking_type != "instant" and slot_capacity(partner, service, scheduled_start, end) <= 0:
        raise BusinessError("SLOT_UNAVAILABLE", "This slot was just taken. Please pick another time.", 409)
    status = S.PAYMENT_PENDING if payment_mode == "online" else S.PENDING_ACCEPTANCE
    booking = Booking.objects.create(
        customer=user, partner=partner, service=service, booking_type=booking_type,
        address=address.snapshot(), latitude=address.latitude, longitude=address.longitude, distance_km=distance_km,
        scheduled_start=scheduled_start, scheduled_end=end, notes=notes, answers=answers or {},
        status=status, payment_mode=payment_mode, subtotal=quote["subtotal"], visit_charge=quote["visit_charge"],
        discount=quote["discount"], partner_discount_share=quote["partner_discount_share"],
        convenience_fee=quote["convenience_fee"], tax=quote["tax"], total=quote["total"],
        coupon_code=quote["offer"].code if quote["offer"] else "", start_otp=random_otp(4), end_otp=random_otp(4),
    )
    for l in quote["lines"]:
        BookingItem.objects.create(booking=booking, service_item=l["service_item"], name=l["name"],
                                   quantity=l["quantity"], unit_price=l["unit_price"], total=l["total"])
    if quote["offer"]:
        from apps.offers.models import Redemption
        Redemption.objects.create(offer=quote["offer"], user=user, booking=booking, amount=quote["discount"])
        type(quote["offer"]).objects.filter(pk=quote["offer"].pk).update(used_count=quote["offer"].used_count + 1)
    BookingStatusHistory.objects.create(booking=booking, to_status=status, actor=user, note="Booking created")
    if status == S.PENDING_ACCEPTANCE:
        send_to_partner(booking)
    return booking


def send_to_partner(booking):
    booking.sla_due_at = timezone.now() + timedelta(minutes=sla_minutes(booking))
    booking.save(update_fields=["sla_due_at"])
    notify(booking.partner.owner, "booking_new", f"New booking {booking.booking_no}",
           f"{booking.service.name} on {timezone.localtime(booking.scheduled_start):%d %b, %I:%M %p}. Accept within SLA.",
           booking_id=booking.id)


def on_payment_success(booking):
    if booking.status == S.PAYMENT_PENDING:
        transition(booking, S.PENDING_ACCEPTANCE, note="Payment received")
        send_to_partner(booking)


def accept(booking, actor):
    transition(booking, S.CONFIRMED, actor, "Accepted by partner")
    booking.accepted_at = timezone.now()
    booking.save(update_fields=["accepted_at"])
    notify(booking.customer, "booking_confirmed", "Booking confirmed",
           f"{booking.partner.display_name} accepted your booking {booking.booking_no}.", booking_id=booking.id)


@transaction.atomic
def reject_or_fallback(booking, actor, reason="", auto=False):
    """BR-B4: rejected / SLA missed -> nearest other partner (fallback radius) -> else cancel + refund."""
    if booking.status != S.PENDING_ACCEPTANCE:
        raise BusinessError("INVALID_STATUS_TRANSITION", "Only pending bookings can be rejected.")
    rejected = set(booking.rejected_partner_ids) | {booking.partner_id}
    booking.rejected_partner_ids = list(rejected)
    extra = float(get_setting("fallback_radius_km")) - float(get_setting("default_service_radius_km"))
    options = find_partners(booking.service, booking.latitude, booking.longitude, exclude_ids=rejected,
                            extra_radius_km=max(extra, 0), limit=1)
    old_partner = booking.partner
    if options:
        new_partner, dist = options[0]
        booking.partner, booking.distance_km = new_partner, dist
        booking.fallback_count += 1
        booking.save()
        transition(booking, S.PENDING_ACCEPTANCE, actor, f"Moved from {old_partner} to {new_partner}: {reason or 'not accepted'}")
        send_to_partner(booking)
        notify(booking.customer, "booking_reassigned", "Finding the best provider",
               f"Your booking {booking.booking_no} has been moved to {new_partner.display_name}.", booking_id=booking.id)
    else:
        cancel(booking, actor, "system", reason or "No provider available", waive_fee=True)


def assign(booking, actor, coworker_ids):
    if booking.status not in (S.CONFIRMED, S.ASSIGNED):
        raise BusinessError("INVALID_STATUS_TRANSITION", "Accept the booking before assigning.")
    coworkers = list(Coworker.objects.filter(pk__in=coworker_ids, partner=booking.partner))
    if not coworkers or len(coworkers) != len(set(coworker_ids)):
        raise BusinessError("COWORKER_NOT_FOUND", "Coworker not found in your team.", 404)
    free_ids = set(eligible_coworkers(booking.partner, booking.service, booking.scheduled_start, booking.scheduled_end)
                   .values_list("id", flat=True))
    current = {a.coworker_id for a in booking.assignments.filter(is_active=True)}
    for c in coworkers:
        if not c.is_assignable or not c.skills.filter(pk=booking.service_id).exists():
            raise BusinessError("COWORKER_NOT_ELIGIBLE", f"{c.name} is not approved/active or lacks this skill.")
        if c.id not in free_ids and c.id not in current:
            raise BusinessError("COWORKER_BUSY", f"{c.name} already has a job at this time.", 409)
    booking.assignments.filter(is_active=True).update(is_active=False)
    for c in coworkers:
        BookingAssignment.objects.create(booking=booking, coworker=c, assigned_by=actor)
        notify(c.user, "job_assigned", "New job assigned",
               f"{booking.service.name} at {booking.address.get('city')} on {timezone.localtime(booking.scheduled_start):%d %b %I:%M %p}",
               booking_id=booking.id)
    transition(booking, S.ASSIGNED, actor, "Assigned: " + ", ".join(c.name for c in coworkers))
    notify(booking.customer, "coworker_assigned", "Professional assigned",
           f"{', '.join(c.name for c in coworkers)} will handle your booking {booking.booking_no}.", booking_id=booking.id)


def check_otp(booking, entered, which):
    expected = booking.start_otp if which == "start" else booking.end_otp
    if str(entered).strip() != expected:
        raise BusinessError("INVALID_OTP", "OTP does not match. Ask the customer for the correct OTP.")


def recalc_total(booking):
    approved = booking.items.filter(is_addon=True, addon_status="approved")
    booking.addons_total = money(sum((i.total for i in approved), Decimal("0")))
    booking.total = money(booking.subtotal + booking.visit_charge + booking.addons_total - booking.discount
                          + booking.convenience_fee + booking.tax)
    booking.save(update_fields=["addons_total", "total"])


@transaction.atomic
def complete(booking, actor, otp=None, by_customer=False):
    if not by_customer:
        check_otp(booking, otp, "end")
    if booking.items.filter(addon_status="pending").exists():
        raise BusinessError("ADDON_PENDING", "Customer must approve or reject pending add-ons first.")
    if booking.status == S.IN_PROGRESS:
        transition(booking, S.COMPLETION_REQUESTED, actor, "Work finished")
    transition(booking, S.COMPLETED, actor, "Completed")
    booking.completed_at = timezone.now()
    booking.save(update_fields=["completed_at"])
    from apps.payments.services import post_completion
    post_completion(booking)
    partner = booking.partner
    partner.jobs_completed += 1
    partner.save(update_fields=["jobs_completed"])
    for c in booking.active_coworkers:
        c.jobs_completed += 1
        c.save(update_fields=["jobs_completed"])
    notify(booking.customer, "booking_completed", "Service completed",
           f"Booking {booking.booking_no} is complete. Please rate your experience.", booking_id=booking.id)


def cancellation_fee(booking, by):
    if by != "customer":
        return Decimal("0")
    if booking.status in (S.ON_THE_WAY, S.ARRIVED):
        return money(booking.visit_charge or get_setting("late_cancel_fee"))
    hours_left = (booking.scheduled_start - timezone.now()).total_seconds() / 3600
    if hours_left < float(get_setting("free_cancel_hours")) and booking.status not in (S.PAYMENT_PENDING, S.PENDING_ACCEPTANCE):
        return money(get_setting("late_cancel_fee"))
    return Decimal("0")


@transaction.atomic
def cancel(booking, actor, by, reason, waive_fee=False):
    fee = Decimal("0") if waive_fee else cancellation_fee(booking, by)
    transition(booking, S.CANCELLED, actor, f"Cancelled by {by}: {reason}")
    booking.cancelled_by, booking.cancel_reason, booking.cancellation_fee = by, reason, fee
    booking.save(update_fields=["cancelled_by", "cancel_reason", "cancellation_fee"])
    if booking.amount_paid > 0:
        from apps.payments.services import refund_booking
        refund_booking(booking, booking.amount_paid - fee, f"Cancellation: {reason}", actor)
    notify(booking.customer, "booking_cancelled", "Booking cancelled",
           f"Booking {booking.booking_no} was cancelled. {('Fee: ₹%s' % fee) if fee else ''}".strip(), booking_id=booking.id)
    if by != "partner":
        notify(booking.partner.owner, "booking_cancelled", "Booking cancelled", f"{booking.booking_no}: {reason}", booking_id=booking.id)
    for c in booking.active_coworkers:
        notify(c.user, "job_cancelled", "Job cancelled", f"{booking.booking_no} was cancelled.", booking_id=booking.id)
    if by == "admin":
        audit(actor, "booking_force_cancel", booking, reason=reason)
