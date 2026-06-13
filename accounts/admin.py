# The bank is operated from the custom staff portal at /staff/.
# Django admin registrations are kept as a minimal maintenance fallback.
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import AuditLog, CustomerProfile, User


@admin.register(User)
class BankUserAdmin(UserAdmin):
    ordering = ("email",)
    list_display = ("email", "first_name", "last_name", "role", "is_active")
    list_filter = ("role", "is_active")
    search_fields = ("email", "first_name", "last_name")
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Personal info", {"fields": ("first_name", "last_name", "phone", "role")}),
        ("Permissions", {"fields": ("is_active", "is_staff", "is_superuser")}),
    )
    add_fieldsets = (
        (None, {"fields": ("email", "password1", "password2", "role")}),
    )


@admin.register(CustomerProfile)
class CustomerProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "kyc_status", "city", "state")
    list_filter = ("kyc_status",)
    search_fields = ("user__email",)


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("action", "actor", "ip_address", "created_at")
    search_fields = ("action", "description")
    readonly_fields = ("actor", "action", "description", "ip_address", "created_at")
