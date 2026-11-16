from datetime import date as date_cls
from datetime import timedelta

from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import mixins, permissions, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import Address
from apps.catalogue.models import Service, ServiceItem
from apps.core.exceptions import BusinessError
from apps.core.permissions import IsCoworker, IsCustomer, IsPartner, IsPlatformAdmin
from apps.core.utils import haversine_km
from apps.partners.models import AreaPartner, Coworker, VerificationCase
from apps.partners.serializers import PublicPartnerSerializer, RateCardItemSerializer
from apps.partners.views import get_partner

from . import services as svc
from .models import OPEN_STATUSES, Booking, BookingItem, BookingOffer, BookingStatus
from .serializers import (
    BookingCreateSerializer, BookingListSerializer, BookingSerializer, JobRequestSerializer, PricePreviewSerializer,
)

S = BookingStatus


def parse_latlng(request):
    try:
        return float(request.query_params["lat"]), float(request.query_params["lng"])
    except (KeyError, ValueError):
        raise BusinessError("LOCATION_REQUIRED", "lat and lng query parameters are required.", 400)


class AvailabilityView(APIView):
    """GET /services/{id}/availability/?lat&lng -> nearest live provider + alternatives."""

    permission_classes = [permissions.AllowAny]

    def get(self, request, pk):
        service = get_object_or_404(Service, pk=pk, is_active=True)
        lat, lng = parse_latlng(request)
        results = svc.find_partners(service, lat, lng, limit=5)
        if not results:
            return Response({"available": False, "code": "SERVICE_NOT_AVAILABLE_AT_LOCATION",
                             "message": "Not available in your area yet.", "partner": None, "alternatives": []})

        def pack(p, d):
            data = PublicPartnerSerializer(p, context={"request": request}).data
            data["distance_km"] = d
            data["rate_card"] = RateCardItemSerializer(
                p.rate_card.filter(is_active=True, service_item__service=service).select_related("service_item"),
                many=True).data
            return data

        return Response({"available": True, "partner": pack(*results[0]),
                         "alternatives": [pack(p, d) for p, d in results[1:]]})


class SlotsView(APIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        """?partner_id=  -> that partner's slots.  ?lat=&lng= (no partner) -> combined slots of nearby partners."""
        q = request.query_params
        service = get_object_or_404(Service, pk=q.get("service_id"))
        try:
            day = date_cls.fromisoformat(q.get("date", ""))
        except ValueError:
            day = timezone.localdate()
        duration = int(q.get("duration", 60))
        if q.get("partner_id"):
            partner = get_object_or_404(AreaPartner, pk=q.get("partner_id"))
            return Response({"date": day, "slots": svc.get_slots(partner, service, day, duration)})
        lat, lng = parse_latlng(request)
        return Response({"date": day, "slots": svc.get_area_slots(service, lat, lng, day, duration)})


class PricePreviewView(APIView):
    serializer_class = PricePreviewSerializer
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        s = PricePreviewSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        d = s.validated_data
        service = get_object_or_404(Service, pk=d["service_id"])
        partner = get_object_or_404(AreaPartner, pk=d["partner_id"]) if d.get("partner_id") else None
        q = svc.build_quote(request.user, partner, service, d["items"], d["coupon_code"], d.get("bring_supplies", False))
        return Response({
            "lines": [{k: v for k, v in l.items() if k != "service_item"} | {"service_item": l["service_item"].id} for l in q["lines"]],
            **{k: q[k] for k in ("subtotal", "visit_charge", "discount", "convenience_fee", "tax", "total", "duration_minutes",
                                 "bring_supplies", "supplies_charge", "supplies_list")},
            "coupon": q["offer"].code if q["offer"] else None,
        })


class BookingViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin, viewsets.GenericViewSet):
    """Scoped: customer -> own, partner -> own business, coworker -> assigned, admin -> all."""

    filterset_fields = ["status", "service", "payment_status", "booking_type"]
    search_fields = ["booking_no", "customer__phone", "customer__first_name"]
    ordering_fields = ["scheduled_start", "created_at"]

    def get_serializer_class(self):
        return BookingListSerializer if self.action == "list" else BookingSerializer

    def get_queryset(self):
        u = self.request.user
        qs = Booking.objects.select_related("service", "partner", "customer").prefetch_related("items", "assignments__coworker")
        if u.user_type == "customer":
            qs = qs.filter(customer=u)
        elif u.user_type == "partner":
            qs = qs.filter(partner__owner=u)
        elif u.user_type == "coworker":
            qs = qs.filter(assignments__coworker__user=u, assignments__is_active=True).distinct()
        elif not (u.user_type == "admin" or u.is_superuser):
            qs = qs.none()
        bucket = self.request.query_params.get("bucket")
        if bucket == "upcoming":
            qs = qs.filter(status__in=OPEN_STATUSES)
        elif bucket == "past":
            qs = qs.exclude(status__in=OPEN_STATUSES)
        elif bucket == "today":
            qs = qs.filter(scheduled_start__date=timezone.localdate())
        return qs

    def create(self, request, *args, **kwargs):
        if request.user.user_type != "customer":
            raise BusinessError("CUSTOMER_ONLY", "Only customers can create bookings.", 403)
        s = BookingCreateSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        d = s.validated_data
        service = get_object_or_404(Service, pk=d["service_id"], is_active=True)
        address = get_object_or_404(Address, pk=d["address_id"], user=request.user)
        if d.get("partner_id"):
            partner = get_object_or_404(AreaPartner, pk=d["partner_id"])
            dist = haversine_km(address.latitude, address.longitude, partner.latitude, partner.longitude)
            _, live = partner.go_live_checklist(service)
            if not live or dist > float(partner.service_radius_km):
                raise BusinessError("PARTNER_NOT_AVAILABLE", "This provider does not serve your address.")
        else:
            # Gap P1: no provider chosen -> broadcast to the nearest partners, first to accept wins.
            if not svc.find_partners(service, address.latitude, address.longitude, limit=1):
                raise BusinessError("SERVICE_NOT_AVAILABLE_AT_LOCATION", "No provider serves this address yet.", 404)
            partner, dist = None, 0
        booking = svc.create_booking(
            request.user, service, partner, address, d["items"], d["scheduled_start"], d["booking_type"],
            d["payment_mode"], d["notes"], d["answers"], d["coupon_code"], round(dist, 2), d.get("bring_supplies", False),
        )
        return Response(BookingSerializer(booking, context={"request": request}).data, status=201)

    def _ok(self, booking):
        booking.refresh_from_db()
        return Response(BookingSerializer(booking, context={"request": self.request}).data)

    def _require(self, *roles):
        u = self.request.user
        role = "admin" if u.is_superuser else u.user_type
        if role not in roles:
            raise BusinessError("FORBIDDEN", "You cannot perform this action.", 403)

    # ---- partner actions ----
    @action(detail=True, methods=["post"])
    def accept(self, request, pk=None):
        self._require("partner")
        b = self.get_object()
        svc.accept(b, request.user)
        return self._ok(b)

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        self._require("partner")
        b = self.get_object()
        reason = request.data.get("reason", "")
        if not reason:
            raise BusinessError("REASON_REQUIRED", "Please give a reason.")
        svc.reject_or_fallback(b, request.user, reason)
        return Response({"message": "Booking rejected"})

    @action(detail=True, methods=["post"])
    def assign(self, request, pk=None):
        self._require("partner")
        b = self.get_object()
        ids = request.data.get("coworker_ids") or ([request.data["coworker_id"]] if request.data.get("coworker_id") else [])
        svc.assign(b, request.user, [int(i) for i in ids])
        return self._ok(b)

    @action(detail=True, methods=["get"], url_path="eligible-coworkers")
    def eligible_coworkers(self, request, pk=None):
        self._require("partner")
        b = self.get_object()
        qs = svc.eligible_coworkers(b.partner, b.service, b.scheduled_start, b.scheduled_end)
        current = [a.coworker_id for a in b.assignments.filter(is_active=True)]
        qs = qs | Coworker.objects.filter(pk__in=current)
        return Response([{"id": c.id, "name": c.name, "phone": c.phone, "rating": c.rating_avg, "skill_level": c.skill_level}
                         for c in qs.distinct()])

    # ---- coworker actions ----
    @action(detail=True, methods=["post"], url_path="on-the-way")
    def on_the_way(self, request, pk=None):
        self._require("coworker", "partner")
        b = self.get_object()
        svc.check_not_too_early(b)
        svc.transition(b, S.ON_THE_WAY, request.user, "On the way")
        svc.notify(b.customer, "on_the_way", "Professional is on the way", f"For booking {b.booking_no}", booking_id=b.id)
        return self._ok(b)

    @action(detail=True, methods=["post"])
    def arrived(self, request, pk=None):
        self._require("coworker", "partner")
        b = self.get_object()
        svc.transition(b, S.ARRIVED, request.user, "Arrived at location")
        svc.notify(b.customer, "arrived", "Professional has arrived", f"Share start OTP {b.start_otp} to begin.", booking_id=b.id)
        return self._ok(b)

    @action(detail=True, methods=["post"])
    def start(self, request, pk=None):
        self._require("coworker", "partner")
        b = self.get_object()
        svc.check_otp(b, request.data.get("otp", ""), "start")
        kit = request.data.get("kit_confirmed") in (True, "true", "1", 1)
        if b.bring_supplies and not kit:
            raise BusinessError("KIT_NOT_CONFIRMED", "Confirm that you carried the supplies kit before starting.")
        svc.transition(b, S.IN_PROGRESS, request.user, "Work started (OTP verified" + (", kit carried)" if kit else ")"))
        b.started_at, b.supplies_confirmed = timezone.now(), kit and b.bring_supplies
        b.save(update_fields=["started_at", "supplies_confirmed"])
        return self._ok(b)

    @action(detail=True, methods=["post"])
    def addons(self, request, pk=None):
        """Coworker raises extra work: {service_item, quantity} or {name, price, is_material}."""
        self._require("coworker", "partner")
        b = self.get_object()
        if b.status != S.IN_PROGRESS:
            raise BusinessError("INVALID_STATUS_TRANSITION", "Add-ons can be raised only while work is in progress.")
        qty = int(request.data.get("quantity", 1))
        if request.data.get("service_item"):
            item = get_object_or_404(ServiceItem, pk=request.data["service_item"], service=b.service)
            rate = b.partner.rate_card.filter(service_item=item, is_active=True).first()
            price = rate.price if rate else item.default_price
            name, is_material = item.name, False
        else:
            name = request.data.get("name", "").strip()
            try:
                price = svc.money(request.data.get("price"))
            except Exception:
                raise BusinessError("INVALID_PRICE", "Enter a valid price.")
            if not name or price <= 0:
                raise BusinessError("INVALID_ADDON", "Name and price are required.")
            item, is_material = None, bool(request.data.get("is_material", True))
        BookingItem.objects.create(booking=b, service_item=item, name=name, quantity=qty, unit_price=price,
                                   total=svc.money(price * qty), is_addon=True, is_material=is_material, addon_status="pending")
        svc.notify(b.customer, "addon_raised", "Approve extra work", f"{name} x{qty} = ₹{svc.money(price * qty)}", booking_id=b.id)
        return self._ok(b)

    @action(detail=True, methods=["post"])
    def complete(self, request, pk=None):
        self._require("coworker", "partner")
        b = self.get_object()
        svc.complete(b, request.user, otp=request.data.get("otp", ""))
        return self._ok(b)

    @action(detail=True, methods=["post"], url_path="cash-collected")
    def cash_collected(self, request, pk=None):
        self._require("coworker", "partner")
        b = self.get_object()
        if b.payment_mode != "cash":
            raise BusinessError("NOT_CASH", "This booking is prepaid.")
        b.cash_collected = True
        b.save(update_fields=["cash_collected"])
        return self._ok(b)

    @action(detail=True, methods=["post"], url_path="no-show")
    def no_show(self, request, pk=None):
        self._require("coworker", "partner")
        b = self.get_object()
        svc.transition(b, S.NO_SHOW, request.user, request.data.get("note", "Customer not available"))
        return self._ok(b)

    # ---- customer actions ----
    @action(detail=True, methods=["post"], url_path=r"addons/(?P<item_id>\d+)/(?P<decision>approve|reject)")
    def addon_decision(self, request, pk=None, item_id=None, decision=None):
        self._require("customer")
        b = self.get_object()
        item = get_object_or_404(BookingItem, pk=item_id, booking=b, is_addon=True, addon_status="pending")
        item.addon_status = "approved" if decision == "approve" else "rejected"
        item.save(update_fields=["addon_status"])
        svc.recalc_total(b)
        return self._ok(b)

    @action(detail=True, methods=["post"], url_path="confirm-completion")
    def confirm_completion(self, request, pk=None):
        self._require("customer")
        b = self.get_object()
        if b.status != S.COMPLETION_REQUESTED:
            raise BusinessError("INVALID_STATUS_TRANSITION", "Work is not marked finished yet.")
        svc.complete(b, request.user, by_customer=True)
        return self._ok(b)

    @action(detail=True, methods=["post"], url_path="request-completion")
    def request_completion(self, request, pk=None):
        self._require("coworker", "partner")
        b = self.get_object()
        svc.transition(b, S.COMPLETION_REQUESTED, request.user, "Work finished, waiting for customer")
        svc.notify(b.customer, "completion_requested", "Work finished", f"Share end OTP {b.end_otp} or confirm in app.", booking_id=b.id)
        return self._ok(b)

    @action(detail=True, methods=["post"])
    def tip(self, request, pk=None):
        """Gap C1: customer creates a tip (then pays it with /payments/initiate/ purpose=tip)."""
        self._require("customer")
        from apps.payments.services import create_tip
        t = create_tip(self.get_object(), request.user, request.data.get("amount", 0))
        return Response({"tip_id": t.id, "amount": t.amount, "coworker": t.coworker.name if t.coworker else None}, status=201)

    @action(detail=True, methods=["get"], url_path="cancel-preview")
    def cancel_preview(self, request, pk=None):
        b = self.get_object()
        role = "admin" if request.user.is_superuser else request.user.user_type
        return Response({"fee": svc.cancellation_fee(b, role), "refund": max(b.amount_paid - svc.cancellation_fee(b, role), 0)})

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        self._require("customer", "partner", "admin")
        b = self.get_object()
        role = "admin" if request.user.is_superuser else request.user.user_type
        reason = request.data.get("reason", "").strip()
        if not reason:
            raise BusinessError("REASON_REQUIRED", "Please give a reason.")
        svc.cancel(b, request.user, role, reason)
        return self._ok(b)


class JobRequestViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    """Gap P1: broadcast job requests for the logged-in partner. First partner to accept gets the booking."""

    permission_classes = [IsPartner]
    serializer_class = JobRequestSerializer
    pagination_class = None

    def get_queryset(self):
        qs = BookingOffer.objects.filter(partner=get_partner(self.request)).select_related("booking__service")
        if self.request.query_params.get("all") != "1":
            qs = qs.filter(status=BookingOffer.Status.PENDING, expires_at__gt=timezone.now())
        return qs.prefetch_related("booking__items")

    def _offer(self, pk):
        return get_object_or_404(BookingOffer, pk=pk, partner=get_partner(self.request))

    @action(detail=True, methods=["post"])
    def accept(self, request, pk=None):
        booking = svc.accept_offer(self._offer(pk), request.user)
        return Response(BookingSerializer(booking, context={"request": request}).data)

    @action(detail=True, methods=["post"])
    def decline(self, request, pk=None):
        svc.decline_offer(self._offer(pk), request.user, request.data.get("reason", ""))
        return Response({"message": "Declined"})


class CoworkerJobsView(APIView):
    permission_classes = [IsCoworker]

    def get(self, request):
        qs = Booking.objects.filter(assignments__coworker__user=request.user, assignments__is_active=True).distinct()
        today = timezone.localdate()
        data = {
            "today": qs.filter(scheduled_start__date=today, status__in=OPEN_STATUSES),
            "upcoming": qs.filter(scheduled_start__date__gt=today, status__in=OPEN_STATUSES),
            "completed": qs.filter(status=S.COMPLETED)[:30],
        }
        profiles = Coworker.objects.filter(user=request.user).select_related("partner")
        from apps.payments.models import Tip
        return Response({
            "profile": [{"id": c.id, "name": c.name, "partner": c.partner.display_name, "status": c.status,
                         "verification_status": c.verification_status, "rating": c.rating_avg,
                         "jobs_completed": c.jobs_completed,
                         "tips_total": Tip.objects.filter(coworker=c, status="paid").aggregate(v=Sum("amount"))["v"] or 0}
                        for c in profiles],
            **{k: BookingListSerializer(v, many=True, context={"request": request}).data for k, v in data.items()},
        })


class PartnerDashboardView(APIView):
    permission_classes = [IsPartner]

    def get(self, request):
        p = get_partner(request)
        today = timezone.localdate()
        month_start = today.replace(day=1)
        qs = Booking.objects.filter(partner=p)
        from apps.payments.models import CommissionLedger
        from apps.payments.services import wallet_for
        earn = CommissionLedger.objects.filter(partner=p)
        sub = p.active_subscription()
        checks, live = p.go_live_checklist()
        return Response({
            "partner": {"id": p.id, "display_name": p.display_name, "verification_status": p.verification_status,
                        "rating": p.rating_avg, "rating_count": p.rating_count, "is_live": live},
            "pending_acceptance": qs.filter(status=S.PENDING_ACCEPTANCE).count(),
            "job_requests": BookingOffer.objects.filter(partner=p, status="pending", expires_at__gt=timezone.now()).count(),
            "today_jobs": qs.filter(scheduled_start__date=today).exclude(status__in=[S.CANCELLED]).count(),
            "in_progress": qs.filter(status__in=[S.ON_THE_WAY, S.ARRIVED, S.IN_PROGRESS, S.COMPLETION_REQUESTED]).count(),
            "completed_month": qs.filter(status=S.COMPLETED, completed_at__date__gte=month_start).count(),
            "cancelled_month": qs.filter(status=S.CANCELLED, updated_at__date__gte=month_start).count(),
            "earnings_today": earn.filter(created_at__date=today).aggregate(v=Sum("partner_net"))["v"] or 0,
            "earnings_month": earn.filter(created_at__date__gte=month_start).aggregate(v=Sum("partner_net"))["v"] or 0,
            "wallet_balance": wallet_for(p).balance,
            "subscription": {"plan": sub.plan.name, "end_date": sub.end_date, "days_left": sub.days_left,
                             "in_grace": sub.in_grace} if sub else None,
            "coworkers": {"total": p.coworkers.count(),
                          "approved": p.coworkers.filter(verification_status="approved", status="active").count()},
            "alerts": [label for label, ok in checks if not ok],
            "recent": BookingListSerializer(qs[:5], many=True, context={"request": request}).data,
        })


class AdminDashboardView(APIView):
    permission_classes = [IsPlatformAdmin]

    def get(self, request):
        from apps.accounts.models import User
        from apps.payments.models import CommissionLedger
        from apps.subscriptions.models import PartnerSubscription
        today = timezone.localdate()
        month_start = today.replace(day=1)
        b = Booking.objects.all()
        completed = b.filter(status=S.COMPLETED)
        by_status = dict(b.values_list("status").annotate(c=Count("id")))
        return Response({
            "gmv_month": completed.filter(completed_at__date__gte=month_start).aggregate(v=Sum("total"))["v"] or 0,
            "commission_month": CommissionLedger.objects.filter(created_at__date__gte=month_start).aggregate(v=Sum("commission_amount"))["v"] or 0,
            "subscription_revenue_month": PartnerSubscription.objects.filter(status__in=["active", "cancelled"], start_date__gte=month_start).aggregate(v=Sum("amount"))["v"] or 0,
            "bookings_today": b.filter(created_at__date=today).count(),
            "live_jobs": b.filter(status__in=[S.ON_THE_WAY, S.ARRIVED, S.IN_PROGRESS]).count(),
            "sla_breaches": b.filter(status__in=[S.PENDING_ACCEPTANCE, S.SEARCHING], sla_due_at__lt=timezone.now()).count(),
            "searching_now": b.filter(status=S.SEARCHING).count(),
            "pending_verifications": VerificationCase.objects.filter(status__in=["open", "in_review"]).count(),
            "partners": {"total": AreaPartner.objects.count(),
                         "approved": AreaPartner.objects.filter(verification_status="approved").count()},
            "customers": User.objects.filter(user_type="customer").count(),
            "coworkers": Coworker.objects.count(),
            "bookings_by_status": by_status,
            "last_7_days": [
                {"date": (today - timedelta(days=i)).isoformat(),
                 "bookings": b.filter(created_at__date=today - timedelta(days=i)).count()}
                for i in range(6, -1, -1)
            ],
        })
