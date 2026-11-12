from rest_framework import serializers

from apps.catalogue.models import BOOKING_TYPES

from .models import Booking, BookingItem, BookingStatusHistory


class ItemInputSerializer(serializers.Serializer):
    service_item = serializers.IntegerField()
    quantity = serializers.IntegerField(min_value=1, default=1)


class PricePreviewSerializer(serializers.Serializer):
    service_id = serializers.IntegerField()
    partner_id = serializers.IntegerField()
    items = ItemInputSerializer(many=True)
    coupon_code = serializers.CharField(required=False, allow_blank=True, default="")


class BookingCreateSerializer(PricePreviewSerializer):
    partner_id = serializers.IntegerField(required=False)
    address_id = serializers.IntegerField()
    scheduled_start = serializers.DateTimeField()
    booking_type = serializers.ChoiceField(choices=[c for c, _ in BOOKING_TYPES], default="scheduled")
    payment_mode = serializers.ChoiceField(choices=["online", "cash"], default="cash")
    notes = serializers.CharField(required=False, allow_blank=True, default="")
    answers = serializers.DictField(required=False, default=dict)


class BookingItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = BookingItem
        fields = ["id", "service_item", "name", "quantity", "unit_price", "total", "is_addon", "is_material", "addon_status"]


class HistorySerializer(serializers.ModelSerializer):
    actor_name = serializers.CharField(source="actor.full_name", read_only=True, default="System")

    class Meta:
        model = BookingStatusHistory
        fields = ["from_status", "to_status", "actor_name", "note", "created_at"]


class BookingSerializer(serializers.ModelSerializer):
    """Role-aware: customers see OTPs, coworkers see masked phone, partners see full details."""

    items = BookingItemSerializer(many=True, read_only=True)
    service_name = serializers.CharField(source="service.name", read_only=True)
    partner_name = serializers.CharField(source="partner.display_name", read_only=True)
    partner_rating = serializers.DecimalField(source="partner.rating_avg", max_digits=3, decimal_places=2, read_only=True)
    customer_name = serializers.CharField(source="customer.full_name", read_only=True)
    customer_phone = serializers.SerializerMethodField()
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    coworkers = serializers.SerializerMethodField()
    history = HistorySerializer(many=True, read_only=True)
    has_review = serializers.SerializerMethodField()

    class Meta:
        model = Booking
        exclude = ["rejected_partner_ids"]

    def _role(self):
        req = self.context.get("request")
        return getattr(getattr(req, "user", None), "user_type", None)

    def get_customer_phone(self, obj):
        role = self._role()
        if role == "coworker":
            return "XXXXXX" + obj.customer.phone[-4:]
        return obj.customer.phone

    def get_coworkers(self, obj):
        return [{"id": c.id, "name": c.name, "phone": c.phone if self._role() != "customer" else None,
                 "rating": c.rating_avg, "photo": c.photo.url if c.photo else None} for c in obj.active_coworkers]

    def get_has_review(self, obj):
        return hasattr(obj, "review")

    def to_representation(self, obj):
        data = super().to_representation(obj)
        if self._role() != "customer":
            data.pop("start_otp", None)
            data.pop("end_otp", None)
        return data


class BookingListSerializer(BookingSerializer):
    class Meta(BookingSerializer.Meta):
        exclude = ["rejected_partner_ids", "answers"]
