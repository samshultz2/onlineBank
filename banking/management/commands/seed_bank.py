"""
Seed the bank with the baseline configuration it needs to operate:
account types, loan products, billers and a default administrator.

Usage:
    python manage.py seed_bank
"""
from decimal import Decimal

from django.core.management.base import BaseCommand

from accounts.models import User
from banking.models import AccountType, Biller
from loans.models import LoanProduct

ACCOUNT_TYPES = [
    {
        "name": "Current Account", "code": "CUR",
        "description": "Everyday current account with a contactless debit card and SEPA payments.",
        "interest_rate": Decimal("0.00"), "minimum_balance": Decimal("0.00"),
        "monthly_fee": Decimal("0.00"), "daily_transfer_limit": Decimal("15000.00"),
    },
    {
        "name": "Savings Account", "code": "SAV",
        "description": "Instant-access savings account paying credit interest on your balance.",
        "interest_rate": Decimal("2.50"), "minimum_balance": Decimal("0.00"),
        "monthly_fee": Decimal("0.00"), "daily_transfer_limit": Decimal("10000.00"),
    },
    {
        "name": "Premier Account", "code": "PRM",
        "description": "Premium current account with higher limits and a relationship manager.",
        "interest_rate": Decimal("1.00"), "minimum_balance": Decimal("5000.00"),
        "monthly_fee": Decimal("12.50"), "daily_transfer_limit": Decimal("100000.00"),
    },
    {
        "name": "Business Account", "code": "BIZ",
        "description": "Current account for registered businesses and sole traders.",
        "interest_rate": Decimal("0.00"), "minimum_balance": Decimal("0.00"),
        "monthly_fee": Decimal("9.90"), "daily_transfer_limit": Decimal("250000.00"),
    },
]

LOAN_PRODUCTS = [
    {
        "name": "Personal Loan",
        "description": "Unsecured personal loan with fixed monthly repayments.",
        "interest_rate": Decimal("7.90"),
        "min_amount": Decimal("1000.00"), "max_amount": Decimal("50000.00"),
        "min_tenor_months": 6, "max_tenor_months": 84,
    },
    {
        "name": "Overdraft Facility",
        "description": "Short-term borrowing to cover everyday cash-flow gaps.",
        "interest_rate": Decimal("11.90"),
        "min_amount": Decimal("250.00"), "max_amount": Decimal("10000.00"),
        "min_tenor_months": 1, "max_tenor_months": 12,
    },
    {
        "name": "Business Loan",
        "description": "Working-capital and investment finance for businesses.",
        "interest_rate": Decimal("6.50"),
        "min_amount": Decimal("10000.00"), "max_amount": Decimal("500000.00"),
        "min_tenor_months": 12, "max_tenor_months": 120,
    },
    {
        "name": "Home Improvement Loan",
        "description": "Finance renovations and home upgrades at a competitive fixed rate.",
        "interest_rate": Decimal("5.40"),
        "min_amount": Decimal("5000.00"), "max_amount": Decimal("75000.00"),
        "min_tenor_months": 12, "max_tenor_months": 120,
    },
]

BILLERS = [
    {"name": "E.ON Energy", "category": "ELECTRICITY", "customer_id_label": "Customer / meter number"},
    {"name": "Vattenfall", "category": "ELECTRICITY", "customer_id_label": "Customer / meter number"},
    {"name": "EnBW Gas", "category": "WATER", "customer_id_label": "Contract number"},
    {"name": "Sky Deutschland", "category": "TV", "customer_id_label": "Subscriber number"},
    {"name": "Deutsche Telekom", "category": "INTERNET", "customer_id_label": "Account number"},
    {"name": "Vodafone Mobile", "category": "AIRTIME", "customer_id_label": "Mobile number"},
    {"name": "O2 Mobile", "category": "AIRTIME", "customer_id_label": "Mobile number"},
    {"name": "Stadtwerke Water", "category": "WATER", "customer_id_label": "Contract number"},
]


class Command(BaseCommand):
    help = "Seed account types, loan products, billers and a default admin user."

    def handle(self, *args, **options):
        for data in ACCOUNT_TYPES:
            obj, created = AccountType.objects.update_or_create(
                code=data["code"], defaults=data
            )
            self.stdout.write(f"{'Created' if created else 'Updated'} account type: {obj}")

        for data in LOAN_PRODUCTS:
            obj, created = LoanProduct.objects.update_or_create(
                name=data["name"], defaults=data
            )
            self.stdout.write(f"{'Created' if created else 'Updated'} loan product: {obj}")

        for data in BILLERS:
            obj, created = Biller.objects.update_or_create(
                name=data["name"], defaults=data
            )
            self.stdout.write(f"{'Created' if created else 'Updated'} biller: {obj}")

        if not User.objects.filter(role=User.Role.ADMIN).exists():
            User.objects.create_user(
                email="admin@securetrustbank.com",
                password="ChangeMe123!",
                first_name="System",
                last_name="Administrator",
                role=User.Role.ADMIN,
                is_staff=True,
                is_superuser=True,
            )
            self.stdout.write(self.style.WARNING(
                "Created default admin: admin@securetrustbank.com / ChangeMe123! "
                "— change this password immediately."
            ))
        self.stdout.write(self.style.SUCCESS("Seeding complete."))
