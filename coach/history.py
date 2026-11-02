"""History-based savings estimates. Money is always calculated in integer paise."""
from datetime import date, datetime, time

from django.db.models import Sum
from django.utils import timezone

from accounts.models import BankAccount
from ledger.models import Category, Transaction
from planning.models import MonthlyGoal
from planning.services import allowance_summary


def savings_history(user, today=None):
    """Use three finished calendar months; never extrapolate a partial month."""
    today = today or timezone.localdate()
    month = today.replace(day=1)
    rows = []
    end = month
    accounts = list(BankAccount.objects.filter(owner=user).prefetch_related("checkpoints"))
    for _ in range(3):
        start = date(end.year - 1, 12, 1) if end.month == 1 else date(end.year, end.month - 1, 1)
        start_at = timezone.make_aware(datetime.combine(start, time.min))
        end_at = timezone.make_aware(datetime.combine(end, time.min))
        txns = Transaction.objects.filter(account__owner=user, occurred_at__gte=start_at, occurred_at__lt=end_at)
        external = txns.filter(status=Transaction.Status.POSTED, transfer__isnull=True).exclude(category__kind=Category.Kind.TRANSFER)
        income = external.filter(direction="credit", category__kind=Category.Kind.INCOME).aggregate(total=Sum("amount_paise"))["total"] or 0
        debits = external.filter(direction="debit").aggregate(total=Sum("amount_paise"))["total"] or 0
        refunds = external.filter(direction="credit", category__kind__in=[Category.Kind.EXPENSE, Category.Kind.REFUND]).aggregate(total=Sum("amount_paise"))["total"] or 0
        expenses = max(0, debits - refunds)
        reasons = []
        if not accounts or any(not any(cp.effective_at <= start_at for cp in a.checkpoints.all()) for a in accounts):
            reasons.append("Missing opening balance coverage")
        if txns.filter(status=Transaction.Status.NEEDS_REVIEW).exists():
            reasons.append("Transactions still need review")
        if external.filter(direction="credit").exclude(category__kind__in=[Category.Kind.INCOME, Category.Kind.EXPENSE, Category.Kind.REFUND]).exists():
            reasons.append("Unclassified credits")
        if not income or not debits:
            reasons.append("Record income and expenses for this month")
        rows.append({"month": start.isoformat(), "income": income, "expenses": expenses, "surplus": income - expenses, "issues": reasons})
        end = start
    rows.reverse()
    current = MonthlyGoal.objects.filter(owner=user, month=month).first()
    allowance = allowance_summary(user, today=today)
    issues = []
    if any(row["issues"] for row in rows):
        issues.append("Three finished months with opening balances, income, expenses and resolved classifications are needed.")
    if allowance.is_partial_period:
        issues.append("Confirm an opening balance for the current month before changing its savings target.")
    if Transaction.objects.filter(account__owner=user, status=Transaction.Status.NEEDS_REVIEW, occurred_at__lt=timezone.make_aware(datetime.combine(today, time.max))).exists():
        issues.append("Resolve pending transaction reviews first.")
    # Do not raise targets based on one exceptional month or a deteriorating latest month.
    median = sorted(row["surplus"] for row in rows)[1]
    baseline = max(0, min(median, rows[-1]["surplus"]))
    # Current forecast is an upper bound, not a promise that income will arrive.
    capacity = max(0, allowance.balance_paise + allowance.income_paise - allowance.bills_paise - (allowance.b0_paise or 0))
    if allowance.balance_paise + allowance.income_paise - allowance.bills_paise < max(allowance.floors_paise, allowance.account_targets_paise):
        capacity = 0
    options = {"cautious": min(baseline * 60 // 100, capacity), "balanced": min(baseline * 80 // 100, capacity)}
    return {
        "month": month.isoformat(), "as_of": today.isoformat(), "history": rows,
        "current_target": current.savings_paise if current else 0,
        "target_revision": current.updated_at.isoformat() if current else None,
        "baseline": baseline, "forecast_capacity": capacity, "options": options,
        "issues": issues,
    }


def recommend_target(user, consent=False):
    import os
    from .ai import choose_history_target
    from .models import TargetRecommendation
    from .services import reserve_ai_call, summary_version

    snapshot = savings_history(user)
    if snapshot["issues"]:
        return None
    version = summary_version(snapshot)
    existing = TargetRecommendation.objects.filter(owner=user, snapshot_hash=version, applied_at__isnull=True)
    if consent:
        existing = existing.filter(source="ai")
    cached = existing.first()
    if cached:
        return cached
    use_ai = consent and bool(os.environ.get("GROQ_API_KEY", "").strip()) and reserve_ai_call(user)
    # Only aggregate amounts and periods leave the server, never account identifiers.
    summary = {key: snapshot[key] for key in ["history", "current_target", "options", "forecast_capacity"]}
    is_ai, choice = choose_history_target(summary, allow_ai=use_ai)
    return TargetRecommendation.objects.create(
        owner=user, month=snapshot["month"], previous_paise=snapshot["current_target"],
        proposed_paise=snapshot["options"][choice], snapshot=snapshot, snapshot_hash=version,
        source="ai" if is_ai else "deterministic", choice=choice,
    )


def apply_target(user, pk):
    from django.contrib.auth import get_user_model
    from django.db import transaction
    from django.shortcuts import get_object_or_404
    from .models import TargetRecommendation
    from .services import summary_version

    with transaction.atomic():
        get_user_model().objects.select_for_update().get(pk=user.pk)
        proposal = get_object_or_404(TargetRecommendation.objects.select_for_update(), pk=pk, owner=user)
        if proposal.applied_at:
            return "already_applied"
        current = MonthlyGoal.objects.select_for_update().filter(owner=user, month=proposal.month).first()
        snapshot = savings_history(user)
        if snapshot["issues"] or summary_version(snapshot) != proposal.snapshot_hash:
            return "stale"
        if current:
            # Compare-and-set prevents a concurrent manual edit being overwritten.
            changed = MonthlyGoal.objects.filter(pk=current.pk, updated_at=current.updated_at).update(savings_paise=proposal.proposed_paise, updated_at=timezone.now())
            if not changed:
                return "stale"
        else:
            current, created = MonthlyGoal.objects.get_or_create(owner=user, month=proposal.month, defaults={"savings_paise": proposal.proposed_paise})
            if not created:
                return "stale"
        proposal.applied_at = timezone.now()
        proposal.save(update_fields=["applied_at"])
        return "applied"
