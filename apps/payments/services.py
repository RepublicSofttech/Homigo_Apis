from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.utils import timezone

from apps.core.config_defaults import get_setting
from apps.core.exceptions import BusinessError
from apps.core.models import AuditLog
from apps.core.utils import audit
from apps.notifications.services import notify

from .models import CommissionLedger, PartnerWallet, Payment, Refund, Settlement, WalletTransaction

TWO = Decimal("0.01")


def money(v):
    return Decimal(v).quantize(TWO, ROUND_HALF_UP)


def wallet_for(partner):
    return PartnerWallet.objects.get_or_create(partner=partner)[0]


def wallet_post(partner, amount, kind, description, booking=None):
    wallet = PartnerWallet.objects.select_for_update().get(pk=wallet_for(partner).pk)
    wallet.balance = money(wallet.balance + amount)
    wallet.save(update_fields=["balance", "updated_at"])
    WalletTransaction.objects.create(wallet=wallet, amount=money(amount), kind=kind, description=description,
                                     booking=booking, balance_after=wallet.balance)
    return wallet


def commission_percent(booking):
    if booking.service.commission_override is not None:
        return booking.service.commission_override
    sub = booking.partner.active_subscription()
    if sub:
        return sub.plan.commission_percent
    return Decimal(str(get_setting("default_commission_percent")))


@transaction.atomic
def post_completion(booking):
    """BR-F1: commission on service value after partner-funded discount; materials excluded."""
    if hasattr(booking, "commission"):
        return booking.commission
    materials = sum((i.total for i in booking.items.filter(is_material=True, addon_status="approved")), Decimal("0"))
    service_value = money(booking.subtotal + booking.visit_charge + booking.addons_total - materials - booking.partner_discount_share)
    pct = commission_percent(booking)
    commission = money(service_value * pct / 100)
    gst = money(commission * Decimal(str(get_setting("gst_percent"))) / 100)
    # Partner receives service value + materials, platform funded discount is paid back by platform
    # Kit charge (gap C13) is a pass-through like materials: no commission, paid to the partner.
    partner_net = money(service_value + materials + booking.supplies_charge - commission - gst)
    ledger = CommissionLedger.objects.create(
        booking=booking, partner=booking.partner, service_value=service_value, commission_percent=pct,
        commission_amount=commission, gst_on_commission=gst, partner_net=partner_net, payment_mode=booking.payment_mode,
    )
    if booking.payment_mode == "online":
        wallet_post(booking.partner, partner_net, "booking_earning", f"Earning for {booking.booking_no}", booking)
    else:
        # Partner already holds the cash, so platform commission becomes a receivable.
        wallet_post(booking.partner, -(commission + gst), "cash_commission", f"Commission due for cash job {booking.booking_no}", booking)
        booking.payment_status, booking.amount_paid, booking.cash_collected = "paid", booking.total, True
        booking.save(update_fields=["payment_status", "amount_paid", "cash_collected"])
    return ledger


def refund_booking(booking, amount, reason, actor=None):
    amount = money(max(amount, Decimal("0")))
    payment = booking.payments.filter(status="success").order_by("-created_at").first()
    if not payment or amount <= 0:
        return None
    refund = Refund.objects.create(payment=payment, amount=amount, reason=reason,
                                   approved_by=actor if getattr(actor, "is_authenticated", False) else None)
    payment.status = "refunded"
    payment.save(update_fields=["status"])
    booking.payment_status = "refunded"
    booking.save(update_fields=["payment_status"])
    notify(booking.customer, "refund", "Refund initiated", f"₹{amount} will be credited to your original payment method.",
           booking_id=booking.id)
    return refund


@transaction.atomic
def mark_success(payment, gateway_payment_id="", raw=None):
    """Idempotent: calling twice does nothing the second time."""
    payment = Payment.objects.select_for_update().get(pk=payment.pk)
    if payment.status == Payment.Status.SUCCESS:
        return payment
    payment.status, payment.gateway_payment_id = Payment.Status.SUCCESS, gateway_payment_id or payment.gateway_payment_id
    payment.raw = raw or payment.raw
    payment.save()
    if payment.purpose == Payment.Purpose.BOOKING:
        from apps.bookings.services import on_payment_success
        b = payment.booking
        b.amount_paid = money(b.amount_paid + payment.amount)
        b.payment_status = "paid" if b.amount_paid >= b.total else b.payment_status
        b.save(update_fields=["amount_paid", "payment_status"])
        on_payment_success(b)
    elif payment.purpose == Payment.Purpose.SUBSCRIPTION:
        payment.subscription.activate()
        notify(payment.user, "subscription_active", "Subscription active",
               f"{payment.subscription.plan.name} plan is active till {payment.subscription.end_date:%d %b %Y}.")
    elif payment.purpose == Payment.Purpose.WALLET_TOPUP:
        wallet_post(payment.user.partner, payment.amount, "topup", "Wallet top-up")
    elif payment.purpose == Payment.Purpose.TIP:
        settle_tip(payment.tip)
    return payment


# ---------------- Tips (gap C1) ----------------
def create_tip(booking, customer, amount):
    from datetime import timedelta

    from .models import Tip

    if booking.customer_id != customer.id:
        raise BusinessError("NOT_YOUR_BOOKING", "Booking not found.", 404)
    if booking.status != "completed":
        raise BusinessError("TIP_NOT_ALLOWED", "You can tip only after the job is completed.")
    days = int(get_setting("tip_window_days"))
    if booking.completed_at and booking.completed_at < timezone.now() - timedelta(days=days):
        raise BusinessError("TIP_WINDOW_CLOSED", f"Tips can be added within {days} days of completion.")
    if booking.tips.filter(status=Tip.Status.PAID).exists():
        raise BusinessError("ALREADY_TIPPED", "You have already tipped for this booking.", 409)
    amount = money(amount)
    max_tip = money(get_setting("tip_max_amount"))
    if amount < 10 or amount > max_tip:
        raise BusinessError("INVALID_TIP", f"Tip must be between ₹10 and ₹{max_tip}.")
    booking.tips.filter(status=Tip.Status.PENDING).delete()
    coworkers = booking.active_coworkers
    return Tip.objects.create(booking=booking, customer=customer, partner=booking.partner,
                              coworker=coworkers[0] if coworkers else None, amount=amount)


def settle_tip(tip):
    if tip.status == "paid":
        return tip
    tip.status, tip.paid_at = "paid", timezone.now()
    tip.save(update_fields=["status", "paid_at"])
    who = tip.coworker.name if tip.coworker else "your team"
    wallet_post(tip.partner, tip.amount, "tip", f"Tip for {who} on {tip.booking.booking_no} (pass 100% to the professional)",
                tip.booking)
    notify(tip.partner.owner, "tip_received", f"₹{tip.amount} tip received",
           f"A customer tipped {who} for {tip.booking.booking_no}. Please pass it on in full.", booking_id=tip.booking_id)
    if tip.coworker:
        notify(tip.coworker.user, "tip_received", f"You got a ₹{tip.amount} tip!",
               f"Customer tipped you for {tip.booking.booking_no}. It will be paid via {tip.partner.display_name}.",
               booking_id=tip.booking_id)
    return tip


def run_settlements(actor):
    """Create a settlement for every partner with a positive wallet balance (weekly job)."""
    created = []
    for wallet in PartnerWallet.objects.filter(balance__gt=0).select_related("partner"):
        if Settlement.objects.filter(partner=wallet.partner, status="pending").exists():
            continue
        with transaction.atomic():
            amount = wallet.balance
            s = Settlement.objects.create(partner=wallet.partner, amount=amount, note="Weekly settlement")
            wallet_post(wallet.partner, -amount, "settlement", f"Settlement #{s.pk}")
            created.append(s)
    AuditLog.objects.create(actor=actor, action="settlement_run", entity="Settlement", entity_id="run",
                            data={"count": len(created)})
    return created


def mark_settlement_paid(settlement, utr, actor):
    if settlement.status == "paid":
        raise BusinessError("ALREADY_PAID", "Settlement already paid.")
    settlement.status, settlement.utr, settlement.paid_at = "paid", utr, timezone.now()
    settlement.save()
    notify(settlement.partner.owner, "settlement_paid", "Settlement paid", f"₹{settlement.amount} sent. UTR {utr}.")
    audit(actor, "settlement_paid", settlement, utr=utr)
    return settlement
