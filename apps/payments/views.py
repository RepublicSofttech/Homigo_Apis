import json

from django.conf import settings
from django.db.models import Sum
from django.shortcuts import get_object_or_404
from rest_framework import mixins, permissions, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.bookings.models import Booking
from apps.core.config_defaults import get_setting
from apps.core.exceptions import BusinessError
from apps.core.permissions import IsPartner, IsPlatformAdmin
from apps.partners.views import get_partner
from apps.subscriptions.models import PartnerSubscription

from . import gateway
from .models import CommissionLedger, Payment, Settlement
from .serializers import (
    CommissionSerializer, InitiatePaymentSerializer, PaymentSerializer, SettlementSerializer,
    VerifyPaymentSerializer, WalletSerializer, WalletTransactionSerializer,
)
from .services import mark_settlement_paid, mark_success, run_settlements, wallet_for


class InitiatePaymentView(APIView):
    serializer_class = InitiatePaymentSerializer
    def post(self, request):
        s = InitiatePaymentSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        d = s.validated_data
        kwargs = {"user": request.user, "purpose": d["purpose"], "gateway": settings.PAYMENT_GATEWAY}
        if d["purpose"] == "booking":
            b = get_object_or_404(Booking, pk=d.get("booking_id"), customer=request.user)
            if b.payment_mode != "online" or b.payment_status == "paid":
                raise BusinessError("NOTHING_TO_PAY", "Nothing to pay for this booking.")
            if b.status == "cancelled":
                raise BusinessError("BOOKING_CANCELLED", "Booking is cancelled.")
            kwargs.update(booking=b, amount=b.total - b.amount_paid)
        elif d["purpose"] == "subscription":
            sub = get_object_or_404(PartnerSubscription, pk=d.get("subscription_id"), partner=get_partner(request))
            if sub.status != "pending_payment":
                raise BusinessError("NOTHING_TO_PAY", "Subscription is not awaiting payment.")
            kwargs.update(subscription=sub, amount=sub.amount)
        else:
            get_partner(request)
            if not d.get("amount") or d["amount"] <= 0:
                raise BusinessError("INVALID_AMOUNT", "Enter a valid amount.")
            kwargs.update(amount=d["amount"])
        payment = Payment.objects.create(**kwargs)
        payment.gateway_order_id = gateway.create_order(payment)
        payment.save(update_fields=["gateway_order_id"])
        return Response({
            "payment_id": payment.id, "gateway": payment.gateway, "order_id": payment.gateway_order_id,
            "amount": payment.amount, "currency": "INR", "key_id": settings.RAZORPAY_KEY_ID,
        })


class VerifyPaymentView(APIView):
    """Checkout callback. For mock gateway it simply confirms; for Razorpay it checks the HMAC signature."""

    serializer_class = VerifyPaymentSerializer

    def post(self, request):
        s = VerifyPaymentSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        d = s.validated_data
        payment = get_object_or_404(Payment, pk=d["payment_id"], user=request.user)
        if payment.gateway == "mock":
            if settings.PAYMENT_GATEWAY != "mock":
                raise BusinessError("MOCK_DISABLED", "Mock payments are disabled.", 403)
            mark_success(payment, f"mock_pay_{payment.pk}")
        else:
            ok = gateway.verify_checkout_signature(payment.gateway_order_id, d.get("razorpay_payment_id"), d.get("razorpay_signature"))
            if not ok:
                payment.status = "failed"
                payment.save(update_fields=["status"])
                raise BusinessError("PAYMENT_SIGNATURE_INVALID", "Payment verification failed.", 400)
            mark_success(payment, d["razorpay_payment_id"])
        payment.refresh_from_db()
        return Response(PaymentSerializer(payment).data)


class RazorpayWebhookView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def post(self, request):
        if not gateway.verify_webhook_signature(request.body, request.headers.get("X-Razorpay-Signature")):
            raise BusinessError("INVALID_SIGNATURE", "Invalid webhook signature.", 400)
        event = json.loads(request.body)
        if event.get("event") == "payment.captured":
            entity = event["payload"]["payment"]["entity"]
            payment = Payment.objects.filter(gateway_order_id=entity.get("order_id")).first()
            if payment:
                mark_success(payment, entity.get("id"), raw=entity)
        return Response({"ok": True})


class MyPaymentsView(mixins.ListModelMixin, viewsets.GenericViewSet):
    serializer_class = PaymentSerializer

    def get_queryset(self):
        return Payment.objects.filter(user=self.request.user).select_related("booking")


class PartnerEarningsView(APIView):
    permission_classes = [IsPartner]

    def get(self, request):
        partner = get_partner(request)
        wallet = wallet_for(partner)
        agg = CommissionLedger.objects.filter(partner=partner).aggregate(
            gross=Sum("service_value"), commission=Sum("commission_amount"), gst=Sum("gst_on_commission"), net=Sum("partner_net"))
        return Response({
            "wallet": WalletSerializer(wallet).data,
            "cash_limit": get_setting("cash_limit"),
            "totals": {k: v or 0 for k, v in agg.items()},
            "transactions": WalletTransactionSerializer(wallet.transactions.all()[:50], many=True).data,
            "commissions": CommissionSerializer(CommissionLedger.objects.filter(partner=partner)[:50], many=True).data,
            "settlements": SettlementSerializer(partner.settlements.all()[:20], many=True).data,
        })


class AdminPaymentViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    permission_classes = [IsPlatformAdmin]
    serializer_class = PaymentSerializer
    queryset = Payment.objects.select_related("booking").all()
    filterset_fields = ["status", "purpose", "gateway"]


class AdminSettlementViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    permission_classes = [IsPlatformAdmin]
    serializer_class = SettlementSerializer
    queryset = Settlement.objects.select_related("partner").all()
    filterset_fields = ["status", "partner"]

    @action(detail=False, methods=["post"])
    def run(self, request):
        created = run_settlements(request.user)
        return Response({"created": len(created), "settlements": SettlementSerializer(created, many=True).data})

    @action(detail=True, methods=["post"])
    def pay(self, request, pk=None):
        utr = request.data.get("utr", "").strip()
        if not utr:
            raise BusinessError("UTR_REQUIRED", "Enter the bank UTR / reference number.")
        return Response(SettlementSerializer(mark_settlement_paid(self.get_object(), utr, request.user)).data)


class AdminCommissionViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    permission_classes = [IsPlatformAdmin]
    serializer_class = CommissionSerializer
    queryset = CommissionLedger.objects.select_related("booking", "partner").all()
    filterset_fields = ["partner", "payment_mode"]
