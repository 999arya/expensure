from django.contrib import admin

from .models import ImportBatch, ImportRow


class ImportRowInline(admin.TabularInline):
    model = ImportRow
    extra = 0
    fields = (
        "line_number",
        "occurred_at",
        "description",
        "amount_paise",
        "direction",
        "status",
        "note",
    )
    readonly_fields = fields
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(ImportBatch)
class ImportBatchAdmin(admin.ModelAdmin):
    list_display = (
        "account",
        "status",
        "row_count",
        "imported_count",
        "duplicate_count",
        "invalid_count",
        "created_at",
    )
    list_filter = ("status", "bank")
    search_fields = ("account__nickname", "account__account_suffix", "owner__username")
    date_hierarchy = "created_at"
    inlines = [ImportRowInline]
    readonly_fields = (
        "owner",
        "account",
        "bank",
        "original_filename",
        "file_sha256",
        "period_start",
        "period_end",
        "row_count",
        "imported_count",
        "duplicate_count",
        "invalid_count",
        "created_at",
        "completed_at",
        "undone_at",
        "warning_text",
    )

    def has_add_permission(self, request):
        return False
