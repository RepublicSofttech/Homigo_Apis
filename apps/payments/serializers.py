from rest_framework import serializers

from .models import CommissionLedger, PartnerWallet, Payment, Refund, Settlement, WalletTransaction


class PaymentSerializer(serializers.ModelSerializer):
    booking_no = serializers.CharField(source="booking.booking_no", read_only=True, default=None)

    class Meta:
        model = Payment
        exclude = ["raw"]


class InitiatePaymentSerializer(serializers.Serializer):
    purpose = serializers.ChoiceField(choices=["booking", "subscription", "wallet_topup", "tip"])
    tip_id = serializers.IntegerField(required=False)
    booking_id = serializers.IntegerField(required=False)
    subscription_id = serializers.IntegerField(required=False)
    amount = serializers.DecimalField(max_digits=10, decimal_places=2, required=False)


class VerifyPaymentSerializer(serializers.Serializer):
    payment_id = serializers.IntegerField()
    razorpay_order_id = serializers.CharField(required=False)
    razorpay_payment_id = serializers.CharField(required=False)
    razorpay_signature = serializers.CharField(required=False)


class WalletTransactionSerializer(serializers.ModelSerializer):
    booking_no = serializers.CharField(source="booking.booking_no", read_only=True, default=None)

    class Meta:
        model = WalletTransaction
        fields = "__all__"


class WalletSerializer(serializers.ModelSerializer):
    class Meta:
        model = PartnerWallet
        fields = ["balance", "updated_at"]


class CommissionSerializer(serializers.ModelSerializer):
    booking_no = serializers.CharField(source="booking.booking_no", read_only=True)
    partner_name = serializers.CharField(source="partner.display_name", read_only=True)

    class Meta:
        model = CommissionLedger
        fields = "__all__"


class SettlementSerializer(serializers.ModelSerializer):
    partner_name = serializers.CharField(source="partner.display_name", read_only=True)

    class Meta:
        model = Settlement
        fields = "__all__"


class RefundSerializer(serializers.ModelSerializer):
    class Meta:
        model = Refund
        fields = "__all__"
