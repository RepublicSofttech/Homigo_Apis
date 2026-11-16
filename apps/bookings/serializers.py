from rest_framework import serializers

from apps.catalogue.models import BOOKING_TYPES

from .models import Booking, BookingItem, BookingStatusHistory


class ItemInputSerializer(serializers.Serializer):
    service_item = serializers.IntegerField()
    quantity = serializers.IntegerField(min_value=1, default=1)


class PricePreviewSerializer(serializers.Serializer):
    service_id = serializers.IntegerField()
    partner_id = serializers.IntegerField(required=False, allow_null=True,
                                          help_text="Empty = platform price for 'nearest available' (broadcast) booking")
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
    partner_name = serializers.CharField(source="partner.display_name", read_only=True, default=None)
    partner_rating = serializers.DecimalField(source="partner.rating_avg", max_digits=3, decimal_places=2, read_only=True,
                                              default=None)
    searching = serializers.SerializerMethodField()
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

    def get_searching(self, obj):
        if obj.status != "searching":
            return None
        return {"round": obj.broadcast_round, "partners_notified": obj.offers.count(),
                "round_ends_at": obj.sla_due_at}

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


class JobRequestSerializer(serializers.ModelSerializer):
    """What a partner sees for a broadcast offer: enough to decide, no exact address or phone (privacy)."""

    booking_id = serializers.IntegerField(source="booking.id", read_only=True)
    booking_no = serializers.CharField(source="booking.booking_no", read_only=True)
    service_name = serializers.CharField(source="booking.service.name", read_only=True)
    scheduled_start = serializers.DateTimeField(source="booking.scheduled_start", read_only=True)
    scheduled_end = serializers.DateTimeField(source="booking.scheduled_end", read_only=True)
    area = serializers.SerializerMethodField()
    items = serializers.SerializerMethodField()
    payment_mode = serializers.CharField(source="booking.payment_mode", read_only=True)
    job_value = serializers.SerializerMethodField()
    notes = serializers.CharField(source="booking.notes", read_only=True)

    class Meta:
        from .models import BookingOffer
        model = BookingOffer
        fields = ["id", "booking_id", "booking_no", "service_name", "scheduled_start", "scheduled_end", "area", "items",
                  "payment_mode", "job_value", "notes", "distance_km", "round", "status", "expires_at", "created_at"]

    def get_area(self, obj):
        a = obj.booking.address
        return ", ".join(x for x in [a.get("line2"), a.get("city"), a.get("pincode")] if x)

    def get_items(self, obj):
        return [{"name": i.name, "quantity": i.quantity, "total": i.total} for i in obj.booking.items.all()]

    def get_job_value(self, obj):
        b = obj.booking
        return b.subtotal + b.visit_charge - b.partner_discount_share
