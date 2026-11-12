from rest_framework import serializers

from .models import PartnerSubscription, SubscriptionPlan


class PlanSerializer(serializers.ModelSerializer):
    class Meta:
        model = SubscriptionPlan
        fields = "__all__"


class PartnerSubscriptionSerializer(serializers.ModelSerializer):
    plan_detail = PlanSerializer(source="plan", read_only=True)
    days_left = serializers.IntegerField(read_only=True)
    in_grace = serializers.BooleanField(read_only=True)
    partner_name = serializers.CharField(source="partner.display_name", read_only=True)

    class Meta:
        model = PartnerSubscription
        fields = "__all__"
        read_only_fields = ["partner", "status", "start_date", "end_date", "amount", "invoice_no"]
