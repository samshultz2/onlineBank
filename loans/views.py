from decimal import Decimal

from django import forms
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render

from accounts.decorators import customer_required, pin_setup_required
from accounts.forms import StyledFormMixin
from accounts.models import CustomerProfile, log_action
from banking.models import BankAccount
from banking.services import TransactionError

from . import services
from .models import Loan, LoanProduct


class LoanApplicationForm(StyledFormMixin, forms.Form):
    product = forms.ModelChoiceField(queryset=LoanProduct.objects.filter(is_active=True))
    disbursement_account = forms.ModelChoiceField(queryset=BankAccount.objects.none())
    amount = forms.DecimalField(
        min_value=Decimal("1.00"), max_digits=14, decimal_places=2,
        widget=forms.NumberInput(attrs={"step": "0.01"}),
    )
    tenor_months = forms.IntegerField(min_value=1, max_value=60, label="Tenor (months)")
    purpose = forms.CharField(max_length=255, widget=forms.Textarea(attrs={"rows": 3}))

    def __init__(self, user, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["disbursement_account"].queryset = user.bank_accounts.filter(
            status=BankAccount.Status.ACTIVE
        )
        self.fields["disbursement_account"].label_from_instance = (
            lambda a: f"{a.account_number} - {a.account_type.name}"
        )

    def clean(self):
        cleaned = super().clean()
        product = cleaned.get("product")
        amount = cleaned.get("amount")
        tenor = cleaned.get("tenor_months")
        if product and amount:
            if amount < product.min_amount or amount > product.max_amount:
                self.add_error(
                    "amount",
                    f"Amount must be between ₦{product.min_amount:,.2f} and "
                    f"₦{product.max_amount:,.2f} for this product.",
                )
        if product and tenor:
            if tenor < product.min_tenor_months or tenor > product.max_tenor_months:
                self.add_error(
                    "tenor_months",
                    f"Tenor must be between {product.min_tenor_months} and "
                    f"{product.max_tenor_months} months for this product.",
                )
        return cleaned


class RepaymentForm(StyledFormMixin, forms.Form):
    account = forms.ModelChoiceField(queryset=BankAccount.objects.none())
    amount = forms.DecimalField(
        min_value=Decimal("1.00"), max_digits=14, decimal_places=2,
        widget=forms.NumberInput(attrs={"step": "0.01"}),
    )
    pin = forms.RegexField(
        regex=r"^\d{4}$", label="Transaction PIN",
        widget=forms.PasswordInput(attrs={"inputmode": "numeric", "maxlength": "4"}),
        error_messages={"invalid": "PIN must be exactly 4 digits."},
    )

    def __init__(self, user, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["account"].queryset = user.bank_accounts.filter(
            status=BankAccount.Status.ACTIVE
        )
        self.fields["account"].label_from_instance = (
            lambda a: f"{a.account_number} (₦{a.balance:,.2f})"
        )


@customer_required
def loan_list(request):
    loans = request.user.loans.select_related("product", "disbursement_account")
    products = LoanProduct.objects.filter(is_active=True)
    return render(request, "customer/loans.html", {"loans": loans, "products": products})


@customer_required
def apply_for_loan(request):
    profile = request.user.profile
    if profile.kyc_status != CustomerProfile.KycStatus.VERIFIED:
        messages.warning(
            request,
            "Loans require a verified identity. Please complete KYC verification first.",
        )
        return redirect("accounts:upload_kyc")
    if request.user.loans.filter(
        status__in=[Loan.Status.PENDING, Loan.Status.APPROVED]
    ).exists():
        messages.error(request, "You already have a loan application under review.")
        return redirect("loans:list")

    if request.method == "POST":
        form = LoanApplicationForm(request.user, request.POST)
        if form.is_valid():
            loan = Loan.objects.create(
                user=request.user,
                product=form.cleaned_data["product"],
                disbursement_account=form.cleaned_data["disbursement_account"],
                amount=form.cleaned_data["amount"],
                tenor_months=form.cleaned_data["tenor_months"],
                purpose=form.cleaned_data["purpose"],
            )
            log_action(request.user, "LOAN_APPLY",
                       f"{loan.product} for ₦{loan.amount}", request)
            messages.success(
                request,
                "Application submitted. You will be notified once it is reviewed.",
            )
            return redirect("loans:list")
    else:
        form = LoanApplicationForm(request.user)
    return render(request, "customer/loan_apply.html", {"form": form})


@customer_required
def loan_detail(request, pk):
    loan = get_object_or_404(
        Loan.objects.select_related("product", "disbursement_account"),
        pk=pk, user=request.user,
    )
    repayment_form = RepaymentForm(request.user)
    return render(request, "customer/loan_detail.html", {
        "loan": loan,
        "repayments": loan.repayments.all(),
        "repayment_form": repayment_form,
    })


@pin_setup_required
def repay_loan(request, pk):
    loan = get_object_or_404(Loan, pk=pk, user=request.user)
    if request.method != "POST":
        return redirect("loans:detail", pk=pk)
    form = RepaymentForm(request.user, request.POST)
    if form.is_valid():
        profile = request.user.profile
        if profile.pin_is_locked or not profile.verify_pin(form.cleaned_data["pin"]):
            messages.error(request, "Incorrect transaction PIN.")
        else:
            try:
                services.repay_loan(
                    loan, form.cleaned_data["account"], form.cleaned_data["amount"],
                    initiated_by=request.user,
                )
            except TransactionError as exc:
                messages.error(request, str(exc))
            else:
                log_action(request.user, "LOAN_REPAY",
                           f"₦{form.cleaned_data['amount']} on loan #{loan.pk}",
                           request)
                messages.success(request, "Repayment successful.")
    else:
        messages.error(request, "Please correct the repayment details and try again.")
    return redirect("loans:detail", pk=pk)
