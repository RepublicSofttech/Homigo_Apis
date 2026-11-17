from rest_framework import serializers

from .models import Review, Testimonial


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


class TestimonialSerializer(serializers.ModelSerializer):
    class Meta:
        model = Testimonial
        fields = "__all__"

    def validate(self, attrs):
        published = attrs.get("is_published", getattr(self.instance, "is_published", False))
        consent = attrs.get("consent", getattr(self.instance, "consent", False))
        if published and not consent:
            raise serializers.ValidationError({"consent": "Get the person's consent before publishing their testimonial."})
        return attrs
