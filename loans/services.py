from django.db import transaction as db_transaction
from django.utils import timezone

from banking.models import Transaction, generate_reference
from banking.services import TransactionError, _locked, _post
from notifications.services import notify

from .models import Loan, LoanRepayment


@db_transaction.atomic
def disburse_loan(loan, *, initiated_by):
    """Approve + credit the loan amount to the customer's account."""
    loan = Loan.objects.select_for_update().get(pk=loan.pk)
    if loan.status not in (Loan.Status.PENDING, Loan.Status.APPROVED):
        raise TransactionError("This loan cannot be disbursed.")

    account = _locked(loan.disbursement_account)
    _post(
        account, Transaction.Direction.CREDIT, Transaction.Channel.LOAN_DISBURSEMENT,
        loan.amount, reference=generate_reference("LND"),
        description=f"{loan.product.name} disbursement",
        initiated_by=initiated_by,
    )
    loan.status = Loan.Status.ACTIVE
    loan.total_payable = loan.compute_total_payable()
    loan.decided_by = initiated_by
    loan.decided_at = loan.decided_at or timezone.now()
    loan.disbursed_at = timezone.now()
    loan.save(update_fields=[
        "status", "total_payable", "decided_by", "decided_at", "disbursed_at",
    ])
    notify(
        loan.user, "Loan approved and disbursed",
        f"Your {loan.product.name} of €{loan.amount:,.2f} has been credited to "
        f"{account.account_number}. Total payable: €{loan.total_payable:,.2f} "
        f"over {loan.tenor_months} months.",
        level="success",
    )
    return loan


@db_transaction.atomic
def repay_loan(loan, account, amount, *, initiated_by):
    """Debit the customer's account and apply the amount to the loan."""
    loan = Loan.objects.select_for_update().get(pk=loan.pk)
    if loan.status != Loan.Status.ACTIVE:
        raise TransactionError("Only active loans can be repaid.")
    outstanding = loan.outstanding_balance
    if amount > outstanding:
        amount = outstanding  # never collect more than what is owed

    account = _locked(account)
    if account.status != account.Status.ACTIVE:
        raise TransactionError("The selected account is not active.")
    reference = generate_reference("LNR")
    _post(
        account, Transaction.Direction.DEBIT, Transaction.Channel.LOAN_REPAYMENT,
        amount, reference=reference,
        description=f"{loan.product.name} repayment",
        initiated_by=initiated_by,
    )
    LoanRepayment.objects.create(
        loan=loan, amount=amount, reference=reference, recorded_by=initiated_by
    )
    loan.amount_repaid += amount
    if loan.amount_repaid >= loan.total_payable:
        loan.status = Loan.Status.PAID
    loan.save(update_fields=["amount_repaid", "status"])

    remaining = loan.outstanding_balance
    if loan.status == Loan.Status.PAID:
        notify(loan.user, "Loan fully repaid",
               f"Congratulations! Your {loan.product.name} is now fully repaid.",
               level="success")
    else:
        notify(loan.user, "Loan repayment received",
               f"€{amount:,.2f} applied to your {loan.product.name}. "
               f"Outstanding balance: €{remaining:,.2f}.",
               level="success")
    return loan
