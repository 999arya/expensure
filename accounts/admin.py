from django.contrib import admin

from .models import BalanceCheckpoint, BankAccount


class BalanceCheckpointInline(admin.TabularInline):
    model = BalanceCheckpoint
    extra = 0
    fields = ("amount_paise", "effective_at", "source", "note")


@admin.register(BankAccount)
class BankAccountAdmin(admin.ModelAdmin):
    list_display = (
        "nickname",
        "bank",
        "account_type",
        "account_suffix",
        "owner",
        "is_active",
    )
    list_filter = ("bank", "account_type", "is_active")
    search_fields = ("nickname", "account_suffix", "owner__username")
    inlines = [BalanceCheckpointInline]


@admin.register(BalanceCheckpoint)
class BalanceCheckpointAdmin(admin.ModelAdmin):
    list_display = ("account", "amount_paise", "effective_at", "source", "recorded_at")
    list_filter = ("source",)
    search_fields = ("account__nickname", "account__account_suffix")
    date_hierarchy = "effective_at"
