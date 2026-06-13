import csv
from decimal import Decimal

from django.contrib import messages
from django.core.paginator import Paginator
from django.db import models
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from accounts.decorators import customer_required, pin_setup_required
from accounts.models import log_action

from . import services
from .forms import (
    BeneficiaryForm,
    BillPaymentForm,
    FixedDepositForm,
    StatementFilterForm,
    TransferForm,
)
from .models import BankAccount, Beneficiary, FixedDeposit, Transaction


def _verify_pin_or_error(request, form):
    """Shared PIN check for money-moving forms. Returns True when valid."""
    profile = request.user.profile
    if profile.pin_is_locked:
        form.add_error("pin", "Your PIN is temporarily locked after too many failed attempts.")
        return False
    if not profile.verify_pin(form.cleaned_data["pin"]):
        form.add_error("pin", "Incorrect transaction PIN.")
        return False
    return True


@customer_required
def dashboard(request):
    accounts = request.user.bank_accounts.exclude(
        status=BankAccount.Status.CLOSED
    ).select_related("account_type")
    total_balance = accounts.aggregate(total=models.Sum("balance"))["total"] or Decimal("0.00")
    account_ids = accounts.values_list("id", flat=True)
    recent_transactions = (
        Transaction.objects.filter(account_id__in=account_ids)
        .select_related("account")[:8]
    )
    active_loans = request.user.loans.filter(status__in=["ACTIVE", "PENDING", "APPROVED"])
    active_fds = request.user.fixed_deposits.filter(status=FixedDeposit.Status.ACTIVE)

    # Last 30 days money in/out for the dashboard chart.
    since = timezone.now() - timezone.timedelta(days=30)
    flows = (
        Transaction.objects.filter(
            account_id__in=account_ids,
            created_at__gte=since,
            status=Transaction.Status.COMPLETED,
        )
        .values("direction")
        .annotate(total=models.Sum("amount"))
    )
    money_in = money_out = Decimal("0.00")
    for row in flows:
        if row["direction"] == Transaction.Direction.CREDIT:
            money_in = row["total"]
        else:
            money_out = row["total"]

    return render(request, "customer/dashboard.html", {
        "accounts": accounts,
        "total_balance": total_balance,
        "recent_transactions": recent_transactions,
        "active_loans": active_loans,
        "active_fds": active_fds,
        "money_in": money_in,
        "money_out": money_out,
        "kyc_status": request.user.profile.kyc_status,
    })


@customer_required
def account_list(request):
    accounts = request.user.bank_accounts.select_related("account_type")
    return render(request, "customer/account_list.html", {"accounts": accounts})


@customer_required
def account_detail(request, account_number):
    account = get_object_or_404(
        BankAccount, account_number=account_number, user=request.user
    )
    form = StatementFilterForm(request.GET or None)
    transactions = account.transactions.all()
    if form.is_valid():
        if form.cleaned_data.get("start_date"):
            transactions = transactions.filter(
                created_at__date__gte=form.cleaned_data["start_date"]
            )
        if form.cleaned_data.get("end_date"):
            transactions = transactions.filter(
                created_at__date__lte=form.cleaned_data["end_date"]
            )
        if form.cleaned_data.get("direction"):
            transactions = transactions.filter(direction=form.cleaned_data["direction"])

    paginator = Paginator(transactions, 20)
    page = paginator.get_page(request.GET.get("page"))
    return render(request, "customer/account_detail.html", {
        "account": account,
        "transactions": page,
        "filter_form": form,
    })


@customer_required
def download_statement(request, account_number):
    account = get_object_or_404(
        BankAccount, account_number=account_number, user=request.user
    )
    transactions = account.transactions.all().order_by("created_at")
    form = StatementFilterForm(request.GET or None)
    if form.is_valid():
        if form.cleaned_data.get("start_date"):
            transactions = transactions.filter(
                created_at__date__gte=form.cleaned_data["start_date"]
            )
        if form.cleaned_data.get("end_date"):
            transactions = transactions.filter(
                created_at__date__lte=form.cleaned_data["end_date"]
            )

    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = (
        f'attachment; filename="statement_{account.account_number}.csv"'
    )
    writer = csv.writer(response)
    writer.writerow([
        "Date", "Reference", "Description", "Channel",
        "Debit", "Credit", "Balance", "Status",
    ])
    for trx in transactions:
        writer.writerow([
            timezone.localtime(trx.created_at).strftime("%Y-%m-%d %H:%M:%S"),
            trx.reference,
            trx.description,
            trx.get_channel_display(),
            trx.amount if trx.direction == Transaction.Direction.DEBIT else "",
            trx.amount if trx.direction == Transaction.Direction.CREDIT else "",
            trx.balance_after,
            trx.get_status_display(),
        ])
    log_action(request.user, "STATEMENT_DOWNLOAD",
               f"Statement CSV for {account.account_number}", request)
    return response


@customer_required
def transaction_receipt(request, reference):
    transaction = get_object_or_404(
        Transaction.objects.select_related("account"),
        reference=reference,
        account__user=request.user,
    )
    return render(request, "customer/receipt.html", {"trx": transaction})


@pin_setup_required
def transfer(request):
    beneficiaries = request.user.beneficiaries.all()
    if request.method == "POST":
        form = TransferForm(request.user, request.POST)
        if form.is_valid() and _verify_pin_or_error(request, form):
            try:
                entry = services.transfer(
                    form.cleaned_data["source_account"],
                    form.cleaned_data["destination_account"],
                    form.cleaned_data["amount"],
                    narration=form.cleaned_data.get("narration", ""),
                    initiated_by=request.user,
                )
            except services.TransactionError as exc:
                messages.error(request, str(exc))
            else:
                if form.cleaned_data.get("save_beneficiary"):
                    Beneficiary.objects.get_or_create(
                        user=request.user,
                        account_number=form.cleaned_data["destination_account"],
                        defaults={"name": entry.counterparty_name},
                    )
                log_action(request.user, "TRANSFER",
                           f"₦{entry.amount} to {entry.counterparty_account}, "
                           f"ref {entry.reference}", request)
                messages.success(request, "Transfer successful.")
                return redirect("banking:receipt", reference=entry.reference)
    else:
        initial = {}
        if request.GET.get("to"):
            initial["destination_account"] = request.GET["to"]
        form = TransferForm(request.user, initial=initial)
    return render(request, "customer/transfer.html", {
        "form": form, "beneficiaries": beneficiaries,
    })


@customer_required
def lookup_account(request):
    """AJAX endpoint used by the transfer form to resolve an account name."""
    number = request.GET.get("account_number", "").strip()
    if len(number) != 10 or not number.isdigit():
        return JsonResponse({"found": False, "error": "Enter a 10-digit account number."})
    try:
        account = BankAccount.objects.select_related("user").get(account_number=number)
    except BankAccount.DoesNotExist:
        return JsonResponse({"found": False, "error": "Account not found."})
    if account.status != BankAccount.Status.ACTIVE:
        return JsonResponse({"found": False, "error": "This account cannot receive transfers."})
    return JsonResponse({
        "found": True,
        "name": account.user.get_full_name() or account.user.email,
    })


@pin_setup_required
def pay_bills(request):
    if request.method == "POST":
        form = BillPaymentForm(request.user, request.POST)
        if form.is_valid() and _verify_pin_or_error(request, form):
            try:
                payment = services.pay_bill(
                    form.cleaned_data["account"],
                    form.cleaned_data["biller"],
                    form.cleaned_data["customer_reference"],
                    form.cleaned_data["amount"],
                    initiated_by=request.user,
                )
            except services.TransactionError as exc:
                messages.error(request, str(exc))
            else:
                log_action(request.user, "BILL_PAYMENT",
                           f"₦{payment.amount} to {payment.biller}, "
                           f"ref {payment.reference}", request)
                messages.success(request, "Bill payment successful.")
                return redirect("banking:receipt", reference=payment.reference)
    else:
        form = BillPaymentForm(request.user)
    recent_payments = request.user.bill_payments.select_related("biller")[:10]
    return render(request, "customer/pay_bills.html", {
        "form": form, "recent_payments": recent_payments,
    })


@pin_setup_required
def fixed_deposits(request):
    if request.method == "POST":
        form = FixedDepositForm(request.user, request.POST)
        if form.is_valid() and _verify_pin_or_error(request, form):
            try:
                fd = services.open_fixed_deposit(
                    form.cleaned_data["source_account"],
                    form.cleaned_data["principal"],
                    form.cleaned_data["tenor_days"],
                    form.rate_for_tenor(),
                    initiated_by=request.user,
                )
            except services.TransactionError as exc:
                messages.error(request, str(exc))
            else:
                log_action(request.user, "FIXED_DEPOSIT_OPEN",
                           f"₦{fd.principal} for {fd.tenor_days} days, "
                           f"ref {fd.reference}", request)
                messages.success(request, "Fixed deposit opened.")
                return redirect("banking:fixed_deposits")
    else:
        form = FixedDepositForm(request.user)
    deposits = request.user.fixed_deposits.select_related("source_account")
    return render(request, "customer/fixed_deposits.html", {
        "form": form, "deposits": deposits,
    })


@pin_setup_required
def close_fixed_deposit(request, pk):
    fd = get_object_or_404(FixedDeposit, pk=pk, user=request.user)
    if request.method == "POST":
        force_break = request.POST.get("force_break") == "1"
        try:
            services.close_fixed_deposit(
                fd, initiated_by=request.user, force_break=force_break
            )
        except services.TransactionError as exc:
            messages.error(request, str(exc))
        else:
            action = "broken early" if force_break and not fd.is_matured else "matured"
            log_action(request.user, "FIXED_DEPOSIT_CLOSE",
                       f"FD {fd.reference} {action}", request)
            messages.success(request, "Fixed deposit closed and funds credited.")
    return redirect("banking:fixed_deposits")


@customer_required
def beneficiaries(request):
    if request.method == "POST":
        form = BeneficiaryForm(request.POST)
        if form.is_valid():
            beneficiary = form.save(commit=False)
            beneficiary.user = request.user
            if request.user.beneficiaries.filter(
                account_number=beneficiary.account_number
            ).exists():
                messages.error(request, "That beneficiary already exists.")
            else:
                beneficiary.save()
                messages.success(request, "Beneficiary added.")
                return redirect("banking:beneficiaries")
    else:
        form = BeneficiaryForm()
    return render(request, "customer/beneficiaries.html", {
        "form": form,
        "beneficiaries": request.user.beneficiaries.all(),
    })


@customer_required
def delete_beneficiary(request, pk):
    beneficiary = get_object_or_404(Beneficiary, pk=pk, user=request.user)
    if request.method == "POST":
        beneficiary.delete()
        messages.success(request, "Beneficiary removed.")
    return redirect("banking:beneficiaries")
