from rest_framework import serializers

from .models import AppSetting, AuditLog


class AppSettingSerializer(serializers.ModelSerializer):
    class Meta:
        model = AppSetting
        fields = ["id", "key", "value", "description", "updated_at"]


class AuditLogSerializer(serializers.ModelSerializer):
    actor_name = serializers.CharField(source="actor.__str__", read_only=True, default=None)

    class Meta:
        model = AuditLog
        fields = ["id", "actor", "actor_name", "action", "entity", "entity_id", "data", "created_at"]
