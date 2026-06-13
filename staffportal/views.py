import json
from decimal import Decimal

from django.contrib import messages
from django.core.paginator import Paginator
from django.db import models
from django.db import transaction as db_transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from accounts.decorators import staff_required
from accounts.models import AuditLog, CustomerProfile, User, log_action
from banking import services as bank_services
from banking.models import (
    AccountType,
    BankAccount,
    Biller,
    FixedDeposit,
    Transaction,
)
from banking.services import TransactionError
from cards.models import Card
from loans import services as loan_services
from loans.models import Loan, LoanProduct
from notifications.services import notify
from support.models import Ticket, TicketMessage

from .forms import (
    AccountOpenForm,
    AccountStatusForm,
    AccountTypeForm,
    AdjustmentForm,
    BillerForm,
    CashRepaymentForm,
    CustomerCreateForm,
    CustomerEditForm,
    KycDecisionForm,
    LoanDecisionForm,
    LoanProductForm,
    PopulateHistoryForm,
    PostingForm,
    ReversalForm,
    SetBalanceForm,
    StaffUserForm,
    TicketReplyForm,
)

MANAGER_UP = (User.Role.MANAGER, User.Role.ADMIN)
ADMIN_ONLY = (User.Role.ADMIN,)


def _paginate(request, queryset, per_page=25):
    return Paginator(queryset, per_page).get_page(request.GET.get("page"))


# --------------------------------------------------------------------------
# Dashboard
# --------------------------------------------------------------------------
@staff_required()
def dashboard(request):
    today = timezone.localdate()
    customer_count = User.objects.filter(role=User.Role.CUSTOMER).count()
    account_count = BankAccount.objects.exclude(status=BankAccount.Status.CLOSED).count()
    total_deposits = BankAccount.objects.aggregate(
        total=models.Sum("balance")
    )["total"] or Decimal("0.00")
    todays_transactions = Transaction.objects.filter(created_at__date=today)
    todays_volume = todays_transactions.aggregate(
        total=models.Sum("amount")
    )["total"] or Decimal("0.00")

    pending_kyc = CustomerProfile.objects.filter(
        kyc_status=CustomerProfile.KycStatus.PENDING,
        user__role=User.Role.CUSTOMER,
    ).count()
    pending_accounts = BankAccount.objects.filter(
        status=BankAccount.Status.PENDING
    ).count()
    pending_loans = Loan.objects.filter(status=Loan.Status.PENDING).count()
    pending_cards = Card.objects.filter(status=Card.Status.REQUESTED).count()
    open_tickets = Ticket.objects.filter(
        status__in=[Ticket.Status.OPEN, Ticket.Status.IN_PROGRESS]
    ).count()

    # 7-day transaction volume for the chart.
    chart_days, chart_values = [], []
    for offset in range(6, -1, -1):
        day = today - timezone.timedelta(days=offset)
        volume = Transaction.objects.filter(
            created_at__date=day, status=Transaction.Status.COMPLETED
        ).aggregate(total=models.Sum("amount"))["total"] or 0
        chart_days.append(day.strftime("%a"))
        chart_values.append(float(volume))

    return render(request, "staff/dashboard.html", {
        "customer_count": customer_count,
        "account_count": account_count,
        "total_deposits": total_deposits,
        "todays_volume": todays_volume,
        "todays_count": todays_transactions.count(),
        "pending_kyc": pending_kyc,
        "pending_accounts": pending_accounts,
        "pending_loans": pending_loans,
        "pending_cards": pending_cards,
        "open_tickets": open_tickets,
        "recent_transactions": Transaction.objects.select_related(
            "account", "account__user"
        )[:10],
        "chart_days": json.dumps(chart_days),
        "chart_values": json.dumps(chart_values),
    })


# --------------------------------------------------------------------------
# Customers
# --------------------------------------------------------------------------
@staff_required()
def customer_list(request):
    query = request.GET.get("q", "").strip()
    kyc = request.GET.get("kyc", "")
    customers = (
        User.objects.filter(role=User.Role.CUSTOMER)
        .select_related("profile")
        .order_by("-date_joined")
    )
    if query:
        customers = customers.filter(
            models.Q(first_name__icontains=query)
            | models.Q(last_name__icontains=query)
            | models.Q(email__icontains=query)
            | models.Q(phone__icontains=query)
            | models.Q(bank_accounts__account_number__icontains=query)
            | models.Q(bank_accounts__iban__icontains=query)
        ).distinct()
    if kyc:
        customers = customers.filter(profile__kyc_status=kyc)
    return render(request, "staff/customer_list.html", {
        "customers": _paginate(request, customers),
        "query": query,
        "kyc": kyc,
        "kyc_choices": CustomerProfile.KycStatus.choices,
    })


@staff_required()
def customer_create(request):
    if request.method == "POST":
        form = CustomerCreateForm(request.POST)
        if form.is_valid():
            data = form.cleaned_data
            with db_transaction.atomic():
                user = User.objects.create_user(
                    email=data["email"],
                    password=data["password"],
                    first_name=data["first_name"],
                    last_name=data["last_name"],
                    phone=data["phone"],
                    role=User.Role.CUSTOMER,
                )
                CustomerProfile.objects.create(
                    user=user,
                    date_of_birth=data.get("date_of_birth"),
                    gender=data.get("gender", ""),
                    address=data.get("address", ""),
                    city=data.get("city", ""),
                    state=data.get("state", ""),
                    national_id_number=data.get("national_id_number", ""),
                    occupation=data.get("occupation", ""),
                    kyc_status=(
                        CustomerProfile.KycStatus.VERIFIED
                        if data.get("mark_kyc_verified")
                        else CustomerProfile.KycStatus.PENDING
                    ),
                )
                account = BankAccount.objects.create(
                    user=user, account_type=data["account_type"]
                )
                if data.get("approve_immediately"):
                    account.status = BankAccount.Status.ACTIVE
                    account.approved_by = request.user
                    account.approved_at = timezone.now()
                    account.review_note = "Reviewed and approved at account opening"
                    account.save(update_fields=[
                        "status", "approved_by", "approved_at", "review_note",
                    ])
            if data.get("approve_immediately") and data.get("opening_deposit"):
                bank_services.deposit(
                    account, data["opening_deposit"],
                    description="Opening deposit",
                    initiated_by=request.user,
                )
            log_action(request.user, "STAFF_CUSTOMER_CREATE",
                       f"Customer {user.email}, account {account.iban} "
                       f"({account.get_status_display()})",
                       request)
            if account.status == BankAccount.Status.PENDING:
                messages.success(
                    request,
                    f"Customer created. Account {account.iban_formatted} is "
                    f"pending approval before it can be used.",
                )
            else:
                messages.success(
                    request,
                    f"Customer created. Account {account.iban_formatted} is active.",
                )
            return redirect("staffportal:customer_detail", pk=user.pk)
    else:
        form = CustomerCreateForm()
    return render(request, "staff/customer_form.html", {
        "form": form, "title": "Create customer", "submit_label": "Create customer",
    })


@staff_required()
def customer_detail(request, pk):
    customer = get_object_or_404(
        User.objects.select_related("profile"), pk=pk, role=User.Role.CUSTOMER
    )
    return render(request, "staff/customer_detail.html", {
        "customer": customer,
        "profile": customer.profile if hasattr(customer, "profile") else None,
        "accounts": customer.bank_accounts.select_related("account_type"),
        "loans": customer.loans.select_related("product")[:10],
        "cards": customer.cards.select_related("account")[:10],
        "tickets": customer.tickets.all()[:10],
        "kyc_form": KycDecisionForm(),
        "account_open_form": AccountOpenForm(),
    })


@staff_required()
def customer_edit(request, pk):
    customer = get_object_or_404(
        User.objects.select_related("profile"), pk=pk, role=User.Role.CUSTOMER
    )
    if request.method == "POST":
        form = CustomerEditForm(request.POST, instance=customer.profile)
        if form.is_valid():
            form.save()
            log_action(request.user, "STAFF_CUSTOMER_EDIT",
                       f"Customer {customer.email}", request)
            messages.success(request, "Customer details updated.")
            return redirect("staffportal:customer_detail", pk=pk)
    else:
        form = CustomerEditForm(instance=customer.profile)
    return render(request, "staff/customer_form.html", {
        "form": form,
        "title": f"Edit {customer.get_full_name()}",
        "submit_label": "Save changes",
    })


@staff_required(*MANAGER_UP)
def customer_toggle_active(request, pk):
    customer = get_object_or_404(User, pk=pk, role=User.Role.CUSTOMER)
    if request.method == "POST":
        customer.is_active = not customer.is_active
        customer.save(update_fields=["is_active"])
        state = "re-activated" if customer.is_active else "deactivated"
        log_action(request.user, "STAFF_CUSTOMER_TOGGLE",
                   f"{customer.email} {state}", request)
        messages.success(request, f"Customer {state}.")
    return redirect("staffportal:customer_detail", pk=pk)


@staff_required(*MANAGER_UP)
def customer_kyc_decision(request, pk):
    customer = get_object_or_404(
        User.objects.select_related("profile"), pk=pk, role=User.Role.CUSTOMER
    )
    profile = customer.profile
    if request.method == "POST":
        form = KycDecisionForm(request.POST)
        if form.is_valid():
            if form.cleaned_data["decision"] == "VERIFY":
                profile.kyc_status = CustomerProfile.KycStatus.VERIFIED
                profile.kyc_rejection_reason = ""
                notify(customer, "Identity verified",
                       "Your KYC documents have been verified. "
                       "You now have full access to all banking services.",
                       "success")
            else:
                profile.kyc_status = CustomerProfile.KycStatus.REJECTED
                profile.kyc_rejection_reason = form.cleaned_data["reason"]
                notify(customer, "KYC verification failed",
                       f"Your KYC submission was rejected: "
                       f"{form.cleaned_data['reason']} "
                       "Please re-upload valid documents.",
                       "danger")
            profile.save(update_fields=["kyc_status", "kyc_rejection_reason"])
            log_action(request.user, "STAFF_KYC_DECISION",
                       f"{customer.email}: {profile.kyc_status}", request)
            messages.success(request, "KYC decision recorded.")
        else:
            messages.error(request, "A reason is required when rejecting KYC.")
    return redirect("staffportal:customer_detail", pk=pk)


@staff_required()
def kyc_queue(request):
    profiles = CustomerProfile.objects.filter(
        kyc_status=CustomerProfile.KycStatus.PENDING,
        user__role=User.Role.CUSTOMER,
    ).select_related("user")
    return render(request, "staff/kyc_queue.html", {
        "profiles": _paginate(request, profiles),
    })


@staff_required()
def account_approval_queue(request):
    accounts = BankAccount.objects.filter(
        status=BankAccount.Status.PENDING
    ).select_related("user", "account_type")
    return render(request, "staff/account_approval_queue.html", {
        "accounts": _paginate(request, accounts),
        "can_approve": request.user.role in MANAGER_UP,
    })


# --------------------------------------------------------------------------
# Accounts & postings
# --------------------------------------------------------------------------
@staff_required()
def account_list(request):
    query = request.GET.get("q", "").strip()
    status = request.GET.get("status", "")
    accounts = BankAccount.objects.select_related("user", "account_type")
    if query:
        accounts = accounts.filter(
            models.Q(account_number__icontains=query)
            | models.Q(iban__icontains=query.replace(" ", ""))
            | models.Q(user__first_name__icontains=query)
            | models.Q(user__last_name__icontains=query)
            | models.Q(user__email__icontains=query)
        )
    if status:
        accounts = accounts.filter(status=status)
    return render(request, "staff/account_list.html", {
        "accounts": _paginate(request, accounts),
        "query": query,
        "status": status,
        "status_choices": BankAccount.Status.choices,
    })


@staff_required()
def account_open(request, customer_pk):
    customer = get_object_or_404(User, pk=customer_pk, role=User.Role.CUSTOMER)
    if request.method == "POST":
        form = AccountOpenForm(request.POST)
        if form.is_valid():
            account = BankAccount.objects.create(
                user=customer, account_type=form.cleaned_data["account_type"]
            )
            approve = form.cleaned_data.get("approve_immediately")
            if approve:
                account.status = BankAccount.Status.ACTIVE
                account.approved_by = request.user
                account.approved_at = timezone.now()
                account.review_note = "Reviewed and approved at account opening"
                account.save(update_fields=[
                    "status", "approved_by", "approved_at", "review_note",
                ])
                if form.cleaned_data.get("opening_deposit"):
                    bank_services.deposit(
                        account, form.cleaned_data["opening_deposit"],
                        description="Opening deposit", initiated_by=request.user,
                    )
                notify(customer, "New account opened",
                       f"A new {account.account_type.name} account "
                       f"({account.iban}) has been opened and activated for you.",
                       "success")
                messages.success(request, f"Account {account.iban_formatted} opened and active.")
            else:
                notify(customer, "New account opened (pending approval)",
                       f"A new {account.account_type.name} account "
                       f"({account.iban}) has been opened and is awaiting approval.")
                messages.success(
                    request,
                    f"Account {account.iban_formatted} opened — pending approval.",
                )
            log_action(request.user, "STAFF_ACCOUNT_OPEN",
                       f"{account.iban} for {customer.email} "
                       f"({account.get_status_display()})", request)
        else:
            messages.error(request, "Could not open account. Check the form values.")
    return redirect("staffportal:customer_detail", pk=customer_pk)


@staff_required(*MANAGER_UP)
def account_approve(request, account_number):
    account = get_object_or_404(BankAccount, account_number=account_number)
    if request.method == "POST":
        try:
            bank_services.approve_account(
                account, initiated_by=request.user,
                note=request.POST.get("note", ""),
            )
        except TransactionError as exc:
            messages.error(request, str(exc))
        else:
            log_action(request.user, "STAFF_ACCOUNT_APPROVE",
                       f"{account.iban} for {account.user.email}", request)
            messages.success(request, f"Account {account.iban_formatted} approved and activated.")
    return redirect("staffportal:account_detail", account_number=account_number)


@staff_required()
def account_detail(request, account_number):
    account = get_object_or_404(
        BankAccount.objects.select_related("user", "account_type"),
        account_number=account_number,
    )
    transactions = account.transactions.select_related("initiated_by")
    return render(request, "staff/account_detail.html", {
        "account": account,
        "transactions": _paginate(request, transactions),
        "posting_form": PostingForm(),
        "adjustment_form": AdjustmentForm(),
        "balance_form": SetBalanceForm(initial={"target_balance": account.balance}),
        "status_form": AccountStatusForm(initial={"status": account.status}),
        "populate_form": PopulateHistoryForm(),
    })


@staff_required(*ADMIN_ONLY)
def account_set_balance(request, account_number):
    account = get_object_or_404(BankAccount, account_number=account_number)
    if request.method == "POST":
        form = SetBalanceForm(request.POST)
        if form.is_valid():
            try:
                entry = bank_services.set_balance(
                    account,
                    form.cleaned_data["target_balance"],
                    description=form.cleaned_data["description"],
                    initiated_by=request.user,
                )
            except TransactionError as exc:
                messages.error(request, str(exc))
            else:
                log_action(request.user, "STAFF_SET_BALANCE",
                           f"{account.iban} set to €{form.cleaned_data['target_balance']} "
                           f"({form.cleaned_data['description']})", request)
                messages.success(
                    request,
                    f"Balance updated to €{entry.balance_after:,.2f}.",
                )
        else:
            messages.error(request, "Enter a target balance and a reason.")
    return redirect("staffportal:account_detail", account_number=account_number)


@staff_required()
def account_post(request, account_number, kind):
    """Teller cash deposit ('deposit') or withdrawal ('withdraw')."""
    account = get_object_or_404(BankAccount, account_number=account_number)
    if request.method == "POST":
        form = PostingForm(request.POST)
        if form.is_valid():
            when = form.cleaned_data.get("value_date")
            try:
                if kind == "deposit":
                    entry = bank_services.deposit(
                        account, form.cleaned_data["amount"],
                        description=form.cleaned_data.get("description") or "Cash deposit",
                        initiated_by=request.user, when=when,
                    )
                else:
                    entry = bank_services.withdraw(
                        account, form.cleaned_data["amount"],
                        description=form.cleaned_data.get("description") or "Cash withdrawal",
                        initiated_by=request.user, when=when,
                    )
            except TransactionError as exc:
                messages.error(request, str(exc))
            else:
                log_action(request.user, f"STAFF_{kind.upper()}",
                           f"€{entry.amount} on {account.account_number}, "
                           f"ref {entry.reference}", request)
                messages.success(
                    request, f"{kind.title()} of €{entry.amount:,.2f} posted."
                )
        else:
            messages.error(request, "Invalid amount.")
    return redirect("staffportal:account_detail", account_number=account_number)


@staff_required(*ADMIN_ONLY)
def account_adjust(request, account_number):
    account = get_object_or_404(BankAccount, account_number=account_number)
    if request.method == "POST":
        form = AdjustmentForm(request.POST)
        if form.is_valid():
            try:
                entry = bank_services.adjustment(
                    account,
                    form.cleaned_data["direction"],
                    form.cleaned_data["amount"],
                    description=form.cleaned_data["description"],
                    initiated_by=request.user,
                    when=form.cleaned_data.get("value_date"),
                )
            except TransactionError as exc:
                messages.error(request, str(exc))
            else:
                log_action(request.user, "STAFF_ADJUSTMENT",
                           f"{entry.direction} €{entry.amount} on "
                           f"{account.account_number}: "
                           f"{form.cleaned_data['description']}", request)
                messages.success(request, "Adjustment posted.")
        else:
            messages.error(request, "Adjustments require an amount and a reason.")
    return redirect("staffportal:account_detail", account_number=account_number)


@staff_required(*ADMIN_ONLY)
def account_populate_history(request, account_number):
    """Generate realistic backdated transactions on an account (admin only)."""
    account = get_object_or_404(BankAccount, account_number=account_number)
    if request.method == "POST":
        form = PopulateHistoryForm(request.POST)
        if form.is_valid():
            try:
                count = bank_services.populate_transaction_history(
                    account,
                    form.cleaned_data["target_balance"],
                    months=int(form.cleaned_data["months"]),
                    initiated_by=request.user,
                )
            except TransactionError as exc:
                messages.error(request, str(exc))
            else:
                log_action(
                    request.user, "STAFF_POPULATE_HISTORY",
                    f"{account.iban}: {count} transactions generated over "
                    f"{form.cleaned_data['months']} months, "
                    f"target €{form.cleaned_data['target_balance']}",
                    request,
                )
                messages.success(
                    request,
                    f"{count} transactions generated. Balance is now "
                    f"€{account.balance:,.2f}.",
                )
        else:
            messages.error(request, "Enter a valid target balance and period.")
    return redirect("staffportal:account_detail", account_number=account_number)


@staff_required(*MANAGER_UP)
def account_status(request, account_number):
    account = get_object_or_404(BankAccount, account_number=account_number)
    if request.method == "POST":
        form = AccountStatusForm(request.POST)
        if form.is_valid():
            new_status = form.cleaned_data["status"]
            if new_status == BankAccount.Status.CLOSED and account.balance != 0:
                messages.error(
                    request,
                    "Accounts must have a zero balance before they can be closed.",
                )
            else:
                account.status = new_status
                account.save(update_fields=["status", "updated_at"])
                reason = form.cleaned_data.get("reason", "")
                level = "warning" if new_status == BankAccount.Status.FROZEN else "info"
                notify(account.user, "Account status changed",
                       f"Your account {account.iban} is now "
                       f"{account.get_status_display().lower()}."
                       + (f" Reason: {reason}" if reason else "")
                       + (" While frozen you can still sign in and view your "
                          "account, but no transactions are possible."
                          if new_status == BankAccount.Status.FROZEN else ""),
                       level=level)
                log_action(request.user, "STAFF_ACCOUNT_STATUS",
                           f"{account.iban} -> {new_status}. {reason}",
                           request)
                messages.success(request, "Account status updated.")
    return redirect("staffportal:account_detail", account_number=account_number)


# --------------------------------------------------------------------------
# Transactions
# --------------------------------------------------------------------------
@staff_required()
def transaction_list(request):
    query = request.GET.get("q", "").strip()
    channel = request.GET.get("channel", "")
    direction = request.GET.get("direction", "")
    transactions = Transaction.objects.select_related("account", "account__user")
    if query:
        transactions = transactions.filter(
            models.Q(reference__icontains=query)
            | models.Q(account__account_number__icontains=query)
            | models.Q(account__iban__icontains=query.replace(" ", ""))
            | models.Q(description__icontains=query)
        )
    if channel:
        transactions = transactions.filter(channel=channel)
    if direction:
        transactions = transactions.filter(direction=direction)
    return render(request, "staff/transaction_list.html", {
        "transactions": _paginate(request, transactions),
        "query": query,
        "channel": channel,
        "direction": direction,
        "channel_choices": Transaction.Channel.choices,
    })


@staff_required(*MANAGER_UP)
def transaction_reverse(request, pk):
    entry = get_object_or_404(Transaction, pk=pk)
    if request.method == "POST":
        form = ReversalForm(request.POST)
        if form.is_valid():
            try:
                reversal = bank_services.reverse_transaction(
                    entry, initiated_by=request.user,
                    reason=form.cleaned_data["reason"],
                )
            except TransactionError as exc:
                messages.error(request, str(exc))
            else:
                log_action(request.user, "STAFF_REVERSAL",
                           f"Reversed {entry.reference} "
                           f"(€{entry.amount} {entry.direction}) -> "
                           f"{reversal.reference}", request)
                messages.success(request, "Transaction reversed.")
        else:
            messages.error(request, "A reason is required to reverse a transaction.")
    return redirect("staffportal:account_detail",
                    account_number=entry.account.account_number)


# --------------------------------------------------------------------------
# Cards
# --------------------------------------------------------------------------
@staff_required()
def card_list(request):
    status = request.GET.get("status", "")
    cards = Card.objects.select_related("user", "account")
    if status:
        cards = cards.filter(status=status)
    return render(request, "staff/card_list.html", {
        "cards": _paginate(request, cards),
        "status": status,
        "status_choices": Card.Status.choices,
    })


@staff_required()
def card_issue(request, pk):
    card = get_object_or_404(Card, pk=pk)
    if request.method == "POST":
        if card.status != Card.Status.REQUESTED:
            messages.error(request, "Only requested cards can be issued.")
        else:
            card.issue(request.user)
            notify(card.user, "Your card is ready",
                   f"Your {card.get_scheme_display()} card ending in "
                   f"{card.card_number[-4:]} is now active.",
                   "success")
            log_action(request.user, "STAFF_CARD_ISSUE",
                       f"{card.scheme} {card.masked_number} for {card.user.email}",
                       request)
            messages.success(request, "Card issued and activated.")
    return redirect("staffportal:card_list")


@staff_required(*MANAGER_UP)
def card_block(request, pk):
    card = get_object_or_404(Card, pk=pk)
    if request.method == "POST":
        card.status = Card.Status.BLOCKED
        card.save(update_fields=["status"])
        notify(card.user, "Card blocked",
               f"Your card {card.masked_number} was blocked by the bank. "
               "Contact support for details.", "danger")
        log_action(request.user, "STAFF_CARD_BLOCK",
                   f"{card.masked_number} for {card.user.email}", request)
        messages.success(request, "Card blocked.")
    return redirect("staffportal:card_list")


# --------------------------------------------------------------------------
# Loans
# --------------------------------------------------------------------------
@staff_required()
def loan_list(request):
    status = request.GET.get("status", "")
    loans = Loan.objects.select_related("user", "product", "disbursement_account")
    if status:
        loans = loans.filter(status=status)
    return render(request, "staff/loan_list.html", {
        "loans": _paginate(request, loans),
        "status": status,
        "status_choices": Loan.Status.choices,
    })


@staff_required()
def loan_detail(request, pk):
    loan = get_object_or_404(
        Loan.objects.select_related("user", "product", "disbursement_account"),
        pk=pk,
    )
    return render(request, "staff/loan_detail.html", {
        "loan": loan,
        "repayments": loan.repayments.all(),
        "decision_form": LoanDecisionForm(),
        "repayment_form": CashRepaymentForm(),
    })


@staff_required(*MANAGER_UP)
def loan_decide(request, pk):
    loan = get_object_or_404(Loan, pk=pk)
    if request.method == "POST":
        form = LoanDecisionForm(request.POST)
        if form.is_valid():
            if loan.status != Loan.Status.PENDING:
                messages.error(request, "This application has already been decided.")
            elif form.cleaned_data["decision"] == "APPROVE":
                try:
                    loan_services.disburse_loan(loan, initiated_by=request.user)
                except TransactionError as exc:
                    messages.error(request, str(exc))
                else:
                    log_action(request.user, "STAFF_LOAN_APPROVE",
                               f"Loan #{loan.pk} €{loan.amount} for "
                               f"{loan.user.email}", request)
                    messages.success(request, "Loan approved and disbursed.")
            else:
                loan.status = Loan.Status.REJECTED
                loan.decision_note = form.cleaned_data["note"]
                loan.decided_by = request.user
                loan.decided_at = timezone.now()
                loan.save(update_fields=[
                    "status", "decision_note", "decided_by", "decided_at",
                ])
                notify(loan.user, "Loan application declined",
                       f"Your {loan.product.name} application was declined: "
                       f"{loan.decision_note}", "danger")
                log_action(request.user, "STAFF_LOAN_REJECT",
                           f"Loan #{loan.pk} for {loan.user.email}", request)
                messages.success(request, "Loan rejected.")
        else:
            messages.error(request, "A note is required when rejecting a loan.")
    return redirect("staffportal:loan_detail", pk=pk)


@staff_required()
def loan_cash_repayment(request, pk):
    """Cash received in branch: deposit it to the customer's account, then
    immediately apply it to the loan so the ledger stays consistent."""
    loan = get_object_or_404(Loan, pk=pk)
    if request.method == "POST":
        form = CashRepaymentForm(request.POST)
        if form.is_valid():
            amount = form.cleaned_data["amount"]
            try:
                with db_transaction.atomic():
                    bank_services.deposit(
                        loan.disbursement_account, amount,
                        description="Cash loan repayment received",
                        initiated_by=request.user,
                    )
                    loan_services.repay_loan(
                        loan, loan.disbursement_account, amount,
                        initiated_by=request.user,
                    )
            except TransactionError as exc:
                messages.error(request, str(exc))
            else:
                log_action(request.user, "STAFF_LOAN_REPAYMENT",
                           f"€{amount} cash on loan #{loan.pk}", request)
                messages.success(request, "Cash repayment recorded.")
        else:
            messages.error(request, "Invalid amount.")
    return redirect("staffportal:loan_detail", pk=pk)


# --------------------------------------------------------------------------
# Fixed deposits
# --------------------------------------------------------------------------
@staff_required()
def fixed_deposit_list(request):
    deposits = FixedDeposit.objects.select_related("user", "source_account")
    return render(request, "staff/fixed_deposit_list.html", {
        "deposits": _paginate(request, deposits),
    })


@staff_required(*MANAGER_UP)
def fixed_deposit_close(request, pk):
    fd = get_object_or_404(FixedDeposit, pk=pk)
    if request.method == "POST":
        force_break = request.POST.get("force_break") == "1"
        try:
            bank_services.close_fixed_deposit(
                fd, initiated_by=request.user, force_break=force_break
            )
        except TransactionError as exc:
            messages.error(request, str(exc))
        else:
            log_action(request.user, "STAFF_FD_CLOSE",
                       f"FD {fd.reference}", request)
            messages.success(request, "Fixed deposit closed and paid out.")
    return redirect("staffportal:fixed_deposit_list")


# --------------------------------------------------------------------------
# Support tickets
# --------------------------------------------------------------------------
@staff_required()
def ticket_list(request):
    status = request.GET.get("status", "")
    tickets = Ticket.objects.select_related("user", "assigned_to")
    if status:
        tickets = tickets.filter(status=status)
    return render(request, "staff/ticket_list.html", {
        "tickets": _paginate(request, tickets),
        "status": status,
        "status_choices": Ticket.Status.choices,
    })


@staff_required()
def ticket_detail(request, pk):
    ticket = get_object_or_404(Ticket.objects.select_related("user"), pk=pk)
    if request.method == "POST":
        form = TicketReplyForm(request.POST)
        if form.is_valid():
            if form.cleaned_data.get("body"):
                TicketMessage.objects.create(
                    ticket=ticket, sender=request.user,
                    body=form.cleaned_data["body"],
                )
                notify(ticket.user, f"Reply on ticket #{ticket.pk}",
                       f"Support replied to your ticket “{ticket.subject}”.")
            ticket.status = form.cleaned_data["status"]
            ticket.assigned_to = ticket.assigned_to or request.user
            ticket.save()
            log_action(request.user, "STAFF_TICKET_UPDATE",
                       f"Ticket #{ticket.pk} -> {ticket.status}", request)
            messages.success(request, "Ticket updated.")
            return redirect("staffportal:ticket_detail", pk=pk)
    else:
        form = TicketReplyForm(initial={"status": ticket.status})
    return render(request, "staff/ticket_detail.html", {
        "ticket": ticket,
        "thread": ticket.messages.select_related("sender"),
        "form": form,
    })


# --------------------------------------------------------------------------
# Configuration: account types, loan products, billers
# --------------------------------------------------------------------------
def _config_crud(request, model, form_class, pk, list_url, title_singular):
    instance = get_object_or_404(model, pk=pk) if pk else None
    if request.method == "POST":
        form = form_class(request.POST, instance=instance)
        if form.is_valid():
            obj = form.save()
            log_action(request.user, "STAFF_CONFIG_SAVE",
                       f"{title_singular}: {obj}", request)
            messages.success(request, f"{title_singular} saved.")
            return redirect(list_url)
    else:
        form = form_class(instance=instance)
    return render(request, "staff/config_form.html", {
        "form": form,
        "title": (f"Edit {title_singular.lower()}" if instance
                  else f"New {title_singular.lower()}"),
        "back_url": list_url,
    })


@staff_required(*ADMIN_ONLY)
def account_type_list(request):
    return render(request, "staff/config_list.html", {
        "title": "Account types",
        "headers": ["Name", "Code", "Interest", "Min balance", "Daily limit", "Active"],
        "rows": [
            {
                "pk": t.pk,
                "cells": [t.name, t.code, f"{t.interest_rate}%",
                          f"€{t.minimum_balance:,.2f}",
                          f"€{t.daily_transfer_limit:,.2f}",
                          "Yes" if t.is_active else "No"],
            }
            for t in AccountType.objects.all()
        ],
        "edit_url_name": "staffportal:account_type_edit",
        "create_url_name": "staffportal:account_type_create",
    })


@staff_required(*ADMIN_ONLY)
def account_type_edit(request, pk=None):
    return _config_crud(request, AccountType, AccountTypeForm, pk,
                        "staffportal:account_type_list", "Account type")


@staff_required(*ADMIN_ONLY)
def loan_product_list(request):
    return render(request, "staff/config_list.html", {
        "title": "Loan products",
        "headers": ["Name", "Rate", "Amount range", "Tenor", "Active"],
        "rows": [
            {
                "pk": p.pk,
                "cells": [p.name, f"{p.interest_rate}%",
                          f"€{p.min_amount:,.0f} – €{p.max_amount:,.0f}",
                          f"{p.min_tenor_months}–{p.max_tenor_months} months",
                          "Yes" if p.is_active else "No"],
            }
            for p in LoanProduct.objects.all()
        ],
        "edit_url_name": "staffportal:loan_product_edit",
        "create_url_name": "staffportal:loan_product_create",
    })


@staff_required(*ADMIN_ONLY)
def loan_product_edit(request, pk=None):
    return _config_crud(request, LoanProduct, LoanProductForm, pk,
                        "staffportal:loan_product_list", "Loan product")


@staff_required(*ADMIN_ONLY)
def biller_list(request):
    return render(request, "staff/config_list.html", {
        "title": "Billers",
        "headers": ["Name", "Category", "Customer ID label", "Active"],
        "rows": [
            {
                "pk": b.pk,
                "cells": [b.name, b.get_category_display(), b.customer_id_label,
                          "Yes" if b.is_active else "No"],
            }
            for b in Biller.objects.all()
        ],
        "edit_url_name": "staffportal:biller_edit",
        "create_url_name": "staffportal:biller_create",
    })


@staff_required(*ADMIN_ONLY)
def biller_edit(request, pk=None):
    return _config_crud(request, Biller, BillerForm, pk,
                        "staffportal:biller_list", "Biller")


# --------------------------------------------------------------------------
# Staff management & audit
# --------------------------------------------------------------------------
@staff_required(*ADMIN_ONLY)
def staff_list(request):
    staff = User.objects.exclude(role=User.Role.CUSTOMER).order_by("role", "email")
    if request.method == "POST":
        form = StaffUserForm(request.POST)
        if form.is_valid():
            data = form.cleaned_data
            User.objects.create_user(
                email=data["email"],
                password=data["password"],
                first_name=data["first_name"],
                last_name=data["last_name"],
                phone=data.get("phone", ""),
                role=data["role"],
                is_staff=True,
            )
            log_action(request.user, "STAFF_USER_CREATE",
                       f"{data['email']} as {data['role']}", request)
            messages.success(request, "Staff member created.")
            return redirect("staffportal:staff_list")
    else:
        form = StaffUserForm()
    return render(request, "staff/staff_list.html", {"staff": staff, "form": form})


@staff_required(*ADMIN_ONLY)
def staff_toggle_active(request, pk):
    member = get_object_or_404(User, pk=pk)
    if member == request.user:
        messages.error(request, "You cannot deactivate your own account.")
    elif request.method == "POST":
        member.is_active = not member.is_active
        member.save(update_fields=["is_active"])
        state = "re-activated" if member.is_active else "deactivated"
        log_action(request.user, "STAFF_USER_TOGGLE",
                   f"{member.email} {state}", request)
        messages.success(request, f"Staff member {state}.")
    return redirect("staffportal:staff_list")


@staff_required(*MANAGER_UP)
def audit_log(request):
    query = request.GET.get("q", "").strip()
    entries = AuditLog.objects.select_related("actor")
    if query:
        entries = entries.filter(
            models.Q(action__icontains=query)
            | models.Q(description__icontains=query)
            | models.Q(actor__email__icontains=query)
        )
    return render(request, "staff/audit_log.html", {
        "entries": _paginate(request, entries, 50),
        "query": query,
    })
