from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.core.exceptions import BusinessError
from apps.core.utils import audit
from apps.notifications.services import notify

from .models import Coworker, VerificationCase, VerificationStatus

ACTIVE_LINK_STATES = [Coworker.Status.ACTIVE, Coworker.Status.INACTIVE, Coworker.Status.SUSPENDED]


def get_or_create_coworker_user(partner, phone, name, id_hash=""):
    """BR-C1 one partner per coworker; BR-C6 blacklisted can never be re-added."""
    others = Coworker.objects.filter(phone=phone).exclude(partner=partner)
    if id_hash:
        others = others | Coworker.objects.filter(id_number_hash=id_hash).exclude(partner=partner)
    if others.filter(status=Coworker.Status.BLACKLISTED).exists():
        raise BusinessError("COWORKER_BLACKLISTED", "This person is blacklisted on the platform.", 409)
    if others.filter(status__in=ACTIVE_LINK_STATES).exists():
        raise BusinessError("COWORKER_ALREADY_LINKED", "This coworker is already linked with another partner.", 409)
    user = User.objects.filter(phone=phone).first()
    if user and user.user_type not in ("coworker",):
        raise BusinessError("PHONE_IN_USE", "This phone number is already used by another account type.", 409)
    if not user:
        first, _, last = name.partition(" ")
        user = User.objects.create_user(phone=phone, user_type="coworker", first_name=first, last_name=last)
    return user


def open_case(subject_type, partner=None, coworker=None):
    case = VerificationCase.objects.filter(
        subject_type=subject_type, partner=partner, coworker=coworker,
        status__in=[VerificationCase.Status.OPEN, VerificationCase.Status.IN_REVIEW],
    ).first()
    if case:
        return case
    return VerificationCase.objects.create(
        subject_type=subject_type, partner=partner, coworker=coworker,
        sla_due=timezone.now() + timedelta(hours=48),
    )


@transaction.atomic
def decide_case(case, actor, decision, note=""):
    if case.status in (VerificationCase.Status.APPROVED, VerificationCase.Status.REJECTED):
        raise BusinessError("CASE_CLOSED", "This case is already closed.")
    status_map = {
        "approve": (VerificationCase.Status.APPROVED, VerificationStatus.APPROVED),
        "reject": (VerificationCase.Status.REJECTED, VerificationStatus.REJECTED),
        "resubmit": (VerificationCase.Status.RESUBMISSION, VerificationStatus.RESUBMISSION),
    }
    case_status, subject_status = status_map[decision]
    case.status, case.decided_by, case.decided_at, case.note = case_status, actor, timezone.now(), note
    case.save()
    if case.subject_type == "partner":
        p = case.partner
        p.verification_status = subject_status
        p.rejection_reason = "" if decision == "approve" else note
        p.save(update_fields=["verification_status", "rejection_reason", "updated_at"])
        if decision == "approve":
            p.documents.update(status="verified", reason="")
            if hasattr(p, "bank_account"):
                p.bank_account.is_verified = True
                p.bank_account.save(update_fields=["is_verified"])
        notify(p.owner, "verification_decision", f"Verification {decision}d",
               note or "Your partner profile has been approved. Welcome to HomeServ!", partner_id=p.id)
        audit(actor, f"partner_verification_{decision}", p, note=note)
    else:
        c = case.coworker
        c.verification_status = subject_status
        c.rejection_reason = "" if decision == "approve" else note
        if decision == "approve":
            c.status = Coworker.Status.ACTIVE
            c.documents.update(status="verified", reason="")
        c.save()
        notify(c.partner.owner, "coworker_verification", f"Coworker {c.name}: {decision}d", note or "Coworker approved.", coworker_id=c.id)
        notify(c.user, "coworker_verification", f"Your profile was {decision}d", note or "You can now receive jobs.")
        audit(actor, f"coworker_verification_{decision}", c, note=note)
    return case
