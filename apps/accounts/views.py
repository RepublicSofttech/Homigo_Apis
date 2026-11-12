from django.conf import settings
from django.contrib.auth import authenticate
from django.db import transaction
from knox.models import AuthToken
from rest_framework import generics, mixins, permissions, status, viewsets
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.core.exceptions import BusinessError
from apps.core.permissions import IsPlatformAdmin
from apps.core.utils import random_otp

from .models import Address, User
from .serializers import (
    AddressSerializer, AdminUserSerializer, AvatarSerializer, OTPSendSerializer,
    OTPVerifySerializer, PasswordLoginSerializer, UserSerializer,
)
from .services import check_otp, issue_otp


def token_response(user, created=False):
    token = AuthToken.objects.create(user)[1]
    return Response(
        {"token": token, "user": UserSerializer(user).data, "is_new_user": created},
        status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
    )


class OTPSendView(APIView):
    serializer_class = OTPSendSerializer
    permission_classes = [permissions.AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "otp"

    def post(self, request):
        s = OTPSendSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        phone = s.validated_data["phone"]
        code = issue_otp(phone)
        data = {"message": "OTP sent", "is_registered": User.objects.filter(phone=phone).exists()}
        if settings.OTP_DEBUG:
            data["debug_otp"] = code  # local development only
        return Response(data)


class OTPVerifyView(APIView):
    serializer_class = OTPVerifySerializer
    permission_classes = [permissions.AllowAny]

    @transaction.atomic
    def post(self, request):
        s = OTPVerifySerializer(data=request.data)
        s.is_valid(raise_exception=True)
        d = s.validated_data
        check_otp(d["phone"], d["otp"])
        user = User.objects.filter(phone=d["phone"]).first()
        created = False
        if user is None:
            user = User.objects.create_user(
                phone=d["phone"], user_type=d["user_type"],
                first_name=d.get("first_name", ""), last_name=d.get("last_name", ""),
                referral_code=random_otp(6),
            )
            created = True
        if not user.is_active:
            raise BusinessError("ACCOUNT_DISABLED", "Your account is disabled.", 403)
        user.is_phone_verified = True
        user.save(update_fields=["is_phone_verified"])
        return token_response(user, created)


class PasswordLoginView(APIView):
    """Password login for admin staff (customers/partners/coworkers use OTP)."""

    serializer_class = PasswordLoginSerializer

    permission_classes = [permissions.AllowAny]

    def post(self, request):
        s = PasswordLoginSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        user = authenticate(request, phone=s.validated_data["phone"], password=s.validated_data["password"])
        if not user:
            raise BusinessError("INVALID_CREDENTIALS", "Invalid phone or password.", 400)
        return token_response(user)


class LogoutView(APIView):
    def post(self, request):
        request._auth.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class MeView(generics.RetrieveUpdateAPIView):
    serializer_class = UserSerializer

    def get_object(self):
        return self.request.user


class AvatarView(generics.UpdateAPIView):
    serializer_class = AvatarSerializer
    parser_classes = [MultiPartParser, FormParser]

    def get_object(self):
        return self.request.user


class AddressViewSet(viewsets.ModelViewSet):
    serializer_class = AddressSerializer
    pagination_class = None

    def get_queryset(self):
        return Address.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        is_first = not Address.objects.filter(user=self.request.user).exists()
        addr = serializer.save(user=self.request.user, is_default=serializer.validated_data.get("is_default") or is_first)
        self._single_default(addr)

    def perform_update(self, serializer):
        self._single_default(serializer.save())

    def _single_default(self, addr):
        if addr.is_default:
            Address.objects.filter(user=addr.user).exclude(pk=addr.pk).update(is_default=False)


class AdminUserViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.UpdateModelMixin,
                       mixins.CreateModelMixin, viewsets.GenericViewSet):
    permission_classes = [IsPlatformAdmin]
    serializer_class = AdminUserSerializer
    queryset = User.objects.all().order_by("-date_joined")
    filterset_fields = ["user_type", "admin_role", "is_active"]
    search_fields = ["phone", "first_name", "last_name", "email"]

    def perform_create(self, serializer):
        data = dict(serializer.validated_data)
        password = data.pop("password", None) or None
        if data.get("user_type") == "admin":
            data["is_staff"] = True
        serializer.instance = User.objects.create_user(password=password, **data)
