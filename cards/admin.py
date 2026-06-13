from django.contrib import admin

from .models import Card


@admin.register(Card)
class CardAdmin(admin.ModelAdmin):
    list_display = ("masked_number", "user", "scheme", "status")
    list_filter = ("scheme", "status")
