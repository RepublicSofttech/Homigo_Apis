from rest_framework import serializers

from .models import Review


class ReviewSerializer(serializers.ModelSerializer):
    customer_name = serializers.SerializerMethodField()
    coworker_name = serializers.CharField(source="coworker.name", read_only=True, default=None)
    service_name = serializers.CharField(source="booking.service.name", read_only=True)

    class Meta:
        model = Review
        fields = "__all__"
        read_only_fields = ["customer", "partner", "coworker", "is_published", "moderation_note", "reply", "replied_at", "reported"]

    def get_customer_name(self, obj):
        # Privacy: first name + initial only
        u = obj.customer
        return f"{u.first_name} {u.last_name[:1]}.".strip() if u.first_name else "Customer"
