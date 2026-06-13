from django.contrib import admin

from .models import (
    AccountType,
    BankAccount,
    Beneficiary,
    Biller,
    BillPayment,
    FixedDeposit,
    Transaction,
)

admin.site.register(AccountType)
admin.site.register(Biller)


@admin.register(BankAccount)
class BankAccountAdmin(admin.ModelAdmin):
    list_display = ("account_number", "user", "account_type", "balance", "status")
    list_filter = ("status", "account_type")
    search_fields = ("account_number", "user__email")


@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = ("reference", "account", "direction", "amount", "status", "created_at")
    list_filter = ("direction", "channel", "status")
    search_fields = ("reference", "account__account_number")


admin.site.register(Beneficiary)
admin.site.register(FixedDeposit)
admin.site.register(BillPayment)
