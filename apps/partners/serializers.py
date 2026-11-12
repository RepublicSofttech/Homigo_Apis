from rest_framework import serializers

from apps.catalogue.models import Service
from apps.core.utils import mask_number, sha256

from .models import (
    AreaPartner, Coworker, CoworkerDocument, PartnerBankAccount, PartnerDocument, PartnerService,
    RateCardItem, VerificationCase,
)


class PartnerProfileSerializer(serializers.ModelSerializer):
    pan_number = serializers.CharField(write_only=True, required=False, allow_blank=True)
    service_ids = serializers.SerializerMethodField()

    class Meta:
        model = AreaPartner
        exclude = ["owner", "services"]
        read_only_fields = [
            "verification_status", "account_status", "rejection_reason", "rating_avg", "rating_count",
            "jobs_completed", "pan_masked", "created_at", "updated_at",
        ]

    def get_service_ids(self, obj):
        return list(obj.partnerservice_set.filter(is_active=True).values_list("service_id", flat=True))

    def validate_gstin(self, value):
        value = value.strip().upper()
        if value and len(value) != 15:
            raise serializers.ValidationError("GSTIN must be 15 characters.")
        return value

    def _apply_pan(self, validated):
        pan = validated.pop("pan_number", None)
        if pan:
            validated["pan_masked"] = mask_number(pan.upper(), 4)
        return validated

    def create(self, validated):
        return super().create(self._apply_pan(validated))

    def update(self, instance, validated):
        return super().update(instance, self._apply_pan(validated))


class PublicPartnerSerializer(serializers.ModelSerializer):
    verified = serializers.BooleanField(source="is_verified")
    verified_coworkers = serializers.SerializerMethodField()
    services = serializers.SerializerMethodField()

    class Meta:
        model = AreaPartner
        fields = [
            "id", "display_name", "description", "logo", "years_in_business", "city", "languages",
            "rating_avg", "rating_count", "jobs_completed", "verified", "verified_coworkers", "services",
            "working_hours_start", "working_hours_end",
        ]

    def get_verified_coworkers(self, obj):
        return obj.coworkers.filter(verification_status="approved", status="active").count()

    def get_services(self, obj):
        return list(obj.services.filter(partnerservice__is_active=True).values("id", "name"))


class PartnerServiceSerializer(serializers.ModelSerializer):
    service_name = serializers.CharField(source="service.name", read_only=True)

    class Meta:
        model = PartnerService
        fields = ["id", "service", "service_name", "is_active", "created_at"]


class PartnerDocumentSerializer(serializers.ModelSerializer):
    number = serializers.CharField(write_only=True, required=False, allow_blank=True)

    class Meta:
        model = PartnerDocument
        exclude = ["partner"]
        read_only_fields = ["status", "reason", "number_masked"]

    def validate(self, attrs):
        number = attrs.pop("number", None)
        if number:
            attrs["number_masked"] = mask_number(number, 4)  # Aadhaar etc: keep last 4 only
        f = attrs.get("file")
        if f and f.size > 5 * 1024 * 1024:
            raise serializers.ValidationError({"file": "Max file size is 5 MB."})
        return attrs


class BankAccountSerializer(serializers.ModelSerializer):
    account_number = serializers.CharField(write_only=True)

    class Meta:
        model = PartnerBankAccount
        exclude = ["partner", "account_hash"]
        read_only_fields = ["account_masked", "is_verified"]

    def validate_ifsc(self, value):
        value = value.strip().upper()
        if len(value) != 11:
            raise serializers.ValidationError("IFSC must be 11 characters.")
        return value

    def validate(self, attrs):
        acc = attrs.pop("account_number").strip()
        if not acc.isdigit() or not 9 <= len(acc) <= 18:
            raise serializers.ValidationError({"account_number": "Enter a valid account number."})
        attrs["account_masked"] = mask_number(acc, 4)
        attrs["account_hash"] = sha256(acc)
        attrs["is_verified"] = False  # any change needs re-verification
        return attrs


class RateCardItemSerializer(serializers.ModelSerializer):
    item_name = serializers.CharField(source="service_item.name", read_only=True)
    service_id = serializers.IntegerField(source="service_item.service_id", read_only=True)
    service_name = serializers.CharField(source="service_item.service.name", read_only=True)
    unit_label = serializers.CharField(source="service_item.unit_label", read_only=True)
    price_floor = serializers.DecimalField(source="service_item.price_floor", max_digits=10, decimal_places=2, read_only=True)
    price_ceiling = serializers.DecimalField(source="service_item.price_ceiling", max_digits=10, decimal_places=2, read_only=True)

    class Meta:
        model = RateCardItem
        exclude = ["partner"]

    def validate(self, attrs):
        item = attrs.get("service_item") or self.instance.service_item
        price = attrs.get("price", getattr(self.instance, "price", None))
        if item.price_floor is not None and price < item.price_floor:
            raise serializers.ValidationError({"price": f"PRICE_OUT_OF_RANGE: minimum is {item.price_floor}"})
        if item.price_ceiling is not None and price > item.price_ceiling:
            raise serializers.ValidationError({"price": f"PRICE_OUT_OF_RANGE: maximum is {item.price_ceiling}"})
        return attrs


class CoworkerDocumentSerializer(serializers.ModelSerializer):
    number = serializers.CharField(write_only=True, required=False, allow_blank=True)

    class Meta:
        model = CoworkerDocument
        fields = "__all__"
        read_only_fields = ["status", "reason", "number_masked"]

    def validate(self, attrs):
        number = attrs.pop("number", None)
        if number:
            attrs["number_masked"] = mask_number(number, 4)
        return attrs


class CoworkerSerializer(serializers.ModelSerializer):
    skill_ids = serializers.PrimaryKeyRelatedField(source="skills", many=True, queryset=Service.objects.all(), required=False)
    skill_names = serializers.SerializerMethodField()
    id_number = serializers.CharField(write_only=True, required=False, allow_blank=True)
    documents = CoworkerDocumentSerializer(many=True, read_only=True)
    partner_name = serializers.CharField(source="partner.display_name", read_only=True)

    class Meta:
        model = Coworker
        exclude = ["user", "skills", "id_number_hash"]
        read_only_fields = [
            "partner", "verification_status", "status", "rejection_reason", "rating_avg", "rating_count",
            "jobs_completed", "id_number_masked",
        ]

    def get_skill_names(self, obj):
        return [s.name for s in obj.skills.all()]

    def validate_phone(self, value):
        from apps.accounts.serializers import validate_phone
        return validate_phone(value)

    def validate(self, attrs):
        id_number = attrs.pop("id_number", None)
        if id_number:
            attrs["id_number_hash"] = sha256(id_number.strip().upper())
            attrs["id_number_masked"] = mask_number(id_number.strip(), 4)
        return attrs


class PublicCoworkerSerializer(serializers.ModelSerializer):
    class Meta:
        model = Coworker
        fields = ["id", "name", "photo", "gender", "languages", "experience_years", "skill_level", "rating_avg", "rating_count", "jobs_completed"]


class VerificationCaseSerializer(serializers.ModelSerializer):
    subject_name = serializers.SerializerMethodField()
    partner_detail = serializers.SerializerMethodField()
    coworker_detail = serializers.SerializerMethodField()
    assigned_to_name = serializers.CharField(source="assigned_to.full_name", read_only=True, default=None)

    class Meta:
        model = VerificationCase
        fields = "__all__"

    def get_subject_name(self, obj):
        return obj.partner.display_name if obj.subject_type == "partner" else obj.coworker.name

    def get_partner_detail(self, obj):
        if obj.subject_type != "partner":
            return None
        p = obj.partner
        data = PartnerProfileSerializer(p, context=self.context).data
        data["documents"] = PartnerDocumentSerializer(p.documents.all(), many=True, context=self.context).data
        bank = getattr(p, "bank_account", None)
        data["bank_account"] = BankAccountSerializer(bank, context=self.context).data if bank else None
        data["owner_phone"] = p.owner.phone
        return data

    def get_coworker_detail(self, obj):
        if obj.subject_type != "coworker":
            return None
        return CoworkerSerializer(obj.coworker, context=self.context).data


class AdminPartnerSerializer(PartnerProfileSerializer):
    owner_phone = serializers.CharField(source="owner.phone", read_only=True)
    owner_name = serializers.CharField(source="owner.full_name", read_only=True)
    coworker_count = serializers.IntegerField(source="coworkers.count", read_only=True)
    subscription = serializers.SerializerMethodField()

    def get_subscription(self, obj):
        sub = obj.active_subscription()
        return {"plan": sub.plan.name, "end_date": sub.end_date, "status": sub.status} if sub else None
