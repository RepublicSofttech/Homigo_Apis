from rest_framework import serializers

from .models import BOOKING_TYPES, Service, ServiceCategory, ServiceItem

BT_CODES = {c for c, _ in BOOKING_TYPES}


class ServiceItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = ServiceItem
        fields = "__all__"

    def validate(self, attrs):
        floor, ceil = attrs.get("price_floor"), attrs.get("price_ceiling")
        if floor and ceil and floor > ceil:
            raise serializers.ValidationError({"price_floor": "Floor cannot be more than ceiling."})
        return attrs


class ServiceSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source="category.name", read_only=True)
    category_icon = serializers.CharField(source="category.icon", read_only=True)
    starting_price = serializers.SerializerMethodField()

    class Meta:
        model = Service
        fields = "__all__"
        read_only_fields = ["slug"]

    def get_starting_price(self, obj):
        prices = [i.default_price for i in obj.items.all() if i.is_active]
        return min(prices) if prices else None

    def validate_booking_types(self, value):
        bad = set(value) - BT_CODES
        if bad:
            raise serializers.ValidationError(f"Unknown booking types: {', '.join(bad)}")
        return value


class ServiceDetailSerializer(ServiceSerializer):
    items = serializers.SerializerMethodField()

    def get_items(self, obj):
        return ServiceItemSerializer(obj.items.filter(is_active=True), many=True).data


class ServiceCategorySerializer(serializers.ModelSerializer):
    service_count = serializers.SerializerMethodField()

    class Meta:
        model = ServiceCategory
        fields = "__all__"
        read_only_fields = ["slug"]

    def get_service_count(self, obj):
        return obj.services.filter(is_active=True).count()
