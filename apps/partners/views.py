from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import generics, mixins, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.exceptions import BusinessError
from apps.core.permissions import IsPartner, IsPlatformAdmin
from apps.core.utils import audit

from .models import (
    AreaPartner, Coworker, CoworkerDocument, PartnerBankAccount, PartnerDocument, PartnerService,
    RateCardItem, VerificationCase, VerificationStatus,
)
from .serializers import (
    AdminPartnerSerializer, BankAccountSerializer, CoworkerDocumentSerializer, CoworkerSerializer,
    PartnerDocumentSerializer, PartnerProfileSerializer, PartnerServiceSerializer, PublicCoworkerSerializer,
    PublicPartnerSerializer, RateCardItemSerializer, VerificationCaseSerializer,
)
from .services import decide_case, get_or_create_coworker_user, open_case

PARSERS = [JSONParser, MultiPartParser, FormParser]


def get_partner(request):
    partner = getattr(request.user, "partner", None)
    if partner is None:
        raise BusinessError("PARTNER_PROFILE_MISSING", "Create your business profile first.", 404)
    return partner


# ---------------- Partner self-service ----------------
class PartnerProfileView(APIView):
    permission_classes = [IsPartner]
    parser_classes = PARSERS

    def get(self, request):
        return Response(PartnerProfileSerializer(get_partner(request), context={"request": request}).data)

    def post(self, request):
        if hasattr(request.user, "partner"):
            raise BusinessError("PROFILE_EXISTS", "Profile already exists. Use PATCH to update.", 409)
        s = PartnerProfileSerializer(data=request.data, context={"request": request})
        s.is_valid(raise_exception=True)
        s.save(owner=request.user)
        return Response(s.data, status=status.HTTP_201_CREATED)

    def patch(self, request):
        partner = get_partner(request)
        s = PartnerProfileSerializer(partner, data=request.data, partial=True, context={"request": request})
        s.is_valid(raise_exception=True)
        s.save()
        return Response(s.data)


class GoLiveStatusView(APIView):
    permission_classes = [IsPartner]

    def get(self, request):
        partner = get_partner(request)
        checks, live = partner.go_live_checklist()
        return Response({"is_live": live, "checks": [{"label": l, "ok": ok} for l, ok in checks],
                         "verification_status": partner.verification_status})


class SubmitVerificationView(APIView):
    permission_classes = [IsPartner]

    def post(self, request):
        p = get_partner(request)
        if p.verification_status == VerificationStatus.APPROVED:
            raise BusinessError("ALREADY_APPROVED", "Your profile is already approved.")
        missing = []
        if not p.documents.exists():
            missing.append("at least one KYC document")
        if not hasattr(p, "bank_account"):
            missing.append("bank account")
        if not p.partnerservice_set.filter(is_active=True).exists():
            missing.append("at least one service")
        if missing:
            raise BusinessError("INCOMPLETE_PROFILE", "Please add: " + ", ".join(missing))
        p.verification_status = VerificationStatus.SUBMITTED
        p.save(update_fields=["verification_status"])
        open_case("partner", partner=p)
        return Response({"message": "Submitted for verification", "verification_status": p.verification_status})


class PartnerServiceViewSet(viewsets.ModelViewSet):
    permission_classes = [IsPartner]
    serializer_class = PartnerServiceSerializer
    pagination_class = None

    def get_queryset(self):
        return PartnerService.objects.filter(partner=get_partner(self.request)).select_related("service")

    def perform_create(self, serializer):
        partner = get_partner(self.request)
        svc = serializer.validated_data["service"]
        obj, _ = PartnerService.objects.update_or_create(partner=partner, service=svc, defaults={"is_active": True})
        serializer.instance = obj


class PartnerDocumentViewSet(viewsets.ModelViewSet):
    permission_classes = [IsPartner]
    serializer_class = PartnerDocumentSerializer
    parser_classes = PARSERS
    pagination_class = None

    def get_queryset(self):
        return PartnerDocument.objects.filter(partner=get_partner(self.request))

    def perform_create(self, serializer):
        serializer.save(partner=get_partner(self.request))

    def perform_update(self, serializer):
        serializer.save(status="pending", reason="")


class BankAccountView(APIView):
    permission_classes = [IsPartner]

    def get(self, request):
        bank = getattr(get_partner(request), "bank_account", None)
        return Response(BankAccountSerializer(bank).data if bank else None)

    def put(self, request):
        partner = get_partner(request)
        bank = getattr(partner, "bank_account", None)
        s = BankAccountSerializer(bank, data=request.data)
        s.is_valid(raise_exception=True)
        s.save(partner=partner)
        return Response(s.data)


class RateCardViewSet(viewsets.ModelViewSet):
    permission_classes = [IsPartner]
    serializer_class = RateCardItemSerializer
    pagination_class = None
    filterset_fields = ["service_item__service", "is_active"]

    def get_queryset(self):
        return RateCardItem.objects.filter(partner=get_partner(self.request)).select_related("service_item__service")

    def perform_create(self, serializer):
        partner = get_partner(self.request)
        item = serializer.validated_data["service_item"]
        if RateCardItem.objects.filter(partner=partner, service_item=item).exists():
            raise BusinessError("DUPLICATE_ITEM", "This item is already on your rate card. Edit it instead.", 409)
        serializer.save(partner=partner)


class CoworkerViewSet(viewsets.ModelViewSet):
    """BR-C5: coworkers with job history cannot be deleted, only deactivated."""

    permission_classes = [IsPartner]
    serializer_class = CoworkerSerializer
    parser_classes = PARSERS
    filterset_fields = ["status", "verification_status", "skills"]
    search_fields = ["name", "phone"]

    def get_queryset(self):
        return Coworker.objects.filter(partner=get_partner(self.request)).prefetch_related("skills", "documents")

    @transaction.atomic
    def perform_create(self, serializer):
        partner = get_partner(self.request)
        sub = partner.active_subscription()
        if sub and sub.plan.max_coworkers and partner.coworkers.exclude(status="released").count() >= sub.plan.max_coworkers:
            raise BusinessError("PLAN_LIMIT_EXCEEDED", "Coworker limit of your plan reached. Upgrade your plan.", 403)
        d = serializer.validated_data
        if partner.coworkers.filter(phone=d["phone"]).exists():
            raise BusinessError("DUPLICATE_COWORKER", "A coworker with this phone already exists in your team.", 409)
        user = get_or_create_coworker_user(partner, d["phone"], d["name"], d.get("id_number_hash", ""))
        serializer.save(partner=partner, user=user)

    def perform_update(self, serializer):
        identity_fields = {"name", "phone", "photo", "id_number_hash", "date_of_birth"}
        changed = identity_fields & set(serializer.validated_data)
        obj = serializer.save()
        if changed and obj.verification_status == VerificationStatus.APPROVED:
            # BR-C3 identity change -> re-verification
            obj.verification_status = VerificationStatus.SUBMITTED
            obj.save(update_fields=["verification_status"])
            open_case("coworker", coworker=obj)

    def perform_destroy(self, instance):
        if instance.assignments.exists():
            raise BusinessError("HAS_HISTORY", "Coworker has job history. Deactivate instead of deleting.")
        instance.delete()

    @action(detail=True, methods=["post"])
    def submit(self, request, pk=None):
        c = self.get_object()
        if not c.documents.exists():
            raise BusinessError("NO_DOCUMENTS", "Upload at least one document before submitting.")
        if not c.skills.exists():
            raise BusinessError("NO_SKILLS", "Select at least one skill.")
        c.verification_status = VerificationStatus.SUBMITTED
        c.save(update_fields=["verification_status"])
        open_case("coworker", coworker=c)
        return Response(CoworkerSerializer(c, context={"request": request}).data)

    @action(detail=True, methods=["post"])
    def activate(self, request, pk=None):
        c = self.get_object()
        if c.verification_status != VerificationStatus.APPROVED:
            raise BusinessError("COWORKER_NOT_ELIGIBLE", "Only approved coworkers can be activated.")
        if c.status in (Coworker.Status.BLACKLISTED, Coworker.Status.SUSPENDED):
            raise BusinessError("COWORKER_BLOCKED", "Coworker is blocked by admin.", 403)
        c.status = Coworker.Status.ACTIVE
        c.save(update_fields=["status"])
        return Response(CoworkerSerializer(c, context={"request": request}).data)

    @action(detail=True, methods=["post"])
    def deactivate(self, request, pk=None):
        c = self.get_object()
        if c.status in (Coworker.Status.BLACKLISTED, Coworker.Status.SUSPENDED):
            raise BusinessError("COWORKER_BLOCKED", "Coworker is blocked by admin.", 403)
        c.status = Coworker.Status.INACTIVE
        c.save(update_fields=["status"])
        return Response(CoworkerSerializer(c, context={"request": request}).data)

    @action(detail=True, methods=["post"])
    def release(self, request, pk=None):
        c = self.get_object()
        c.status = Coworker.Status.RELEASED
        c.save(update_fields=["status"])
        return Response(CoworkerSerializer(c, context={"request": request}).data)


class CoworkerDocumentViewSet(viewsets.ModelViewSet):
    permission_classes = [IsPartner]
    serializer_class = CoworkerDocumentSerializer
    parser_classes = PARSERS
    pagination_class = None
    filterset_fields = ["coworker"]

    def get_queryset(self):
        return CoworkerDocument.objects.filter(coworker__partner=get_partner(self.request))

    def perform_create(self, serializer):
        if serializer.validated_data["coworker"].partner_id != get_partner(self.request).id:
            raise BusinessError("NOT_YOUR_COWORKER", "Coworker not found.", 404)
        serializer.save()


# ---------------- Public ----------------
class PublicPartnerView(generics.RetrieveAPIView):
    permission_classes = [permissions.AllowAny]
    serializer_class = PublicPartnerSerializer
    queryset = AreaPartner.objects.filter(verification_status=VerificationStatus.APPROVED, account_status="active")


class PublicPartnerCoworkersView(generics.ListAPIView):
    permission_classes = [permissions.AllowAny]
    serializer_class = PublicCoworkerSerializer
    pagination_class = None

    def get_queryset(self):
        qs = Coworker.objects.filter(partner_id=self.kwargs["pk"], verification_status="approved", status="active")
        svc = self.request.query_params.get("service_id")
        return qs.filter(skills=svc) if svc else qs


# ---------------- Admin ----------------
class AdminPartnerViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    permission_classes = [IsPlatformAdmin]
    serializer_class = AdminPartnerSerializer
    queryset = AreaPartner.objects.select_related("owner").order_by("-created_at")
    filterset_fields = ["verification_status", "account_status", "city"]
    search_fields = ["display_name", "legal_name", "owner__phone", "city"]

    def _set_status(self, request, value):
        p = self.get_object()
        p.account_status = value
        p.save(update_fields=["account_status"])
        audit(request.user, f"partner_{value}", p, reason=request.data.get("reason", ""))
        return Response(AdminPartnerSerializer(p, context={"request": request}).data)

    @action(detail=True, methods=["post"])
    def suspend(self, request, pk=None):
        return self._set_status(request, "suspended")

    @action(detail=True, methods=["post"])
    def reactivate(self, request, pk=None):
        return self._set_status(request, "active")

    @action(detail=True, methods=["get"], url_path="go-live")
    def go_live(self, request, pk=None):
        checks, live = self.get_object().go_live_checklist()
        return Response({"is_live": live, "checks": [{"label": l, "ok": ok} for l, ok in checks]})


class AdminCoworkerViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    permission_classes = [IsPlatformAdmin]
    serializer_class = CoworkerSerializer
    queryset = Coworker.objects.select_related("partner").prefetch_related("skills", "documents").order_by("-created_at")
    filterset_fields = ["status", "verification_status", "partner"]
    search_fields = ["name", "phone", "partner__display_name"]

    @action(detail=True, methods=["post"])
    def blacklist(self, request, pk=None):
        c = self.get_object()
        c.status = Coworker.Status.BLACKLISTED
        c.save(update_fields=["status"])
        audit(request.user, "coworker_blacklist", c, reason=request.data.get("reason", ""))
        return Response(CoworkerSerializer(c, context={"request": request}).data)

    @action(detail=True, methods=["post"])
    def suspend(self, request, pk=None):
        c = self.get_object()
        c.status = Coworker.Status.SUSPENDED
        c.save(update_fields=["status"])
        audit(request.user, "coworker_suspend", c, reason=request.data.get("reason", ""))
        return Response(CoworkerSerializer(c, context={"request": request}).data)


class VerificationCaseViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    permission_classes = [IsPlatformAdmin]
    serializer_class = VerificationCaseSerializer
    queryset = VerificationCase.objects.select_related("partner", "coworker", "assigned_to")
    filterset_fields = ["status", "subject_type", "assigned_to"]

    @action(detail=True, methods=["post"])
    def claim(self, request, pk=None):
        case = self.get_object()
        case.assigned_to, case.status = request.user, VerificationCase.Status.IN_REVIEW
        case.save(update_fields=["assigned_to", "status"])
        subject = case.partner or case.coworker
        subject.verification_status = VerificationStatus.UNDER_REVIEW
        subject.save(update_fields=["verification_status"])
        return Response(self.get_serializer(case).data)

    def _decide(self, request, decision):
        note = request.data.get("note", "")
        if decision != "approve" and not note:
            raise BusinessError("REASON_REQUIRED", "Please give a reason.")
        case = decide_case(self.get_object(), request.user, decision, note)
        return Response(self.get_serializer(case).data)

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        return self._decide(request, "approve")

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        return self._decide(request, "reject")

    @action(detail=True, methods=["post"])
    def resubmit(self, request, pk=None):
        return self._decide(request, "resubmit")

    @action(detail=True, methods=["post"], url_path=r"documents/(?P<doc_id>\d+)/decision")
    def document_decision(self, request, pk=None, doc_id=None):
        case = self.get_object()
        model = PartnerDocument if case.subject_type == "partner" else CoworkerDocument
        owner_filter = {"partner": case.partner} if case.subject_type == "partner" else {"coworker": case.coworker}
        doc = get_object_or_404(model, pk=doc_id, **owner_filter)
        result = request.data.get("status")
        if result not in ("verified", "rejected"):
            raise BusinessError("INVALID_STATUS", "status must be verified or rejected")
        doc.status, doc.reason = result, request.data.get("reason", "")
        doc.save(update_fields=["status", "reason"])
        return Response(self.get_serializer(case).data)
