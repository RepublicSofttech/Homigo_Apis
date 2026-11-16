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

from .models import OPEN_STATUSES, Booking, BookingAssignment, BookingItem, BookingOffer, BookingStatus, BookingStatusHistory

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


def get_area_slots(service, lat, lng, day, duration_minutes=60):
    """Slots for a broadcast booking: a slot is open when any nearby live partner has capacity."""
    partners = [p for p, _ in find_partners(service, lat, lng, limit=5)]
    if not partners:
        return []
    merged = None
    for p in partners:
        slots = get_slots(p, service, day, duration_minutes)
        if merged is None:
            merged = {s["start"]: dict(s) for s in slots}
            continue
        for s in slots:
            m = merged.setdefault(s["start"], dict(s, capacity=0, available=False))
            m["capacity"] += s["capacity"]
            m["available"] = m["available"] or s["available"]
    return sorted(merged.values(), key=lambda s: s["start"])


# ---------------- Pricing ----------------
def price_items(partner, service, items):
    """items: [{'service_item': id, 'quantity': n}] -> priced lines.
    With a partner: that partner's rate card. Without (broadcast): the platform price (ServiceItem.default_price)."""
    if not items:
        raise BusinessError("NO_ITEMS", "Select at least one item.")
    lines = []
    if partner is None:
        rates = {i.id: i.default_price for i in service.items.filter(is_active=True)}
    else:
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
    S.PAYMENT_PENDING: {S.PENDING_ACCEPTANCE, S.SEARCHING, S.CANCELLED},
    S.SEARCHING: {S.SEARCHING, S.CONFIRMED, S.CANCELLED},
    S.PENDING_ACCEPTANCE: {S.CONFIRMED, S.CANCELLED, S.PENDING_ACCEPTANCE, S.SEARCHING},
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
    """partner=None creates a broadcast booking (gap P1) that is offered to the nearest partners."""
    broadcast = partner is None
    quote = build_quote(user, partner, service, items, coupon_code)
    end = scheduled_start + timedelta(minutes=quote["duration_minutes"])
    if scheduled_start < timezone.now() - timedelta(minutes=5):
        raise BusinessError("SLOT_IN_PAST", "Selected time is in the past.")
    if broadcast:
        candidates = broadcast_candidates(service, address.latitude, address.longitude, scheduled_start, end,
                                          payment_mode, exclude_ids=(), extra_radius_km=0)
        if not candidates:
            raise BusinessError("SLOT_UNAVAILABLE", "No professional is free at this time near you. Please pick another slot.", 409)
    else:
        # Lock partner row so two checkouts can't take the last slot at the same time.
        AreaPartner.objects.select_for_update().get(pk=partner.pk)
        if payment_mode == "cash" and not partner.accepts_cash:
            raise BusinessError("CASH_NOT_ALLOWED", "This provider accepts online payment only.")
        if booking_type != "instant" and slot_capacity(partner, service, scheduled_start, end) <= 0:
            raise BusinessError("SLOT_UNAVAILABLE", "This slot was just taken. Please pick another time.", 409)
    if payment_mode == "online":
        status = S.PAYMENT_PENDING
    else:
        status = S.SEARCHING if broadcast else S.PENDING_ACCEPTANCE
    booking = Booking.objects.create(
        customer=user, partner=partner, service=service, booking_type=booking_type,
        dispatch_mode=Booking.DispatchMode.BROADCAST if broadcast else Booking.DispatchMode.DIRECT,
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
    elif status == S.SEARCHING:
        broadcast_next_round(booking)
    return booking


def send_to_partner(booking):
    booking.sla_due_at = timezone.now() + timedelta(minutes=sla_minutes(booking))
    booking.save(update_fields=["sla_due_at"])
    notify(booking.partner.owner, "booking_new", f"New booking {booking.booking_no}",
           f"{booking.service.name} on {timezone.localtime(booking.scheduled_start):%d %b, %I:%M %p}. Accept within SLA.",
           booking_id=booking.id)


def on_payment_success(booking):
    if booking.status != S.PAYMENT_PENDING:
        return
    if booking.dispatch_mode == Booking.DispatchMode.BROADCAST:
        transition(booking, S.SEARCHING, note="Payment received")
        broadcast_next_round(booking)
    else:
        transition(booking, S.PENDING_ACCEPTANCE, note="Payment received")
        send_to_partner(booking)


# ---------------- Broadcast dispatch (gap P1) ----------------
def broadcast_candidates(service, lat, lng, start, end, payment_mode, exclude_ids=(), extra_radius_km=0):
    """Nearest live partners who also have a free coworker for the slot (and take cash, if needed)."""
    out = []
    for p, dist in find_partners(service, lat, lng, exclude_ids=exclude_ids, extra_radius_km=extra_radius_km, limit=20):
        if payment_mode == "cash" and not p.accepts_cash:
            continue
        if slot_capacity(p, service, start, end) <= 0:
            continue
        out.append((p, dist))
    return out


def broadcast_next_round(booking, actor=None):
    """Send the booking to the next batch of nearest partners. Cancels with refund when nobody is left."""
    booking.offers.filter(status=BookingOffer.Status.PENDING).update(status=BookingOffer.Status.EXPIRED)
    max_rounds = int(get_setting("broadcast_max_rounds"))
    if booking.broadcast_round >= max_rounds:
        cancel(booking, actor, "system", "No professional accepted in time", waive_fee=True)
        return []
    already = set(booking.offers.values_list("partner_id", flat=True)) | set(booking.rejected_partner_ids)
    extra = 0
    if booking.broadcast_round >= 1:  # widen to the fallback radius after the first round
        extra = max(float(get_setting("fallback_radius_km")) - float(get_setting("default_service_radius_km")), 0)
    candidates = broadcast_candidates(booking.service, booking.latitude, booking.longitude, booking.scheduled_start,
                                      booking.scheduled_end, booking.payment_mode, exclude_ids=already,
                                      extra_radius_km=extra)[: int(get_setting("broadcast_batch_size"))]
    if not candidates:
        cancel(booking, actor, "system", "No professional available nearby", waive_fee=True)
        return []
    booking.broadcast_round += 1
    expires = timezone.now() + timedelta(minutes=int(get_setting("broadcast_round_minutes")))
    booking.sla_due_at = expires
    booking.save(update_fields=["broadcast_round", "sla_due_at"])
    offers = []
    for p, dist in candidates:
        offers.append(BookingOffer.objects.create(booking=booking, partner=p, round=booking.broadcast_round,
                                                  distance_km=dist, expires_at=expires))
        notify(p.owner, "job_request", f"New job request: {booking.service.name}",
               f"{timezone.localtime(booking.scheduled_start):%d %b, %I:%M %p} · {dist} km away · {booking.address.get('city')}. "
               "First partner to accept gets the job.", booking_id=booking.id)
    return offers


@transaction.atomic
def accept_offer(offer, actor):
    booking = Booking.objects.select_for_update().get(pk=offer.booking_id)
    offer = BookingOffer.objects.select_for_update().get(pk=offer.pk)
    if booking.status != S.SEARCHING or offer.status != BookingOffer.Status.PENDING:
        raise BusinessError("OFFER_NOT_AVAILABLE", "This job was already taken by another partner or has closed.", 409)
    if offer.expires_at < timezone.now():
        offer.status = BookingOffer.Status.EXPIRED
        offer.save(update_fields=["status"])
        raise BusinessError("OFFER_EXPIRED", "This job request has expired.", 410)
    if slot_capacity(offer.partner, booking.service, booking.scheduled_start, booking.scheduled_end) <= 0:
        raise BusinessError("SLOT_UNAVAILABLE", "You have no free professional for this time.", 409)
    now = timezone.now()
    offer.status, offer.responded_at = BookingOffer.Status.ACCEPTED, now
    offer.save(update_fields=["status", "responded_at"])
    booking.offers.filter(status=BookingOffer.Status.PENDING).exclude(pk=offer.pk).update(
        status=BookingOffer.Status.LOST, responded_at=now)
    booking.partner, booking.distance_km = offer.partner, offer.distance_km
    booking.save(update_fields=["partner", "distance_km"])
    accept(booking, actor)
    return booking


@transaction.atomic
def decline_offer(offer, actor, reason=""):
    if offer.status != BookingOffer.Status.PENDING:
        raise BusinessError("OFFER_NOT_AVAILABLE", "This job request is no longer open.", 409)
    offer.status, offer.responded_at, offer.decline_reason = BookingOffer.Status.DECLINED, timezone.now(), reason[:255]
    offer.save(update_fields=["status", "responded_at", "decline_reason"])
    booking = Booking.objects.select_for_update().get(pk=offer.booking_id)
    if booking.status == S.SEARCHING and not booking.offers.filter(status=BookingOffer.Status.PENDING).exists():
        broadcast_next_round(booking, actor)


def accept(booking, actor):
    transition(booking, S.CONFIRMED, actor, f"Accepted by {booking.partner}")
    booking.accepted_at = timezone.now()
    booking.save(update_fields=["accepted_at"])
    notify(booking.customer, "booking_confirmed", "Booking confirmed",
           f"{booking.partner.display_name} accepted your booking {booking.booking_no}.", booking_id=booking.id)


@transaction.atomic
def reject_or_fallback(booking, actor, reason="", auto=False):
    """BR-B4: the chosen partner rejected / missed the SLA -> broadcast to the nearest other partners
    (gap P1) at the same price -> cancel + refund if nobody accepts."""
    if booking.status != S.PENDING_ACCEPTANCE:
        raise BusinessError("INVALID_STATUS_TRANSITION", "Only pending bookings can be rejected.")
    old_partner = booking.partner
    booking.rejected_partner_ids = list(set(booking.rejected_partner_ids) | {booking.partner_id})
    booking.partner, booking.dispatch_mode = None, Booking.DispatchMode.BROADCAST
    booking.fallback_count += 1
    booking.save()
    transition(booking, S.SEARCHING, actor, f"{old_partner} could not take it: {reason or 'not accepted'}")
    offers = broadcast_next_round(booking, actor)
    if offers:
        notify(booking.customer, "booking_reassigned", "Finding the best provider",
               f"{old_partner} is unavailable, so we sent booking {booking.booking_no} to other nearby professionals.",
               booking_id=booking.id)


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


def check_not_too_early(booking):
    """Field work can begin at most `early_start_hours` before the booked slot."""
    hours = float(get_setting("early_start_hours"))
    if booking.scheduled_start - timezone.now() > timedelta(hours=hours):
        when = timezone.localtime(booking.scheduled_start)
        raise BusinessError("TOO_EARLY", f"This job is scheduled for {when:%d %b, %I:%M %p}. You can start travel {hours:g} hours before.")


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
    if hours_left < float(get_setting("free_cancel_hours")) and booking.status not in (S.PAYMENT_PENDING, S.SEARCHING, S.PENDING_ACCEPTANCE):
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
    booking.offers.filter(status=BookingOffer.Status.PENDING).update(status=BookingOffer.Status.EXPIRED)
    if by != "partner" and booking.partner_id:
        notify(booking.partner.owner, "booking_cancelled", "Booking cancelled", f"{booking.booking_no}: {reason}", booking_id=booking.id)
    for c in booking.active_coworkers:
        notify(c.user, "job_cancelled", "Job cancelled", f"{booking.booking_no} was cancelled.", booking_id=booking.id)
    if by == "admin":
        audit(actor, "booking_force_cancel", booking, reason=reason)
