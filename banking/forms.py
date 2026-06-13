from decimal import Decimal

from django import forms
from django.conf import settings

from accounts.forms import StyledFormMixin

from django.utils import timezone

from .models import (
    BankAccount,
    Beneficiary,
    Biller,
    StandingOrder,
    iban_is_valid,
    normalise_iban,
)


class AccountChoiceMixin(StyledFormMixin):
    """Limit account dropdowns to the customer's own active accounts."""

    def __init__(self, user, *args, **kwargs):
        super().__init__(*args, **kwargs)
        accounts = user.bank_accounts.filter(status=BankAccount.Status.ACTIVE)
        for name in ("source_account", "account"):
            if name in self.fields:
                self.fields[name].queryset = accounts
                self.fields[name].label_from_instance = (
                    lambda a: f"{a.iban_formatted} · {a.account_type.name} "
                              f"(€{a.balance:,.2f})"
                )


class IbanField(forms.CharField):
    """A CharField that normalises and validates an IBAN."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("max_length", 42)
        kwargs.setdefault("label", "Beneficiary IBAN")
        super().__init__(*args, **kwargs)
        self.widget.attrs.setdefault("placeholder", "DE00 0000 0000 0000 0000 00")
        self.widget.attrs.setdefault("autocomplete", "off")

    def clean(self, value):
        value = super().clean(value)
        iban = normalise_iban(value)
        if not iban_is_valid(iban):
            raise forms.ValidationError(
                "Enter a valid IBAN (check the country code and length)."
            )
        return iban


class TransferForm(AccountChoiceMixin, forms.Form):
    source_account = forms.ModelChoiceField(
        queryset=BankAccount.objects.none(), label="Pay from"
    )
    destination_account = IbanField(
        label="Beneficiary IBAN",
        widget=forms.TextInput(attrs={
            "autocomplete": "off", "id": "id_destination_account",
            "spellcheck": "false",
        }),
    )
    amount = forms.DecimalField(
        min_value=Decimal("1.00"), max_digits=14, decimal_places=2,
        label="Amount (€)",
        widget=forms.NumberInput(attrs={"step": "0.01"}),
    )
    narration = forms.CharField(
        max_length=140, required=False, label="Payment reference / remittance info"
    )
    save_beneficiary = forms.BooleanField(
        required=False, label="Save as beneficiary"
    )
    pin = forms.RegexField(
        regex=r"^\d{4}$", label="Transaction PIN",
        widget=forms.PasswordInput(attrs={"inputmode": "numeric", "maxlength": "4"}),
        error_messages={"invalid": "PIN must be exactly 4 digits."},
    )


class BillPaymentForm(AccountChoiceMixin, forms.Form):
    account = forms.ModelChoiceField(queryset=BankAccount.objects.none())
    biller = forms.ModelChoiceField(queryset=Biller.objects.filter(is_active=True))
    customer_reference = forms.CharField(
        max_length=50, label="Customer / meter / phone number"
    )
    amount = forms.DecimalField(
        min_value=Decimal("1.00"), max_digits=14, decimal_places=2,
        widget=forms.NumberInput(attrs={"step": "0.01"}),
    )
    pin = forms.RegexField(
        regex=r"^\d{4}$", label="Transaction PIN",
        widget=forms.PasswordInput(attrs={"inputmode": "numeric", "maxlength": "4"}),
        error_messages={"invalid": "PIN must be exactly 4 digits."},
    )


class FixedDepositForm(AccountChoiceMixin, forms.Form):
    TENOR_CHOICES = [
        (30, "30 days @ 7% p.a."),
        (90, "90 days @ 9% p.a."),
        (180, "180 days @ 11% p.a."),
        (365, "365 days @ 13% p.a."),
    ]
    TENOR_RATES = {30: Decimal("7.00"), 90: Decimal("9.00"),
                   180: Decimal("11.00"), 365: Decimal("13.00")}

    source_account = forms.ModelChoiceField(queryset=BankAccount.objects.none())
    principal = forms.DecimalField(
        min_value=Decimal("1000.00"), max_digits=14, decimal_places=2,
        label="Principal (€)",
        help_text="Minimum €1,000",
        widget=forms.NumberInput(attrs={"step": "0.01"}),
    )
    tenor_days = forms.TypedChoiceField(choices=TENOR_CHOICES, coerce=int)
    pin = forms.RegexField(
        regex=r"^\d{4}$", label="Transaction PIN",
        widget=forms.PasswordInput(attrs={"inputmode": "numeric", "maxlength": "4"}),
        error_messages={"invalid": "PIN must be exactly 4 digits."},
    )

    def rate_for_tenor(self):
        return self.TENOR_RATES[self.cleaned_data["tenor_days"]]


class BeneficiaryForm(StyledFormMixin, forms.ModelForm):
    iban = IbanField(label="IBAN")

    class Meta:
        model = Beneficiary
        fields = ("name", "nickname", "iban", "bank_name", "bic")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["bank_name"].initial = settings.BANK_NAME


class StatementFilterForm(StyledFormMixin, forms.Form):
    start_date = forms.DateField(
        required=False, widget=forms.DateInput(attrs={"type": "date"})
    )
    end_date = forms.DateField(
        required=False, widget=forms.DateInput(attrs={"type": "date"})
    )
    direction = forms.ChoiceField(
        required=False,
        choices=[("", "All"), ("CREDIT", "Credits"), ("DEBIT", "Debits")],
    )
    min_amount = forms.DecimalField(
        required=False, min_value=Decimal("0.00"), max_digits=14, decimal_places=2,
        label="Min €", widget=forms.NumberInput(attrs={"step": "0.01"}),
    )
    max_amount = forms.DecimalField(
        required=False, min_value=Decimal("0.00"), max_digits=14, decimal_places=2,
        label="Max €", widget=forms.NumberInput(attrs={"step": "0.01"}),
    )
    query = forms.CharField(
        required=False, max_length=100, label="Search",
        widget=forms.TextInput(attrs={
            "placeholder": "Description, reference or counterparty",
        }),
    )

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("start_date"), cleaned.get("end_date")
        if start and end and start > end:
            self.add_error("end_date", "End date cannot be before the start date.")
        lo, hi = cleaned.get("min_amount"), cleaned.get("max_amount")
        if lo is not None and hi is not None and lo > hi:
            self.add_error("max_amount", "Maximum amount cannot be below the minimum.")
        return cleaned

    def apply(self, transactions):
        """Apply the cleaned filters to a Transaction queryset."""
        data = self.cleaned_data
        if data.get("start_date"):
            transactions = transactions.filter(created_at__date__gte=data["start_date"])
        if data.get("end_date"):
            transactions = transactions.filter(created_at__date__lte=data["end_date"])
        if data.get("direction"):
            transactions = transactions.filter(direction=data["direction"])
        if data.get("min_amount") is not None:
            transactions = transactions.filter(amount__gte=data["min_amount"])
        if data.get("max_amount") is not None:
            transactions = transactions.filter(amount__lte=data["max_amount"])
        if data.get("query"):
            from django.db.models import Q

            q = data["query"]
            transactions = transactions.filter(
                Q(description__icontains=q)
                | Q(reference__icontains=q)
                | Q(counterparty_name__icontains=q)
                | Q(counterparty_account__icontains=q)
            )
        return transactions


class StandingOrderForm(AccountChoiceMixin, forms.Form):
    source_account = forms.ModelChoiceField(
        queryset=BankAccount.objects.none(), label="Pay from"
    )
    beneficiary_name = forms.CharField(max_length=150, label="Beneficiary name")
    destination_iban = IbanField(label="Beneficiary IBAN")
    amount = forms.DecimalField(
        min_value=Decimal("1.00"), max_digits=14, decimal_places=2,
        label="Amount (€)", widget=forms.NumberInput(attrs={"step": "0.01"}),
    )
    narration = forms.CharField(
        max_length=140, required=False, label="Payment reference"
    )
    frequency = forms.ChoiceField(choices=StandingOrder.Frequency.choices)
    start_date = forms.DateField(
        label="First payment date", widget=forms.DateInput(attrs={"type": "date"})
    )
    end_date = forms.DateField(
        required=False, label="End date (optional)",
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    max_executions = forms.IntegerField(
        required=False, min_value=1, label="Number of payments (optional)",
        help_text="Leave blank to keep paying until you cancel or the end date.",
    )
    pin = forms.RegexField(
        regex=r"^\d{4}$", label="Transaction PIN",
        widget=forms.PasswordInput(attrs={"inputmode": "numeric", "maxlength": "4"}),
        error_messages={"invalid": "PIN must be exactly 4 digits."},
    )

    def clean_start_date(self):
        start = self.cleaned_data["start_date"]
        if start < timezone.localdate():
            raise forms.ValidationError("The first payment date cannot be in the past.")
        return start

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("start_date"), cleaned.get("end_date")
        if start and end and end < start:
            self.add_error("end_date", "The end date must be after the first payment.")
        return cleaned
