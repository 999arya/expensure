from django.contrib import admin

from .models import Bill, Goal, IncomePlan


@admin.register(Goal)
class GoalAdmin(admin.ModelAdmin):
    list_display = ("name", "owner", "target_paise", "saved_paise", "target_date", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name", "owner__username")


@admin.register(IncomePlan)
class IncomePlanAdmin(admin.ModelAdmin):
    list_display = ("description", "owner", "amount_paise", "expected_on", "status")
    list_filter = ("status",)
    search_fields = ("description", "owner__username")
    date_hierarchy = "expected_on"


@admin.register(Bill)
class BillAdmin(admin.ModelAdmin):
    list_display = ("description", "owner", "amount_paise", "due_on", "status")
    list_filter = ("status",)
    search_fields = ("description", "owner__username")
    date_hierarchy = "due_on"
