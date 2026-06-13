from decimal import Decimal

from django import forms

from accounts.forms import StyledFormMixin
from accounts.models import CustomerProfile, User
from banking.models import AccountType, BankAccount, Biller
from loans.models import LoanProduct
from support.models import Ticket


class CustomerCreateForm(StyledFormMixin, forms.Form):
    """Create a full customer from the staff portal: user + profile + first
    account, with an optional opening deposit."""

    first_name = forms.CharField(max_length=150)
    last_name = forms.CharField(max_length=150)
    email = forms.EmailField()
    phone = forms.CharField(max_length=20)
    password = forms.CharField(
        widget=forms.PasswordInput,
        help_text="Temporary password the customer will use to sign in.",
    )
    date_of_birth = forms.DateField(
        required=False, widget=forms.DateInput(attrs={"type": "date"})
    )
    gender = forms.ChoiceField(choices=CustomerProfile.Gender.choices, required=False)
    address = forms.CharField(max_length=255, required=False)
    city = forms.CharField(max_length=100, required=False)
    state = forms.CharField(max_length=100, required=False)
    national_id_number = forms.CharField(
        label="National ID / Tax number", max_length=30, required=False
    )
    occupation = forms.CharField(max_length=100, required=False)
    account_type = forms.ModelChoiceField(
        queryset=AccountType.objects.filter(is_active=True)
    )
    opening_deposit = forms.DecimalField(
        min_value=Decimal("0.00"), max_digits=14, decimal_places=2,
        required=False, initial=Decimal("0.00"), label="Opening deposit (€)",
        widget=forms.NumberInput(attrs={"step": "0.01"}),
        help_text="Only applied if the account is approved immediately.",
    )
    mark_kyc_verified = forms.BooleanField(
        required=False, initial=True,
        label="Mark KYC as verified (documents sighted in branch)",
    )
    approve_immediately = forms.BooleanField(
        required=False, initial=True,
        label="Approve account immediately (reviewed in branch)",
        help_text="Untick to leave the account pending approval.",
    )

    def clean_email(self):
        email = self.cleaned_data["email"].lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("A user with this email already exists.")
        return email


class CustomerEditForm(StyledFormMixin, forms.ModelForm):
    first_name = forms.CharField(max_length=150)
    last_name = forms.CharField(max_length=150)
    phone = forms.CharField(max_length=20, required=False)

    class Meta:
        model = CustomerProfile
        fields = (
            "date_of_birth", "gender", "address", "city", "state", "country",
            "national_id_number", "occupation",
            "next_of_kin_name", "next_of_kin_phone", "next_of_kin_relationship",
        )
        widgets = {"date_of_birth": forms.DateInput(attrs={"type": "date"})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        user = self.instance.user
        self.fields["first_name"].initial = user.first_name
        self.fields["last_name"].initial = user.last_name
        self.fields["phone"].initial = user.phone

    def save(self, commit=True):
        profile = super().save(commit=commit)
        user = profile.user
        user.first_name = self.cleaned_data["first_name"]
        user.last_name = self.cleaned_data["last_name"]
        user.phone = self.cleaned_data.get("phone", "")
        if commit:
            user.save(update_fields=["first_name", "last_name", "phone"])
        return profile


class AccountOpenForm(StyledFormMixin, forms.Form):
    account_type = forms.ModelChoiceField(
        queryset=AccountType.objects.filter(is_active=True)
    )
    opening_deposit = forms.DecimalField(
        min_value=Decimal("0.00"), max_digits=14, decimal_places=2,
        required=False, initial=Decimal("0.00"), label="Opening deposit (€)",
        widget=forms.NumberInput(attrs={"step": "0.01"}),
    )
    approve_immediately = forms.BooleanField(
        required=False, initial=True,
        label="Approve immediately (otherwise pending approval)",
    )


_DATETIME_LOCAL_FORMATS = ["%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S",
                           "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"]


def value_date_field(label="Value date"):
    """A datetime-local field for back- or forward-dating a posting.
    Leaving it blank uses the current date and time."""
    return forms.DateTimeField(
        required=False,
        label=label,
        input_formats=_DATETIME_LOCAL_FORMATS,
        widget=forms.DateTimeInput(
            attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"
        ),
        help_text="Leave blank to use the current date and time.",
    )


class PostingForm(StyledFormMixin, forms.Form):
    """Cash deposit or withdrawal posted by a teller, with a reason and date."""

    amount = forms.DecimalField(
        min_value=Decimal("0.01"), max_digits=14, decimal_places=2,
        label="Amount (€)",
        widget=forms.NumberInput(attrs={"step": "0.01"}),
    )
    description = forms.CharField(
        max_length=255, required=False, label="Reason / narration",
    )
    value_date = value_date_field()


class AdjustmentForm(StyledFormMixin, forms.Form):
    direction = forms.ChoiceField(
        choices=[("CREDIT", "Credit (add funds)"), ("DEBIT", "Debit (remove funds)")]
    )
    amount = forms.DecimalField(
        min_value=Decimal("0.01"), max_digits=14, decimal_places=2,
        label="Amount (€)",
        widget=forms.NumberInput(attrs={"step": "0.01"}),
    )
    description = forms.CharField(
        max_length=255, label="Reason",
        help_text="Reason for the adjustment (required for audit).",
    )
    value_date = value_date_field()


class SetBalanceForm(StyledFormMixin, forms.Form):
    target_balance = forms.DecimalField(
        min_value=Decimal("0.00"), max_digits=14, decimal_places=2,
        label="New balance (€)",
        widget=forms.NumberInput(attrs={"step": "0.01"}),
    )
    description = forms.CharField(
        max_length=255, label="Reason",
        help_text="Recorded on the ledger and shown in the audit trail.",
    )


class AccountStatusForm(StyledFormMixin, forms.Form):
    status = forms.ChoiceField(choices=BankAccount.Status.choices)
    reason = forms.CharField(max_length=255, required=False)


class KycDecisionForm(StyledFormMixin, forms.Form):
    decision = forms.ChoiceField(
        choices=[("VERIFY", "Verify"), ("REJECT", "Reject")]
    )
    reason = forms.CharField(
        max_length=255, required=False,
        help_text="Required when rejecting.",
    )

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("decision") == "REJECT" and not cleaned.get("reason"):
            self.add_error("reason", "Give the customer a reason for the rejection.")
        return cleaned


class LoanDecisionForm(StyledFormMixin, forms.Form):
    decision = forms.ChoiceField(
        choices=[("APPROVE", "Approve and disburse"), ("REJECT", "Reject")]
    )
    note = forms.CharField(max_length=255, required=False)

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("decision") == "REJECT" and not cleaned.get("note"):
            self.add_error("note", "Give the customer a reason for the rejection.")
        return cleaned


class CashRepaymentForm(StyledFormMixin, forms.Form):
    amount = forms.DecimalField(
        min_value=Decimal("0.01"), max_digits=14, decimal_places=2,
        widget=forms.NumberInput(attrs={"step": "0.01"}),
        help_text="Cash received in branch; it is deposited then applied to the loan.",
    )


class ReversalForm(StyledFormMixin, forms.Form):
    reason = forms.CharField(max_length=255)


class TicketReplyForm(StyledFormMixin, forms.Form):
    body = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}), required=False)
    status = forms.ChoiceField(choices=Ticket.Status.choices)


class StaffUserForm(StyledFormMixin, forms.Form):
    first_name = forms.CharField(max_length=150)
    last_name = forms.CharField(max_length=150)
    email = forms.EmailField()
    phone = forms.CharField(max_length=20, required=False)
    role = forms.ChoiceField(choices=[
        (User.Role.TELLER, "Teller"),
        (User.Role.MANAGER, "Manager"),
        (User.Role.ADMIN, "Administrator"),
    ])
    password = forms.CharField(widget=forms.PasswordInput)

    def clean_email(self):
        email = self.cleaned_data["email"].lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("A user with this email already exists.")
        return email


class AccountTypeForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = AccountType
        fields = (
            "name", "code", "description", "interest_rate", "minimum_balance",
            "monthly_fee", "daily_transfer_limit", "is_active",
        )


class LoanProductForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = LoanProduct
        fields = (
            "name", "description", "interest_rate", "min_amount", "max_amount",
            "min_tenor_months", "max_tenor_months", "is_active",
        )


class BillerForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Biller
        fields = ("name", "category", "customer_id_label", "is_active")


class PopulateHistoryForm(StyledFormMixin, forms.Form):
    target_balance = forms.DecimalField(
        min_value=Decimal("0.00"), max_digits=14, decimal_places=2,
        label="Target balance after populating (€)",
        widget=forms.NumberInput(attrs={"step": "0.01"}),
        help_text="The account will land at approximately this balance once the history is generated.",
    )
    months = forms.ChoiceField(
        choices=[(3, "3 months"), (6, "6 months"), (12, "12 months"), (24, "24 months")],
        initial=6,
        label="History period",
        help_text="How far back the generated transactions will be spread.",
    )
