from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

from banking.models import BankAccount

TWO_PLACES = Decimal("0.01")


class LoanProduct(models.Model):
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    interest_rate = models.DecimalField(
        max_digits=5, decimal_places=2, help_text="Annual interest rate (%)"
    )
    min_amount = models.DecimalField(max_digits=14, decimal_places=2)
    max_amount = models.DecimalField(max_digits=14, decimal_places=2)
    min_tenor_months = models.PositiveSmallIntegerField(default=1)
    max_tenor_months = models.PositiveSmallIntegerField(default=24)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Loan(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending review"
        APPROVED = "APPROVED", "Approved (awaiting disbursement)"
        ACTIVE = "ACTIVE", "Active"
        REJECTED = "REJECTED", "Rejected"
        PAID = "PAID", "Fully repaid"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="loans"
    )
    product = models.ForeignKey(LoanProduct, on_delete=models.PROTECT)
    disbursement_account = models.ForeignKey(
        BankAccount, on_delete=models.PROTECT, related_name="loans"
    )
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    tenor_months = models.PositiveSmallIntegerField()
    purpose = models.CharField(max_length=255, blank=True)
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.PENDING
    )
    # Simple flat interest: total = principal * (1 + rate/100 * tenor/12)
    total_payable = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True
    )
    amount_repaid = models.DecimalField(
        max_digits=14, decimal_places=2, default=Decimal("0.00")
    )
    decision_note = models.CharField(max_length=255, blank=True)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="decided_loans",
    )
    applied_at = models.DateTimeField(auto_now_add=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    disbursed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-applied_at"]

    def __str__(self):
        return f"{self.product} - {self.amount} for {self.user}"

    def compute_total_payable(self):
        rate = self.product.interest_rate / Decimal("100")
        total = self.amount * (
            Decimal("1") + rate * Decimal(self.tenor_months) / Decimal("12")
        )
        return total.quantize(TWO_PLACES)

    @property
    def outstanding_balance(self):
        if self.total_payable is None:
            return self.compute_total_payable()
        return (self.total_payable - self.amount_repaid).quantize(TWO_PLACES)

    @property
    def monthly_installment(self):
        total = self.total_payable or self.compute_total_payable()
        return (total / Decimal(self.tenor_months)).quantize(TWO_PLACES)

    @property
    def repayment_progress(self):
        if not self.total_payable:
            return 0
        return round(float(self.amount_repaid / self.total_payable) * 100)


class LoanRepayment(models.Model):
    loan = models.ForeignKey(Loan, on_delete=models.PROTECT, related_name="repayments")
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    reference = models.CharField(max_length=40)
    paid_at = models.DateTimeField(default=timezone.now)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )

    class Meta:
        ordering = ["-paid_at"]

    def __str__(self):
        return f"₦{self.amount} on {self.loan}"
