from datetime import date

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import RecurringPaymentRule
from .services import calculate_monthly_report, export_transactions_csv, get_preceding_month


@login_required
def report_view(request):
    today = timezone.localdate()
    try:
        year = int(request.GET.get("year", today.year))
        month = int(request.GET.get("month", today.month))
        if not (1 <= month <= 12 and 2000 <= year <= 2100):
            raise ValueError
    except (ValueError, TypeError):
        year, month = today.year, today.month

    report = calculate_monthly_report(request.user, year, month)

    prec_y, prec_m = get_preceding_month(year, month)
    next_y, next_m = (year + 1, 1) if month == 12 else (year, month + 1)

    context = {
        "report": report,
        "selected_date": date(year, month, 1),
        "prev_year": prec_y,
        "prev_month": prec_m,
        "next_year": next_y,
        "next_month": next_m,
        "is_current_month": (year == today.year and month == today.month),
    }
    return render(request, "reports/report.html", context)


@login_required
def report_api_data(request):
    """JSON API endpoint providing data for Chart.js interactive rendering."""
    today = timezone.localdate()
    try:
        year = int(request.GET.get("year", today.year))
        month = int(request.GET.get("month", today.month))
    except (ValueError, TypeError):
        year, month = today.year, today.month

    report = calculate_monthly_report(request.user, year, month)

    # Palette suitable for dark & light mode
    colors = [
        "#146356", "#2f855a", "#319795", "#3182ce", "#805ad5",
        "#d69e2e", "#dd6b20", "#e53e3e", "#718096", "#4a5568"
    ]

    cat_labels = [c.name for c in report.categories]
    cat_values = [round(c.net_paise / 100, 2) for c in report.categories]

    data = {
        "year": report.year,
        "month": report.month,
        "month_label": report.first_day.strftime("%B %Y"),
        "income": round(report.income_paise / 100, 2),
        "expenses": round(report.expenses_paise / 100, 2),
        "savings": round(report.net_savings_paise / 100, 2),
        "categories": {
            "labels": cat_labels,
            "values": cat_values,
            "colors": colors[:len(cat_labels)],
        },
        "comparison": {
            "has_preceding": report.has_preceding,
            "labels": [f"{report.preceding_year}-{report.preceding_month:02d}", f"{report.year}-{report.month:02d}"],
            "income": [round(report.preceding_income_paise / 100, 2), round(report.income_paise / 100, 2)],
            "expenses": [round(report.preceding_expenses_paise / 100, 2), round(report.expenses_paise / 100, 2)],
            "savings": [round(report.preceding_savings_paise / 100, 2), round(report.net_savings_paise / 100, 2)],
        },
    }
    return JsonResponse(data)


@login_required
def export_csv(request):
    today = timezone.localdate()
    try:
        year = int(request.GET.get("year", today.year))
        month = int(request.GET.get("month", today.month))
    except (ValueError, TypeError):
        year, month = today.year, today.month

    csv_content = export_transactions_csv(request.user, year, month)
    filename = f"transactions-{year}-{month:02d}.csv"
    response = HttpResponse(csv_content, content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@require_POST
@login_required
def recurring_confirm(request):
    merchant = request.POST.get("merchant", "").strip()
    if not merchant:
        messages.error(request, "Invalid merchant.")
        return redirect("reports:overview")

    rule, _ = RecurringPaymentRule.objects.get_or_create(
        owner=request.user,
        merchant_name=merchant,
    )
    rule.status = RecurringPaymentRule.Status.CONFIRMED
    rule.save(update_fields=["status", "updated_at"])
    messages.success(request, f"Marked '{merchant}' as a confirmed recurring payment.")
    return redirect("reports:overview")


@require_POST
@login_required
def recurring_dismiss(request):
    merchant = request.POST.get("merchant", "").strip()
    if not merchant:
        messages.error(request, "Invalid merchant.")
        return redirect("reports:overview")

    rule, _ = RecurringPaymentRule.objects.get_or_create(
        owner=request.user,
        merchant_name=merchant,
    )
    rule.status = RecurringPaymentRule.Status.DISMISSED
    rule.save(update_fields=["status", "updated_at"])
    messages.info(request, f"Dismissed recurring suggestion for '{merchant}'.")
    return redirect("reports:overview")
