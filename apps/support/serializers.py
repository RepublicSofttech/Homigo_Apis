from rest_framework import serializers

from .models import Ticket, TicketMessage


class TicketMessageSerializer(serializers.ModelSerializer):
    sender_name = serializers.CharField(source="sender.full_name", read_only=True)
    is_staff = serializers.SerializerMethodField()

    class Meta:
        model = TicketMessage
        fields = ["id", "sender_name", "is_staff", "message", "created_at"]

    def get_is_staff(self, obj):
        return obj.sender.user_type == "admin"


class TicketSerializer(serializers.ModelSerializer):
    messages = TicketMessageSerializer(many=True, read_only=True)
    first_message = serializers.CharField(write_only=True)
    booking_no = serializers.CharField(source="booking.booking_no", read_only=True, default=None)
    user_name = serializers.CharField(source="user.full_name", read_only=True)

    class Meta:
        model = Ticket
        fields = "__all__"
        read_only_fields = ["user", "status", "priority", "assigned_to", "resolution"]
