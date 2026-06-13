from decimal import Decimal

from django import forms
from django.conf import settings

from accounts.forms import StyledFormMixin

from .models import BankAccount, Beneficiary, Biller


class AccountChoiceMixin(StyledFormMixin):
    """Limit account dropdowns to the customer's own active accounts."""

    def __init__(self, user, *args, **kwargs):
        super().__init__(*args, **kwargs)
        accounts = user.bank_accounts.filter(status=BankAccount.Status.ACTIVE)
        for name in ("source_account", "account"):
            if name in self.fields:
                self.fields[name].queryset = accounts
                self.fields[name].label_from_instance = (
                    lambda a: f"{a.account_number} - {a.account_type.name} "
                              f"(₦{a.balance:,.2f})"
                )


class TransferForm(AccountChoiceMixin, forms.Form):
    source_account = forms.ModelChoiceField(queryset=BankAccount.objects.none())
    destination_account = forms.RegexField(
        regex=r"^\d{10}$",
        label="Destination account number",
        widget=forms.TextInput(attrs={
            "inputmode": "numeric", "maxlength": "10", "autocomplete": "off",
            "id": "id_destination_account",
        }),
        error_messages={"invalid": "Account number must be exactly 10 digits."},
    )
    amount = forms.DecimalField(
        min_value=Decimal("1.00"), max_digits=14, decimal_places=2,
        widget=forms.NumberInput(attrs={"step": "0.01"}),
    )
    narration = forms.CharField(max_length=100, required=False)
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
        min_value=Decimal("10000.00"), max_digits=14, decimal_places=2,
        help_text="Minimum ₦10,000",
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
    class Meta:
        model = Beneficiary
        fields = ("name", "nickname", "account_number", "bank_name")

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
