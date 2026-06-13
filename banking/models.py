import secrets
import string
from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone

TWO_PLACES = Decimal("0.01")


def generate_account_number():
    """10-digit account number with a configurable bank prefix."""
    from banking.models import BankAccount  # local import for clarity

    prefix = settings.BANK_ACCOUNT_NUMBER_PREFIX
    while True:
        body = "".join(secrets.choice(string.digits) for _ in range(10 - len(prefix)))
        number = f"{prefix}{body}"
        if not BankAccount.objects.filter(account_number=number).exists():
            return number


def generate_reference(prefix="TRX"):
    stamp = timezone.now().strftime("%Y%m%d%H%M%S")
    rand = "".join(secrets.choice(string.digits) for _ in range(6))
    return f"{prefix}-{stamp}-{rand}"


def _iban_check_digits(country_code, bban):
    """Compute the two ISO 13616 (mod-97) check digits for an IBAN."""
    rearranged = f"{bban}{country_code}00"
    numeric = "".join(
        str(int(ch, 36)) if ch.isalpha() else ch for ch in rearranged
    )
    check = 98 - (int(numeric) % 97)
    return f"{check:02d}"


def build_iban(account_number):
    """Build a valid IBAN from our internal account number (German layout:
    DE + 2 check digits + 8-digit bank code + 10-digit account number)."""
    country = settings.BANK_COUNTRY_CODE
    bank_code = settings.BANK_CODE
    bban = f"{bank_code}{account_number}"
    check = _iban_check_digits(country, bban)
    return f"{country}{check}{bban}"


def format_iban(iban):
    """Group an IBAN into blocks of four for display."""
    iban = (iban or "").replace(" ", "")
    return " ".join(iban[i:i + 4] for i in range(0, len(iban), 4))


def normalise_iban(value):
    return (value or "").replace(" ", "").upper()


def iban_is_valid(value):
    """Validate an IBAN's structure and mod-97 checksum."""
    iban = normalise_iban(value)
    if not iban or len(iban) < 15 or len(iban) > 34 or not iban[:2].isalpha():
        return False
    if not iban[2:4].isdigit():
        return False
    rearranged = iban[4:] + iban[:4]
    try:
        numeric = "".join(
            str(int(ch, 36)) if ch.isalpha() else ch for ch in rearranged
        )
        return int(numeric) % 97 == 1
    except ValueError:
        return False


class AccountType(models.Model):
    name = models.CharField(max_length=50, unique=True)
    code = models.CharField(max_length=10, unique=True)
    description = models.TextField(blank=True)
    interest_rate = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal("0.00"),
        help_text="Annual interest rate (%)",
    )
    minimum_balance = models.DecimalField(
        max_digits=14, decimal_places=2, default=Decimal("0.00")
    )
    monthly_fee = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal("0.00")
    )
    daily_transfer_limit = models.DecimalField(
        max_digits=14, decimal_places=2, default=Decimal("1000000.00")
    )
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class BankAccount(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending approval"
        ACTIVE = "ACTIVE", "Active"
        DORMANT = "DORMANT", "Dormant"
        FROZEN = "FROZEN", "Frozen"
        CLOSED = "CLOSED", "Closed"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="bank_accounts"
    )
    account_type = models.ForeignKey(AccountType, on_delete=models.PROTECT)
    account_number = models.CharField(max_length=10, unique=True, db_index=True)
    iban = models.CharField(max_length=34, unique=True, db_index=True, blank=True)
    bic = models.CharField(max_length=11, blank=True)
    balance = models.DecimalField(
        max_digits=14, decimal_places=2, default=Decimal("0.00")
    )
    currency = models.CharField(max_length=3, default="EUR")
    # New accounts must be reviewed and approved by bank staff before use.
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.PENDING
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_accounts",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    review_note = models.CharField(max_length=255, blank=True)
    opened_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-opened_at"]

    def __str__(self):
        return f"{self.iban or self.account_number} ({self.user})"

    @property
    def is_active(self):
        return self.status == self.Status.ACTIVE

    @property
    def is_pending(self):
        return self.status == self.Status.PENDING

    @property
    def can_transact(self):
        return self.status == self.Status.ACTIVE

    @property
    def iban_formatted(self):
        return format_iban(self.iban)

    @property
    def available_balance(self):
        return max(
            Decimal("0.00"), self.balance - self.account_type.minimum_balance
        ).quantize(TWO_PLACES)

    def amount_transferred_today(self):
        today = timezone.localdate()
        total = self.transactions.filter(
            direction=Transaction.Direction.DEBIT,
            channel__in=[Transaction.Channel.TRANSFER, Transaction.Channel.BILL_PAYMENT],
            status=Transaction.Status.COMPLETED,
            created_at__date=today,
        ).aggregate(total=models.Sum("amount"))["total"]
        return total or Decimal("0.00")

    def save(self, *args, **kwargs):
        if not self.account_number:
            self.account_number = generate_account_number()
        if not self.iban:
            self.iban = build_iban(self.account_number)
        if not self.bic:
            self.bic = settings.BANK_BIC
        super().save(*args, **kwargs)


class Transaction(models.Model):
    """A single ledger entry. Transfers create one DEBIT and one CREDIT row
    that share the same reference."""

    class Direction(models.TextChoices):
        CREDIT = "CREDIT", "Credit"
        DEBIT = "DEBIT", "Debit"

    class Channel(models.TextChoices):
        TRANSFER = "TRANSFER", "Transfer"
        DEPOSIT = "DEPOSIT", "Cash deposit"
        WITHDRAWAL = "WITHDRAWAL", "Cash withdrawal"
        BILL_PAYMENT = "BILL_PAYMENT", "Bill payment"
        LOAN_DISBURSEMENT = "LOAN_DISBURSEMENT", "Loan disbursement"
        LOAN_REPAYMENT = "LOAN_REPAYMENT", "Loan repayment"
        FIXED_DEPOSIT = "FIXED_DEPOSIT", "Fixed deposit"
        INTEREST = "INTEREST", "Interest"
        FEE = "FEE", "Fee"
        REVERSAL = "REVERSAL", "Reversal"
        ADJUSTMENT = "ADJUSTMENT", "Admin adjustment"

    class Status(models.TextChoices):
        COMPLETED = "COMPLETED", "Completed"
        PENDING = "PENDING", "Pending"
        FAILED = "FAILED", "Failed"
        REVERSED = "REVERSED", "Reversed"

    account = models.ForeignKey(
        BankAccount, on_delete=models.PROTECT, related_name="transactions"
    )
    direction = models.CharField(max_length=6, choices=Direction.choices)
    channel = models.CharField(max_length=20, choices=Channel.choices)
    amount = models.DecimalField(
        max_digits=14, decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    balance_after = models.DecimalField(max_digits=14, decimal_places=2)
    reference = models.CharField(max_length=40, db_index=True)
    description = models.CharField(max_length=255, blank=True)
    counterparty_name = models.CharField(max_length=150, blank=True)
    counterparty_account = models.CharField(max_length=20, blank=True)
    counterparty_bank = models.CharField(max_length=100, blank=True)
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.COMPLETED
    )
    initiated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="initiated_transactions",
    )
    reversed_by = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="reversal_of",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.direction} {self.amount} on {self.account.account_number}"

    @property
    def signed_amount(self):
        return self.amount if self.direction == self.Direction.CREDIT else -self.amount


class Beneficiary(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="beneficiaries"
    )
    name = models.CharField(max_length=150)
    nickname = models.CharField(max_length=50, blank=True)
    iban = models.CharField("IBAN", max_length=34)
    bank_name = models.CharField(max_length=100, default=settings.BANK_NAME)
    bic = models.CharField("BIC / SWIFT", max_length=11, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ["user", "iban"]
        ordering = ["name"]
        verbose_name_plural = "beneficiaries"

    def __str__(self):
        return f"{self.name} ({self.iban})"

    @property
    def iban_formatted(self):
        return format_iban(self.iban)


class FixedDeposit(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        MATURED = "MATURED", "Matured"
        BROKEN = "BROKEN", "Broken early"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="fixed_deposits"
    )
    source_account = models.ForeignKey(
        BankAccount, on_delete=models.PROTECT, related_name="fixed_deposits"
    )
    reference = models.CharField(max_length=40, unique=True)
    principal = models.DecimalField(max_digits=14, decimal_places=2)
    interest_rate = models.DecimalField(
        max_digits=5, decimal_places=2, help_text="Annual rate (%)"
    )
    tenor_days = models.PositiveIntegerField()
    start_date = models.DateField(default=timezone.localdate)
    maturity_date = models.DateField()
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.ACTIVE
    )
    payout_amount = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True
    )
    closed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"FD {self.reference} - {self.principal}"

    @property
    def expected_interest(self):
        rate = self.interest_rate / Decimal("100")
        interest = self.principal * rate * Decimal(self.tenor_days) / Decimal("365")
        return interest.quantize(TWO_PLACES)

    @property
    def expected_payout(self):
        return (self.principal + self.expected_interest).quantize(TWO_PLACES)

    @property
    def is_matured(self):
        return timezone.localdate() >= self.maturity_date


class Biller(models.Model):
    """Billers (electricity, TV, internet...) are managed by bank staff."""

    class Category(models.TextChoices):
        ELECTRICITY = "ELECTRICITY", "Electricity"
        TV = "TV", "Cable TV"
        INTERNET = "INTERNET", "Internet"
        AIRTIME = "AIRTIME", "Airtime"
        WATER = "WATER", "Water"
        EDUCATION = "EDUCATION", "Education"
        OTHER = "OTHER", "Other"

    name = models.CharField(max_length=100, unique=True)
    category = models.CharField(max_length=20, choices=Category.choices)
    customer_id_label = models.CharField(
        max_length=50, default="Customer ID",
        help_text="e.g. Meter number, Smartcard number, Phone number",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["category", "name"]

    def __str__(self):
        return self.name


class BillPayment(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="bill_payments"
    )
    account = models.ForeignKey(BankAccount, on_delete=models.PROTECT)
    biller = models.ForeignKey(Biller, on_delete=models.PROTECT)
    customer_reference = models.CharField(
        max_length=50, help_text="Meter / smartcard / phone number"
    )
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    reference = models.CharField(max_length=40, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.biller} - {self.amount}"
