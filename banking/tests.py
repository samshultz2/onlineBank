from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from accounts.models import CustomerProfile, User
from banking import services
from banking.models import AccountType, BankAccount, Transaction
from banking.services import TransactionError
from loans import services as loan_services
from loans.models import Loan, LoanProduct


def make_customer(email, balance=Decimal("0.00"), account_type=None):
    user = User.objects.create_user(
        email=email, password="Str0ngPass!23",
        first_name="Test", last_name="Customer",
    )
    profile = CustomerProfile.objects.create(user=user)
    profile.set_pin("1234")
    account_type = account_type or AccountType.objects.first()
    account = BankAccount.objects.create(user=user, account_type=account_type)
    if balance:
        BankAccount.objects.filter(pk=account.pk).update(balance=balance)
        account.refresh_from_db()
    return user, account


class BaseBankTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.savings = AccountType.objects.create(
            name="Savings", code="SAV",
            interest_rate=Decimal("4.50"),
            minimum_balance=Decimal("0.00"),
            daily_transfer_limit=Decimal("1000000.00"),
        )


class TransferTests(BaseBankTest):
    def setUp(self):
        self.alice, self.alice_acct = make_customer("alice@test.com", Decimal("10000.00"))
        self.bob, self.bob_acct = make_customer("bob@test.com")

    def test_successful_transfer_moves_money_and_writes_ledger(self):
        services.transfer(
            self.alice_acct, self.bob_acct.account_number, Decimal("2500.00"),
            narration="Rent", initiated_by=self.alice,
        )
        self.alice_acct.refresh_from_db()
        self.bob_acct.refresh_from_db()
        self.assertEqual(self.alice_acct.balance, Decimal("7500.00"))
        self.assertEqual(self.bob_acct.balance, Decimal("2500.00"))

        debit = self.alice_acct.transactions.get()
        credit = self.bob_acct.transactions.get()
        self.assertEqual(debit.reference, credit.reference)
        self.assertEqual(debit.direction, Transaction.Direction.DEBIT)
        self.assertEqual(credit.direction, Transaction.Direction.CREDIT)
        self.assertEqual(debit.balance_after, Decimal("7500.00"))
        self.assertEqual(credit.balance_after, Decimal("2500.00"))

    def test_insufficient_funds_rejected_and_nothing_posted(self):
        with self.assertRaises(TransactionError):
            services.transfer(
                self.alice_acct, self.bob_acct.account_number, Decimal("10000.01"),
            )
        self.alice_acct.refresh_from_db()
        self.assertEqual(self.alice_acct.balance, Decimal("10000.00"))
        self.assertEqual(Transaction.objects.count(), 0)

    def test_transfer_to_self_rejected(self):
        with self.assertRaises(TransactionError):
            services.transfer(
                self.alice_acct, self.alice_acct.account_number, Decimal("100.00"),
            )

    def test_transfer_to_unknown_account_rejected(self):
        with self.assertRaises(TransactionError):
            services.transfer(self.alice_acct, "0000000000", Decimal("100.00"))

    def test_transfer_to_frozen_account_rejected(self):
        self.bob_acct.status = BankAccount.Status.FROZEN
        self.bob_acct.save()
        with self.assertRaises(TransactionError):
            services.transfer(
                self.alice_acct, self.bob_acct.account_number, Decimal("100.00"),
            )

    def test_daily_transfer_limit_enforced(self):
        limited = AccountType.objects.create(
            name="Limited", code="LIM", daily_transfer_limit=Decimal("1000.00"),
        )
        carol, carol_acct = make_customer(
            "carol@test.com", Decimal("50000.00"), account_type=limited
        )
        services.transfer(carol_acct, self.bob_acct.account_number, Decimal("800.00"))
        with self.assertRaises(TransactionError):
            services.transfer(carol_acct, self.bob_acct.account_number, Decimal("300.00"))

    def test_minimum_balance_protected(self):
        protected = AccountType.objects.create(
            name="MinBal", code="MIN", minimum_balance=Decimal("1000.00"),
            daily_transfer_limit=Decimal("1000000.00"),
        )
        dave, dave_acct = make_customer(
            "dave@test.com", Decimal("1500.00"), account_type=protected
        )
        with self.assertRaises(TransactionError):
            services.transfer(dave_acct, self.bob_acct.account_number, Decimal("600.00"))
        services.transfer(dave_acct, self.bob_acct.account_number, Decimal("500.00"))


class PostingTests(BaseBankTest):
    def setUp(self):
        self.user, self.account = make_customer("erin@test.com", Decimal("5000.00"))
        self.teller = User.objects.create_user(
            email="teller@bank.com", password="Str0ngPass!23",
            role=User.Role.TELLER, is_staff=True,
        )

    def test_deposit_and_withdrawal(self):
        services.deposit(self.account, Decimal("1000.00"), initiated_by=self.teller)
        self.account.refresh_from_db()
        self.assertEqual(self.account.balance, Decimal("6000.00"))

        services.withdraw(self.account, Decimal("500.00"), initiated_by=self.teller)
        self.account.refresh_from_db()
        self.assertEqual(self.account.balance, Decimal("5500.00"))

    def test_reversal_restores_balance_and_marks_original(self):
        entry = services.withdraw(self.account, Decimal("2000.00"),
                                  initiated_by=self.teller)
        services.reverse_transaction(entry, initiated_by=self.teller, reason="Error")
        self.account.refresh_from_db()
        self.assertEqual(self.account.balance, Decimal("5000.00"))
        entry.refresh_from_db()
        self.assertEqual(entry.status, Transaction.Status.REVERSED)
        self.assertIsNotNone(entry.reversed_by)

    def test_reversed_transaction_cannot_be_reversed_twice(self):
        entry = services.deposit(self.account, Decimal("100.00"),
                                 initiated_by=self.teller)
        services.reverse_transaction(entry, initiated_by=self.teller)
        with self.assertRaises(TransactionError):
            services.reverse_transaction(entry, initiated_by=self.teller)


class FixedDepositTests(BaseBankTest):
    def setUp(self):
        self.user, self.account = make_customer("fd@test.com", Decimal("100000.00"))

    def test_open_and_break_early_returns_principal_only(self):
        fd = services.open_fixed_deposit(
            self.account, Decimal("50000.00"), 90, Decimal("9.00"),
        )
        self.account.refresh_from_db()
        self.assertEqual(self.account.balance, Decimal("50000.00"))
        self.assertGreater(fd.expected_payout, fd.principal)

        services.close_fixed_deposit(fd, force_break=True)
        self.account.refresh_from_db()
        self.assertEqual(self.account.balance, Decimal("100000.00"))
        fd.refresh_from_db()
        self.assertEqual(fd.status, fd.Status.BROKEN)
        self.assertEqual(fd.payout_amount, Decimal("50000.00"))


class LoanTests(BaseBankTest):
    def setUp(self):
        self.user, self.account = make_customer("loan@test.com")
        self.manager = User.objects.create_user(
            email="manager@bank.com", password="Str0ngPass!23",
            role=User.Role.MANAGER, is_staff=True,
        )
        self.product = LoanProduct.objects.create(
            name="Personal", interest_rate=Decimal("18.00"),
            min_amount=Decimal("10000.00"), max_amount=Decimal("1000000.00"),
            min_tenor_months=1, max_tenor_months=24,
        )
        self.loan = Loan.objects.create(
            user=self.user, product=self.product,
            disbursement_account=self.account,
            amount=Decimal("100000.00"), tenor_months=12,
        )

    def test_disbursement_credits_account_and_activates_loan(self):
        loan_services.disburse_loan(self.loan, initiated_by=self.manager)
        self.account.refresh_from_db()
        self.loan.refresh_from_db()
        self.assertEqual(self.account.balance, Decimal("100000.00"))
        self.assertEqual(self.loan.status, Loan.Status.ACTIVE)
        # 100,000 * (1 + 0.18 * 12/12) = 118,000
        self.assertEqual(self.loan.total_payable, Decimal("118000.00"))

    def test_repayment_reduces_outstanding_and_caps_at_balance_owed(self):
        loan_services.disburse_loan(self.loan, initiated_by=self.manager)
        services.deposit(self.account, Decimal("50000.00"))
        loan_services.repay_loan(
            self.loan, self.account, Decimal("118000.00"), initiated_by=self.user,
        )
        self.loan.refresh_from_db()
        self.account.refresh_from_db()
        self.assertEqual(self.loan.status, Loan.Status.PAID)
        self.assertEqual(self.loan.outstanding_balance, Decimal("0.00"))
        self.assertEqual(self.account.balance, Decimal("32000.00"))

    def test_overpayment_is_capped(self):
        loan_services.disburse_loan(self.loan, initiated_by=self.manager)
        services.deposit(self.account, Decimal("200000.00"))
        loan_services.repay_loan(
            self.loan, self.account, Decimal("999999.00"), initiated_by=self.user,
        )
        self.account.refresh_from_db()
        # Only 118,000 (total payable) should have been collected.
        self.assertEqual(self.account.balance, Decimal("182000.00"))


class ViewSecurityTests(BaseBankTest):
    def setUp(self):
        self.user, self.account = make_customer("sec@test.com", Decimal("1000.00"))
        self.other, self.other_acct = make_customer("other@test.com")

    def test_anonymous_redirected_from_dashboard(self):
        response = self.client.get(reverse("banking:dashboard"))
        self.assertEqual(response.status_code, 302)

    def test_customer_cannot_view_someone_elses_account(self):
        self.client.login(username="sec@test.com", password="Str0ngPass!23")
        response = self.client.get(
            reverse("banking:account_detail", args=[self.other_acct.account_number])
        )
        self.assertEqual(response.status_code, 404)

    def test_customer_blocked_from_staff_portal(self):
        self.client.login(username="sec@test.com", password="Str0ngPass!23")
        response = self.client.get(reverse("staffportal:dashboard"))
        self.assertRedirects(response, reverse("banking:dashboard"))

    def test_teller_cannot_post_adjustment(self):
        User.objects.create_user(
            email="teller2@bank.com", password="Str0ngPass!23",
            role=User.Role.TELLER, is_staff=True,
        )
        self.client.login(username="teller2@bank.com", password="Str0ngPass!23")
        response = self.client.post(
            reverse("staffportal:account_adjust", args=[self.account.account_number]),
            {"direction": "CREDIT", "amount": "100.00", "description": "sneaky"},
        )
        self.assertRedirects(response, reverse("staffportal:dashboard"))
        self.account.refresh_from_db()
        self.assertEqual(self.account.balance, Decimal("1000.00"))

    def test_transfer_requires_correct_pin(self):
        self.client.login(username="sec@test.com", password="Str0ngPass!23")
        response = self.client.post(reverse("banking:transfer"), {
            "source_account": self.account.pk,
            "destination_account": self.other_acct.account_number,
            "amount": "100.00",
            "narration": "",
            "pin": "9999",
        })
        self.assertEqual(response.status_code, 200)  # re-renders with error
        self.account.refresh_from_db()
        self.assertEqual(self.account.balance, Decimal("1000.00"))

    def test_transfer_with_correct_pin_succeeds(self):
        self.client.login(username="sec@test.com", password="Str0ngPass!23")
        response = self.client.post(reverse("banking:transfer"), {
            "source_account": self.account.pk,
            "destination_account": self.other_acct.account_number,
            "amount": "100.00",
            "narration": "test",
            "pin": "1234",
        })
        self.assertEqual(response.status_code, 302)
        self.account.refresh_from_db()
        self.assertEqual(self.account.balance, Decimal("900.00"))


class PinLockoutTests(BaseBankTest):
    def test_pin_locks_after_three_failures(self):
        user, _ = make_customer("pin@test.com")
        profile = user.profile
        for _ in range(3):
            self.assertFalse(profile.verify_pin("0000"))
        # Correct PIN no longer works while locked.
        self.assertTrue(profile.pin_is_locked)
        self.assertFalse(profile.verify_pin("1234"))
