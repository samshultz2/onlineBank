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
        "name": "Savings", "code": "SAV",
        "description": "Everyday savings account with interest on your balance.",
        "interest_rate": Decimal("4.50"), "minimum_balance": Decimal("0.00"),
        "monthly_fee": Decimal("0.00"), "daily_transfer_limit": Decimal("1000000.00"),
    },
    {
        "name": "Current", "code": "CUR",
        "description": "Cheque-enabled current account for frequent transactions.",
        "interest_rate": Decimal("0.00"), "minimum_balance": Decimal("1000.00"),
        "monthly_fee": Decimal("50.00"), "daily_transfer_limit": Decimal("5000000.00"),
    },
    {
        "name": "Premium", "code": "PRM",
        "description": "High-limit account for premium customers.",
        "interest_rate": Decimal("2.00"), "minimum_balance": Decimal("50000.00"),
        "monthly_fee": Decimal("500.00"), "daily_transfer_limit": Decimal("20000000.00"),
    },
]

LOAN_PRODUCTS = [
    {
        "name": "Personal Loan",
        "description": "Quick personal loan for everyday needs.",
        "interest_rate": Decimal("18.00"),
        "min_amount": Decimal("50000.00"), "max_amount": Decimal("2000000.00"),
        "min_tenor_months": 3, "max_tenor_months": 24,
    },
    {
        "name": "Salary Advance",
        "description": "Short-term advance against your next salary.",
        "interest_rate": Decimal("12.00"),
        "min_amount": Decimal("20000.00"), "max_amount": Decimal("500000.00"),
        "min_tenor_months": 1, "max_tenor_months": 6,
    },
    {
        "name": "Business Loan",
        "description": "Working capital for registered businesses.",
        "interest_rate": Decimal("22.00"),
        "min_amount": Decimal("500000.00"), "max_amount": Decimal("20000000.00"),
        "min_tenor_months": 6, "max_tenor_months": 48,
    },
]

BILLERS = [
    {"name": "Ikeja Electric", "category": "ELECTRICITY", "customer_id_label": "Meter number"},
    {"name": "Eko Electricity", "category": "ELECTRICITY", "customer_id_label": "Meter number"},
    {"name": "DSTV", "category": "TV", "customer_id_label": "Smartcard number"},
    {"name": "GOtv", "category": "TV", "customer_id_label": "IUC number"},
    {"name": "Spectranet", "category": "INTERNET", "customer_id_label": "Account ID"},
    {"name": "MTN Airtime", "category": "AIRTIME", "customer_id_label": "Phone number"},
    {"name": "Airtel Airtime", "category": "AIRTIME", "customer_id_label": "Phone number"},
    {"name": "Glo Airtime", "category": "AIRTIME", "customer_id_label": "Phone number"},
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
