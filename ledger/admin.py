from django.contrib import admin

from .models import Category, Transaction


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "kind", "owner", "is_default")
    list_filter = ("kind", "is_default")
    search_fields = ("name", "owner__username")


@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = (
        "description",
        "account",
        "direction",
        "amount_paise",
        "occurred_at",
        "status",
        "category",
    )
    list_filter = ("direction", "status", "source", "category")
    search_fields = ("description", "reference", "account__nickname")
    date_hierarchy = "occurred_at"
    raw_id_fields = ("account", "category")
