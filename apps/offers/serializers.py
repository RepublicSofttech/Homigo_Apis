from rest_framework import serializers

from .models import Offer


class OfferSerializer(serializers.ModelSerializer):
    service_name = serializers.CharField(source="service.name", read_only=True, default=None)
    partner_name = serializers.CharField(source="partner.display_name", read_only=True, default=None)

    class Meta:
        model = Offer
        fields = "__all__"
        read_only_fields = ["used_count"]

    def validate(self, attrs):
        vf = attrs.get("valid_from", getattr(self.instance, "valid_from", None))
        vt = attrs.get("valid_to", getattr(self.instance, "valid_to", None))
        if vf and vt and vf >= vt:
            raise serializers.ValidationError({"valid_to": "End must be after start."})
        if attrs.get("discount_type") == "percent" and attrs.get("value", 0) > 100:
            raise serializers.ValidationError({"value": "Percentage cannot exceed 100."})
        return attrs


class PartnerOfferSerializer(OfferSerializer):
    class Meta(OfferSerializer.Meta):
        read_only_fields = ["used_count", "partner", "funded_by", "partner_share_percent", "category"]


class CouponValidateSerializer(serializers.Serializer):
    code = serializers.CharField()
    service_id = serializers.IntegerField()
    partner_id = serializers.IntegerField(required=False)
    order_value = serializers.DecimalField(max_digits=10, decimal_places=2)
