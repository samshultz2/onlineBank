import secrets
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

from banking.models import BankAccount


def generate_card_number(scheme):
    prefixes = {"VISA": "4", "MASTERCARD": "5399", "GIROCARD": "6705"}
    prefix = prefixes.get(scheme, "6705")
    while True:
        body = "".join(secrets.choice("0123456789") for _ in range(16 - len(prefix)))
        number = f"{prefix}{body}"
        if not Card.objects.filter(card_number=number).exists():
            return number


class Card(models.Model):
    class Scheme(models.TextChoices):
        GIROCARD = "GIROCARD", "Girocard"
        VISA = "VISA", "Visa"
        MASTERCARD = "MASTERCARD", "Mastercard"

    class Status(models.TextChoices):
        REQUESTED = "REQUESTED", "Requested"
        ACTIVE = "ACTIVE", "Active"
        FROZEN = "FROZEN", "Frozen"
        BLOCKED = "BLOCKED", "Blocked"
        EXPIRED = "EXPIRED", "Expired"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="cards"
    )
    account = models.ForeignKey(
        BankAccount, on_delete=models.PROTECT, related_name="cards"
    )
    scheme = models.CharField(max_length=12, choices=Scheme.choices)
    card_number = models.CharField(max_length=16, unique=True, blank=True)
    cvv = models.CharField(max_length=3, blank=True)
    expiry_month = models.PositiveSmallIntegerField(null=True, blank=True)
    expiry_year = models.PositiveSmallIntegerField(null=True, blank=True)
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.REQUESTED
    )
    daily_spend_limit = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("500000.00")
    )
    requested_at = models.DateTimeField(auto_now_add=True)
    issued_at = models.DateTimeField(null=True, blank=True)
    issued_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="issued_cards",
    )

    class Meta:
        ordering = ["-requested_at"]

    def __str__(self):
        return f"{self.get_scheme_display()} card for {self.user}"

    @property
    def masked_number(self):
        if not self.card_number:
            return "•••• •••• •••• ••••"
        return f"•••• •••• •••• {self.card_number[-4:]}"

    @property
    def expiry_display(self):
        if not self.expiry_month or not self.expiry_year:
            return "--/--"
        return f"{self.expiry_month:02d}/{str(self.expiry_year)[-2:]}"

    def issue(self, issued_by):
        """Generate card details and activate; called from the staff portal."""
        self.card_number = generate_card_number(self.scheme)
        self.cvv = "".join(secrets.choice("0123456789") for _ in range(3))
        now = timezone.localdate()
        self.expiry_month = now.month
        self.expiry_year = now.year + 4
        self.status = self.Status.ACTIVE
        self.issued_at = timezone.now()
        self.issued_by = issued_by
        self.save()
