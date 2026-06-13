"""
Posting engine: every balance movement in the bank goes through this module
inside a database transaction with row locks, so balances and the ledger can
never disagree.
"""
from decimal import Decimal

from django.db import transaction as db_transaction
from django.utils import timezone

from notifications.services import notify

from .models import (
    BankAccount,
    BillPayment,
    FixedDeposit,
    Transaction,
    generate_reference,
    normalise_iban,
)

TWO_PLACES = Decimal("0.01")


class TransactionError(Exception):
    """Raised for any business-rule violation while posting."""


def _quantize(amount):
    return Decimal(amount).quantize(TWO_PLACES)


def _locked(account):
    return BankAccount.objects.select_for_update().get(pk=account.pk)


def _require_active(account, action="transact on"):
    """Block any money movement unless the account is fully active.

    Pending (awaiting approval), frozen, dormant and closed accounts can all be
    viewed by the customer but cannot move money."""
    if account.status == BankAccount.Status.PENDING:
        raise TransactionError(
            f"Account {account.iban} is awaiting approval by the bank and cannot "
            f"be used yet."
        )
    if account.status == BankAccount.Status.FROZEN:
        raise TransactionError(
            f"Account {account.iban} is frozen. No transactions are permitted "
            f"while a freeze is in place — please contact the bank."
        )
    if account.status != BankAccount.Status.ACTIVE:
        raise TransactionError(
            f"Account {account.iban} is {account.get_status_display().lower()}; "
            f"cannot {action} it."
        )


def _post(account, direction, channel, amount, *, reference, description="",
          counterparty_name="", counterparty_account="", counterparty_bank="",
          initiated_by=None, enforce_minimum=True, when=None):
    """Apply one ledger entry to a locked account row. Must be called inside
    an atomic block with the account already locked.

    ``when`` optionally sets the entry's value date (used by staff to record a
    posting that took place on an earlier date)."""
    amount = _quantize(amount)
    if amount <= 0:
        raise TransactionError("Amount must be greater than zero.")

    if direction == Transaction.Direction.DEBIT:
        floor = account.account_type.minimum_balance if enforce_minimum else Decimal("0.00")
        if account.balance - amount < floor:
            raise TransactionError("Insufficient funds.")
        account.balance -= amount
    else:
        account.balance += amount
    account.save(update_fields=["balance", "updated_at"])

    extra = {}
    if when is not None:
        if timezone.is_naive(when):
            when = timezone.make_aware(when, timezone.get_current_timezone())
        extra["created_at"] = when

    return Transaction.objects.create(
        account=account,
        direction=direction,
        channel=channel,
        amount=amount,
        balance_after=account.balance,
        reference=reference,
        description=description,
        counterparty_name=counterparty_name,
        counterparty_account=counterparty_account,
        counterparty_bank=counterparty_bank,
        initiated_by=initiated_by,
        **extra,
    )


def _check_transfer_limit(account, amount):
    limit = account.account_type.daily_transfer_limit
    if account.amount_transferred_today() + amount > limit:
        raise TransactionError(
            f"This payment exceeds the daily transfer limit of "
            f"€{limit:,.2f} for this account type."
        )


@db_transaction.atomic
def transfer(source, destination_iban, amount, *, narration="", initiated_by=None):
    """Make a SEPA credit transfer between two accounts in this bank."""
    amount = _quantize(amount)
    source = _locked(source)
    _require_active(source, "transfer from")

    destination_iban = normalise_iban(destination_iban)
    try:
        destination = BankAccount.objects.select_for_update().get(
            iban=destination_iban
        )
    except BankAccount.DoesNotExist:
        raise TransactionError("No account was found for that IBAN.")

    if destination.pk == source.pk:
        raise TransactionError("You cannot transfer to the same account.")
    _require_active(destination, "transfer to")
    _check_transfer_limit(source, amount)

    reference = generate_reference("TRF")
    sender_name = source.user.get_full_name() or source.user.email
    receiver_name = destination.user.get_full_name() or destination.user.email

    debit = _post(
        source, Transaction.Direction.DEBIT, Transaction.Channel.TRANSFER, amount,
        reference=reference,
        description=narration or f"SEPA transfer to {receiver_name}",
        counterparty_name=receiver_name,
        counterparty_account=destination.iban,
        initiated_by=initiated_by,
    )
    _post(
        destination, Transaction.Direction.CREDIT, Transaction.Channel.TRANSFER, amount,
        reference=reference,
        description=narration or f"SEPA transfer from {sender_name}",
        counterparty_name=sender_name,
        counterparty_account=source.iban,
        initiated_by=initiated_by,
    )

    notify(
        source.user, "Debit notification",
        f"€{amount:,.2f} was sent to {receiver_name} ({destination.iban}). "
        f"Reference: {reference}.",
        level="warning",
    )
    notify(
        destination.user, "Credit notification",
        f"€{amount:,.2f} was received from {sender_name}. Reference: {reference}.",
        level="success",
    )
    return debit


@db_transaction.atomic
def deposit(account, amount, *, description="Cash deposit", initiated_by=None, when=None):
    """Teller/manual cash deposit posted from the staff portal."""
    account = _locked(account)
    _require_active(account, "deposit into")
    entry = _post(
        account, Transaction.Direction.CREDIT, Transaction.Channel.DEPOSIT,
        amount, reference=generate_reference("DEP"), description=description,
        initiated_by=initiated_by, when=when,
    )
    notify(
        account.user, "Credit alert",
        f"€{entry.amount:,.2f} deposit posted to {account.iban}. "
        f"Ref: {entry.reference}.",
        level="success",
    )
    return entry


@db_transaction.atomic
def withdraw(account, amount, *, description="Cash withdrawal", initiated_by=None, when=None):
    """Teller/manual cash withdrawal posted from the staff portal."""
    account = _locked(account)
    _require_active(account, "withdraw from")
    entry = _post(
        account, Transaction.Direction.DEBIT, Transaction.Channel.WITHDRAWAL,
        amount, reference=generate_reference("WDL"), description=description,
        initiated_by=initiated_by, when=when,
    )
    notify(
        account.user, "Debit alert",
        f"€{entry.amount:,.2f} withdrawn from {account.iban}. "
        f"Ref: {entry.reference}.",
        level="warning",
    )
    return entry


@db_transaction.atomic
def adjustment(account, direction, amount, *, description, initiated_by, when=None):
    """Manual ledger adjustment (admin only) — e.g. corrections."""
    account = _locked(account)
    entry = _post(
        account, direction, Transaction.Channel.ADJUSTMENT, amount,
        reference=generate_reference("ADJ"), description=description,
        initiated_by=initiated_by, enforce_minimum=False, when=when,
    )
    notify(
        account.user, "Account adjustment",
        f"An adjustment of €{entry.amount:,.2f} ({direction.lower()}) was posted "
        f"to {account.iban}: {description}",
    )
    return entry


@db_transaction.atomic
def pay_bill(account, biller, customer_reference, amount, *, initiated_by=None):
    amount = _quantize(amount)
    account = _locked(account)
    _require_active(account, "pay bills from")
    _check_transfer_limit(account, amount)

    reference = generate_reference("BIL")
    _post(
        account, Transaction.Direction.DEBIT, Transaction.Channel.BILL_PAYMENT,
        amount, reference=reference,
        description=f"{biller.name} - {customer_reference}",
        counterparty_name=biller.name,
        initiated_by=initiated_by,
    )
    payment = BillPayment.objects.create(
        user=account.user, account=account, biller=biller,
        customer_reference=customer_reference, amount=amount, reference=reference,
    )
    notify(
        account.user, "Bill payment successful",
        f"€{amount:,.2f} paid to {biller.name} ({customer_reference}). "
        f"Ref: {reference}.",
        level="success",
    )
    return payment


@db_transaction.atomic
def open_fixed_deposit(account, principal, tenor_days, interest_rate, *, initiated_by=None):
    principal = _quantize(principal)
    account = _locked(account)
    _require_active(account, "open a fixed deposit from")

    reference = generate_reference("FXD")
    _post(
        account, Transaction.Direction.DEBIT, Transaction.Channel.FIXED_DEPOSIT,
        principal, reference=reference,
        description=f"Fixed deposit ({tenor_days} days @ {interest_rate}%)",
        initiated_by=initiated_by,
    )
    fd = FixedDeposit.objects.create(
        user=account.user,
        source_account=account,
        reference=reference,
        principal=principal,
        interest_rate=interest_rate,
        tenor_days=tenor_days,
        maturity_date=timezone.localdate() + timezone.timedelta(days=tenor_days),
    )
    notify(
        account.user, "Fixed deposit opened",
        f"€{principal:,.2f} locked for {tenor_days} days at {interest_rate}% p.a. "
        f"Expected payout €{fd.expected_payout:,.2f} on {fd.maturity_date:%d %b %Y}.",
        level="success",
    )
    return fd


@db_transaction.atomic
def close_fixed_deposit(fd, *, initiated_by=None, force_break=False):
    """Pay out a fixed deposit. Early break forfeits the interest."""
    fd = FixedDeposit.objects.select_for_update().get(pk=fd.pk)
    if fd.status != FixedDeposit.Status.ACTIVE:
        raise TransactionError("This fixed deposit is already closed.")
    if not fd.is_matured and not force_break:
        raise TransactionError("This fixed deposit has not matured yet.")

    account = _locked(fd.source_account)
    _require_active(account, "pay a fixed deposit into")

    if fd.is_matured:
        payout = fd.expected_payout
        fd.status = FixedDeposit.Status.MATURED
        description = f"Fixed deposit {fd.reference} matured"
    else:
        payout = fd.principal
        fd.status = FixedDeposit.Status.BROKEN
        description = f"Fixed deposit {fd.reference} broken early (interest forfeited)"

    _post(
        account, Transaction.Direction.CREDIT, Transaction.Channel.FIXED_DEPOSIT,
        payout, reference=generate_reference("FXC"), description=description,
        initiated_by=initiated_by,
    )
    fd.payout_amount = payout
    fd.closed_at = timezone.now()
    fd.save(update_fields=["status", "payout_amount", "closed_at"])
    notify(
        fd.user, "Fixed deposit closed",
        f"€{payout:,.2f} from fixed deposit {fd.reference} has been credited to "
        f"{account.iban}.",
        level="success",
    )
    return fd


@db_transaction.atomic
def reverse_transaction(entry, *, initiated_by, reason=""):
    """Reverse a single completed ledger entry (staff only). For transfers,
    reverse each leg separately so partial corrections stay possible."""
    entry = Transaction.objects.select_for_update().get(pk=entry.pk)
    if entry.status != Transaction.Status.COMPLETED:
        raise TransactionError("Only completed transactions can be reversed.")

    account = _locked(entry.account)
    opposite = (
        Transaction.Direction.CREDIT
        if entry.direction == Transaction.Direction.DEBIT
        else Transaction.Direction.DEBIT
    )
    reversal = _post(
        account, opposite, Transaction.Channel.REVERSAL, entry.amount,
        reference=generate_reference("RVS"),
        description=f"Reversal of {entry.reference}" + (f": {reason}" if reason else ""),
        counterparty_name=entry.counterparty_name,
        counterparty_account=entry.counterparty_account,
        initiated_by=initiated_by,
        enforce_minimum=False,
    )
    entry.status = Transaction.Status.REVERSED
    entry.reversed_by = reversal
    entry.save(update_fields=["status", "reversed_by"])
    notify(
        account.user, "Transaction reversed",
        f"Transaction {entry.reference} of €{entry.amount:,.2f} on "
        f"{account.iban} has been reversed.",
    )
    return reversal


@db_transaction.atomic
def set_balance(account, target_balance, *, description, initiated_by):
    """Set an account's balance to an exact figure (admin only).

    The difference is posted to the ledger as an adjustment so the change is
    fully auditable and statements continue to reconcile."""
    target = _quantize(target_balance)
    if target < 0:
        raise TransactionError("Balance cannot be negative.")
    account = _locked(account)
    delta = target - account.balance
    if delta == 0:
        raise TransactionError("The balance is already at that figure.")
    direction = (
        Transaction.Direction.CREDIT if delta > 0 else Transaction.Direction.DEBIT
    )
    entry = _post(
        account, direction, Transaction.Channel.ADJUSTMENT, abs(delta),
        reference=generate_reference("ADJ"),
        description=description or "Balance correction",
        initiated_by=initiated_by, enforce_minimum=False,
    )
    notify(
        account.user, "Account adjustment",
        f"Your balance on {account.iban} was adjusted to €{target:,.2f}: "
        f"{description}",
    )
    return entry


@db_transaction.atomic
def approve_account(account, *, initiated_by, note=""):
    """Approve a pending account so it can be used."""
    account = _locked(account)
    if account.status != BankAccount.Status.PENDING:
        raise TransactionError("This account is not awaiting approval.")
    account.status = BankAccount.Status.ACTIVE
    account.approved_by = initiated_by
    account.approved_at = timezone.now()
    account.review_note = note
    account.save(update_fields=[
        "status", "approved_by", "approved_at", "review_note", "updated_at",
    ])
    notify(
        account.user, "Account approved",
        f"Good news — your account {account.iban} has passed review and is now "
        f"active. You can begin banking right away.",
        level="success",
    )
    return account
