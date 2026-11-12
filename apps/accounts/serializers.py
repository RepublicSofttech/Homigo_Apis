import re

from rest_framework import serializers

from .models import Address, User

PHONE_RE = re.compile(r"^[6-9]\d{9}$")


def validate_phone(value):
    value = value.strip().replace(" ", "")
    if value.startswith("+91"):
        value = value[3:]
    if not PHONE_RE.match(value):
        raise serializers.ValidationError("Enter a valid 10-digit Indian mobile number.")
    return value


class OTPSendSerializer(serializers.Serializer):
    phone = serializers.CharField()

    def validate_phone(self, value):
        return validate_phone(value)


class OTPVerifySerializer(serializers.Serializer):
    phone = serializers.CharField()
    otp = serializers.CharField(max_length=6)
    user_type = serializers.ChoiceField(choices=["customer", "partner"], required=False, default="customer")
    first_name = serializers.CharField(required=False, allow_blank=True)
    last_name = serializers.CharField(required=False, allow_blank=True)

    def validate_phone(self, value):
        return validate_phone(value)


class PasswordLoginSerializer(serializers.Serializer):
    phone = serializers.CharField()
    password = serializers.CharField()


class UserSerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(read_only=True)

    class Meta:
        model = User
        fields = [
            "id", "phone", "first_name", "last_name", "full_name", "email", "gender", "date_of_birth",
            "avatar", "language", "user_type", "admin_role", "referral_code", "date_joined",
        ]
        read_only_fields = ["id", "phone", "user_type", "admin_role", "referral_code", "date_joined", "avatar"]


class AvatarSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["avatar"]


class AddressSerializer(serializers.ModelSerializer):
    class Meta:
        model = Address
        exclude = ["user"]
        read_only_fields = ["id", "created_at", "updated_at"]


class AdminUserSerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(read_only=True)
    password = serializers.CharField(write_only=True, required=False, allow_blank=True, min_length=8,
                                     help_text="Only for admin staff (password login)")

    def validate_phone(self, value):
        return validate_phone(value)

    def update(self, instance, validated):
        password = validated.pop("password", None)
        user = super().update(instance, validated)
        if password:
            user.set_password(password)
            user.save(update_fields=["password"])
        return user

    class Meta:
        model = User
        fields = [
            "id", "phone", "first_name", "last_name", "full_name", "email", "user_type", "admin_role",
            "is_active", "date_joined", "last_login", "password",
        ]
        read_only_fields = ["id", "date_joined", "last_login"]
