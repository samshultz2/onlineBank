"""
Posting engine: every balance movement in the bank goes through this module
inside a database transaction with row locks, so balances and the ledger can
never disagree.
"""
import random
from datetime import timedelta as dt_timedelta
from decimal import Decimal

from django.db import transaction as db_transaction
from django.utils import timezone

from notifications.services import notify

from .models import (
    BankAccount,
    BillPayment,
    FixedDeposit,
    StandingOrder,
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
        if enforce_minimum:
            floor = account.account_type.minimum_balance
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


@db_transaction.atomic
def execute_standing_order(order, *, on_date=None):
    """Run one occurrence of a standing order: attempt the transfer, then move
    the schedule forward. A failed attempt (e.g. insufficient funds) is recorded
    and the customer is notified, and the occurrence is skipped — exactly as a
    real bank treats a missed standing-order payment.

    Returns the created debit Transaction on success, or None on a skipped run.
    """
    order = StandingOrder.objects.select_for_update().get(pk=order.pk)
    if order.status != StandingOrder.Status.ACTIVE:
        return None

    when = on_date or timezone.localdate()
    narration = order.narration or f"Standing order to {order.beneficiary_name}"
    entry = None
    try:
        entry = transfer(
            order.source_account,
            order.destination_iban,
            order.amount,
            narration=narration,
            initiated_by=order.user,
        )
    except TransactionError as exc:
        order.last_error = str(exc)[:255]
        notify(
            order.user, "Standing order failed",
            f"Your scheduled payment of €{order.amount:,.2f} to "
            f"{order.beneficiary_name} could not be made: {exc} "
            f"The payment was skipped.",
            level="danger",
        )
    else:
        order.last_error = ""
        order.executions_count += 1

    order.last_run_at = timezone.now()
    order.advance_schedule()
    order.save(update_fields=[
        "last_error", "executions_count", "last_run_at",
        "next_run_date", "status", "updated_at",
    ])
    return entry


def process_due_standing_orders(as_of=None, *, stdout=None):
    """Execute every active standing order whose next run date has arrived.

    Each order runs in its own transaction so one failure never blocks the rest.
    Designed to be called once a day from a cron job / systemd timer.
    """
    as_of = as_of or timezone.localdate()
    due = StandingOrder.objects.filter(
        status=StandingOrder.Status.ACTIVE, next_run_date__lte=as_of
    ).order_by("next_run_date")

    processed = succeeded = failed = 0
    # Re-fetch each order by id so a long catch-up run cannot operate on stale rows.
    for order_id in list(due.values_list("pk", flat=True)):
        order = StandingOrder.objects.get(pk=order_id)
        # Catch up on any back-dated runs (e.g. if the worker was down for days).
        while (
            order.status == StandingOrder.Status.ACTIVE
            and order.next_run_date <= as_of
        ):
            run_date = order.next_run_date
            entry = execute_standing_order(order, on_date=run_date)
            processed += 1
            if entry is not None:
                succeeded += 1
            else:
                failed += 1
            order.refresh_from_db()
        if stdout:
            stdout(f"Processed standing order #{order_id} ({order.beneficiary_name}).")

    return {"processed": processed, "succeeded": succeeded, "failed": failed}


@db_transaction.atomic
def populate_transaction_history(account, target_balance, *, months=6, initiated_by):
    """Generate realistic-looking backdated transaction history on an active account.

    Posts a mix of European banking debits (rent, utilities, groceries, etc.) and
    monthly salary credits spread across the last ``months`` months.  The salary
    amounts are calculated so the account lands at approximately ``target_balance``
    after all postings.
    """
    target = _quantize(Decimal(str(target_balance)))
    if target < 0:
        raise TransactionError("Target balance cannot be negative.")
    months = int(months)
    if not 1 <= months <= 24:
        raise TransactionError("Period must be between 1 and 24 months.")

    account = _locked(account)
    _require_active(account, "populate history on")

    rng = random.Random()
    now = timezone.now()

    DEBIT = Transaction.Direction.DEBIT
    CREDIT = Transaction.Direction.CREDIT
    TRANSFER = Transaction.Channel.TRANSFER
    BILL = Transaction.Channel.BILL_PAYMENT
    WITHDRAWAL = Transaction.Channel.WITHDRAWAL

    def eur(lo, hi):
        return Decimal(str(round(rng.uniform(lo, hi), 2)))

    def when_in_period(month_idx, day_lo, day_hi):
        base = now - dt_timedelta(days=(months - month_idx) * 30)
        return base + dt_timedelta(
            days=rng.randint(day_lo, min(day_hi, 29)),
            hours=rng.randint(8, 20),
            minutes=rng.randint(0, 59),
        )

    employers = [
        "Siemens AG", "SAP SE", "BMW Group", "Bosch GmbH",
        "Allianz SE", "Volkswagen AG", "Deutsche Telekom AG",
        "Lufthansa Group", "BASF SE", "Continental AG",
    ]
    grocery_stores = [
        "REWE Supermarkt", "EDEKA Markt", "Aldi Süd",
        "Lidl", "Kaufland", "Penny Markt",
    ]
    cafes = ["Starbucks", "Café Einstein", "Backwerk", "Nordsee", "Subway", "BackFactory"]
    employer = rng.choice(employers)

    # Build raw debit entries (salary added later)
    entries = []  # [(when, desc, channel, direction, amount), ...]

    for m in range(months):
        # Fixed monthly debits posted on specific days of the 30-day block
        entries.append((when_in_period(m, 3, 4),   "Kaltmiete + Nebenkosten",         TRANSFER,   DEBIT, eur(720,  1200)))
        entries.append((when_in_period(m, 4, 6),   "E.ON Energie GmbH",               BILL,       DEBIT, eur(75,   165)))
        entries.append((when_in_period(m, 5, 7),   "DEVK Versicherungen",             TRANSFER,   DEBIT, eur(50,   130)))
        entries.append((when_in_period(m, 7, 9),   "Vodafone GmbH",                   BILL,       DEBIT, eur(24,   49)))
        entries.append((when_in_period(m, 9, 11),  "BVG Monatskarte",                 TRANSFER,   DEBIT, eur(29,   86)))
        entries.append((when_in_period(m, 13, 15), "Netflix International B.V.",       TRANSFER,   DEBIT, eur(12.99, 17.99)))
        entries.append((when_in_period(m, 13, 15), "Spotify AB",                       TRANSFER,   DEBIT, Decimal("9.99")))
        entries.append((when_in_period(m, 18, 20), "Rundfunkbeitrag ARD ZDF",          TRANSFER,   DEBIT, Decimal("18.36")))

        # Weekly grocery shopping (3–4 times per month)
        for _ in range(rng.randint(3, 4)):
            entries.append((when_in_period(m, 3, 27), rng.choice(grocery_stores), TRANSFER, DEBIT, eur(28, 115)))

        # Café / restaurant (2–3 times per month)
        for _ in range(rng.randint(2, 3)):
            entries.append((when_in_period(m, 3, 27), rng.choice(cafes), TRANSFER, DEBIT, eur(9, 52)))

        # Occasional debits (probabilistic)
        if rng.random() < 0.75:
            entries.append((when_in_period(m, 5, 25), "Amazon.de",                    TRANSFER,   DEBIT, eur(14, 185)))
        if rng.random() < 0.60:
            entries.append((when_in_period(m, 5, 25), "Bargeldauszahlung",            WITHDRAWAL, DEBIT, eur(100, 300)))
        if rng.random() < 0.40:
            entries.append((when_in_period(m, 5, 25), "Apotheke am Markt",            TRANSFER,   DEBIT, eur(7, 46)))
        if rng.random() < 0.30:
            entries.append((when_in_period(m, 5, 25), "Deutsche Bahn AG",             TRANSFER,   DEBIT, eur(29, 220)))
        if rng.random() < 0.25:
            entries.append((when_in_period(m, 5, 25), "Zalando SE",                   TRANSFER,   DEBIT, eur(28, 155)))
        if rng.random() < 0.20:
            entries.append((when_in_period(m, 5, 25), "PayPal Europe S.à r.l.",       TRANSFER,   DEBIT, eur(15, 120)))

    # How much net change do we need?
    total_debits = sum(amt for _, _, _, d, amt in entries if d == DEBIT)
    needed_net = target - account.balance          # positive → need credits; negative → need drain
    total_credits_needed = (total_debits + needed_net).quantize(TWO_PLACES)

    if total_credits_needed > 0:
        # Distribute as monthly salary credits arriving on day 1–2 of each period
        base_salary = (total_credits_needed / months).quantize(TWO_PLACES)
        running_salary = Decimal("0.00")

        for m in range(months):
            salary_when = when_in_period(m, 1, 2)
            month_label = (now - dt_timedelta(days=(months - m) * 30)).strftime("%b %Y")

            if m < months - 1:
                jitter = eur(-0.03, 0.03) * base_salary
                month_salary = max((base_salary + jitter).quantize(TWO_PLACES), Decimal("0.01"))
            else:
                month_salary = (total_credits_needed - running_salary).quantize(TWO_PLACES)
                if month_salary <= 0:
                    break

            running_salary += month_salary
            entries.append((
                salary_when,
                f"Gehaltszahlung {employer} {month_label}",
                TRANSFER, CREDIT, month_salary,
            ))

    elif total_credits_needed < 0:
        # Account needs to drain — trim debits to the exact amount needed
        drain_needed = (account.balance - target).quantize(TWO_PLACES)
        if drain_needed <= 0:
            entries = []
        else:
            debit_entries = sorted(
                [(w, desc, ch, d, amt) for w, desc, ch, d, amt in entries if d == DEBIT],
                key=lambda x: x[0],
            )
            kept, running = [], Decimal("0.00")
            for w, desc, ch, d, amt in debit_entries:
                if running >= drain_needed:
                    break
                remainder = drain_needed - running
                if amt > remainder:
                    amt = remainder.quantize(TWO_PLACES)
                if amt > 0:
                    kept.append((w, desc, ch, d, amt))
                    running += amt
            entries = kept

    # Sort chronologically; salary (day 1–2) always arrives before expenses (day 3+)
    entries.sort(key=lambda x: x[0])

    posted = 0
    for when, desc, channel, direction, amount in entries:
        if amount <= 0:
            continue
        _post(
            account, direction, channel, amount,
            reference=generate_reference("HST"),
            description=desc,
            initiated_by=initiated_by,
            enforce_minimum=False,
            when=when,
        )
        posted += 1

    return posted
