from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models
from django.utils import timezone

from apps.core.models import TimeStampedModel


class UserManager(BaseUserManager):
    use_in_migrations = True

    def create_user(self, phone, password=None, **extra):
        if not phone:
            raise ValueError("Phone is required")
        extra.setdefault("username", phone)
        user = self.model(phone=phone, **extra)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_superuser(self, phone, password=None, **extra):
        extra.update(is_staff=True, is_superuser=True, user_type=User.UserType.ADMIN, admin_role="super_admin")
        return self.create_user(phone, password, **extra)


class User(AbstractUser):
    class UserType(models.TextChoices):
        CUSTOMER = "customer", "Customer"
        PARTNER = "partner", "Area Partner"
        COWORKER = "coworker", "Coworker"
        ADMIN = "admin", "Admin"

    class Gender(models.TextChoices):
        MALE = "male", "Male"
        FEMALE = "female", "Female"
        OTHER = "other", "Other"

    ADMIN_ROLES = [
        ("super_admin", "Super Admin"),
        ("verification_officer", "Verification Officer"),
        ("operations", "Operations / City Manager"),
        ("support", "Support Agent"),
        ("finance", "Finance"),
        ("marketing", "Marketing"),
    ]

    phone = models.CharField(max_length=15, unique=True)
    user_type = models.CharField(max_length=20, choices=UserType.choices, default=UserType.CUSTOMER)
    admin_role = models.CharField(max_length=30, choices=ADMIN_ROLES, blank=True)
    gender = models.CharField(max_length=10, choices=Gender.choices, blank=True)
    date_of_birth = models.DateField(null=True, blank=True)
    avatar = models.ImageField(upload_to="avatars/", null=True, blank=True)
    language = models.CharField(max_length=10, default="en")
    referral_code = models.CharField(max_length=12, blank=True)
    is_phone_verified = models.BooleanField(default=False)

    USERNAME_FIELD = "phone"
    REQUIRED_FIELDS = ["username"]
    objects = UserManager()

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}".strip() or self.phone

    def __str__(self):
        return self.full_name


class OTPRequest(models.Model):
    phone = models.CharField(max_length=15, db_index=True)
    code_hash = models.CharField(max_length=64)
    expires_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)
    used = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def is_valid(self):
        return not self.used and self.expires_at > timezone.now()


class Address(TimeStampedModel):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="addresses")
    label = models.CharField(max_length=30, default="Home")
    line1 = models.CharField(max_length=255)
    line2 = models.CharField(max_length=255, blank=True)
    landmark = models.CharField(max_length=120, blank=True)
    city = models.CharField(max_length=100)
    state = models.CharField(max_length=100, blank=True)
    pincode = models.CharField(max_length=10)
    latitude = models.DecimalField(max_digits=9, decimal_places=6)
    longitude = models.DecimalField(max_digits=9, decimal_places=6)
    is_default = models.BooleanField(default=False)

    class Meta:
        ordering = ["-is_default", "-created_at"]

    def __str__(self):
        return f"{self.label}: {self.line1}, {self.city}"

    def snapshot(self):
        return {
            "label": self.label, "line1": self.line1, "line2": self.line2, "landmark": self.landmark,
            "city": self.city, "state": self.state, "pincode": self.pincode,
            "latitude": str(self.latitude), "longitude": str(self.longitude),
        }
