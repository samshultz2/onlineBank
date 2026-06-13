from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models
from django.utils import timezone


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create_user(self, email, password, **extra_fields):
        if not email:
            raise ValueError("An email address is required")
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("role", User.Role.ADMIN)
        return self._create_user(email, password, **extra_fields)


class User(AbstractUser):
    class Role(models.TextChoices):
        CUSTOMER = "CUSTOMER", "Customer"
        TELLER = "TELLER", "Teller"
        MANAGER = "MANAGER", "Manager"
        ADMIN = "ADMIN", "Administrator"

    username = None
    email = models.EmailField("email address", unique=True)
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.CUSTOMER)
    phone = models.CharField(max_length=20, blank=True)
    failed_login_attempts = models.PositiveIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    objects = UserManager()

    def __str__(self):
        return f"{self.get_full_name() or self.email}"

    @property
    def is_bank_staff(self):
        return self.role in {self.Role.TELLER, self.Role.MANAGER, self.Role.ADMIN}

    @property
    def is_locked(self):
        return bool(self.locked_until and self.locked_until > timezone.now())

    def register_failed_login(self):
        self.failed_login_attempts += 1
        if self.failed_login_attempts >= settings.MAX_LOGIN_ATTEMPTS:
            self.locked_until = timezone.now() + timezone.timedelta(
                minutes=settings.LOGIN_LOCKOUT_MINUTES
            )
            self.failed_login_attempts = 0
        self.save(update_fields=["failed_login_attempts", "locked_until"])

    def register_successful_login(self):
        if self.failed_login_attempts or self.locked_until:
            self.failed_login_attempts = 0
            self.locked_until = None
            self.save(update_fields=["failed_login_attempts", "locked_until"])


class CustomerProfile(models.Model):
    class KycStatus(models.TextChoices):
        PENDING = "PENDING", "Pending review"
        VERIFIED = "VERIFIED", "Verified"
        REJECTED = "REJECTED", "Rejected"

    class Gender(models.TextChoices):
        MALE = "M", "Male"
        FEMALE = "F", "Female"
        OTHER = "O", "Other"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile"
    )
    date_of_birth = models.DateField(null=True, blank=True)
    gender = models.CharField(max_length=1, choices=Gender.choices, blank=True)
    address = models.CharField(max_length=255, blank=True)
    city = models.CharField(max_length=100, blank=True)
    state = models.CharField(max_length=100, blank=True)
    country = models.CharField(max_length=100, default="Germany")
    national_id_number = models.CharField(
        "Tax ID / National ID", max_length=30, blank=True
    )
    occupation = models.CharField(max_length=100, blank=True)
    photo = models.ImageField(upload_to="customer_photos/", null=True, blank=True)
    id_document = models.FileField(upload_to="kyc_documents/", null=True, blank=True)
    kyc_status = models.CharField(
        max_length=10, choices=KycStatus.choices, default=KycStatus.PENDING
    )
    kyc_rejection_reason = models.CharField(max_length=255, blank=True)
    next_of_kin_name = models.CharField(max_length=150, blank=True)
    next_of_kin_phone = models.CharField(max_length=20, blank=True)
    next_of_kin_relationship = models.CharField(max_length=50, blank=True)
    # Transaction PIN (hashed). Required before any money can move.
    transaction_pin = models.CharField(max_length=128, blank=True)
    failed_pin_attempts = models.PositiveIntegerField(default=0)
    pin_locked_until = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Profile of {self.user}"

    @property
    def has_pin(self):
        return bool(self.transaction_pin)

    @property
    def pin_is_locked(self):
        return bool(self.pin_locked_until and self.pin_locked_until > timezone.now())

    def set_pin(self, raw_pin):
        self.transaction_pin = make_password(raw_pin)
        self.failed_pin_attempts = 0
        self.pin_locked_until = None
        self.save(update_fields=["transaction_pin", "failed_pin_attempts", "pin_locked_until"])

    def verify_pin(self, raw_pin):
        """Check the PIN; lock it for 30 minutes after too many failures."""
        if self.pin_is_locked or not self.transaction_pin:
            return False
        if check_password(raw_pin, self.transaction_pin):
            if self.failed_pin_attempts:
                self.failed_pin_attempts = 0
                self.save(update_fields=["failed_pin_attempts"])
            return True
        self.failed_pin_attempts += 1
        if self.failed_pin_attempts >= settings.MAX_PIN_ATTEMPTS:
            self.pin_locked_until = timezone.now() + timezone.timedelta(minutes=30)
            self.failed_pin_attempts = 0
        self.save(update_fields=["failed_pin_attempts", "pin_locked_until"])
        return False


class AuditLog(models.Model):
    """Immutable trail of every sensitive action in the system."""

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_entries",
    )
    action = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.action} by {self.actor} at {self.created_at:%Y-%m-%d %H:%M}"


def log_action(actor, action, description="", request=None):
    ip = None
    if request is not None:
        ip = (
            request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip()
            or request.META.get("REMOTE_ADDR")
        )
    AuditLog.objects.create(
        actor=actor if getattr(actor, "is_authenticated", False) else None,
        action=action,
        description=description,
        ip_address=ip,
    )
