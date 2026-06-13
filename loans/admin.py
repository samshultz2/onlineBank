from django.contrib import admin

from .models import Loan, LoanProduct, LoanRepayment

admin.site.register(LoanProduct)


@admin.register(Loan)
class LoanAdmin(admin.ModelAdmin):
    list_display = ("user", "product", "amount", "status", "applied_at")
    list_filter = ("status",)


admin.site.register(LoanRepayment)
