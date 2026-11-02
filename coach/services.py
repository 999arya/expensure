"""Versioned insight cache and explicit daily AI cost budget."""
import hashlib
import json
import os
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from .ai import query_ai_coach
from .calculations import build_compact_summary, evaluate_affordability
from .models import AIUsage, FinancialInsight


def summary_version(summary, **extra):
    return hashlib.sha256(json.dumps({"summary": summary, **extra}, sort_keys=True).encode()).hexdigest()


def reserve_ai_call(user):
    with transaction.atomic():
        get_user_model().objects.select_for_update().get(pk=user.pk)
        usage, _ = AIUsage.objects.get_or_create(owner=user, day=timezone.localdate())
        if usage.calls >= settings.AI_DAILY_LIMIT:
            return False
        usage.calls += 1
        usage.save(update_fields=["calls"])
        return True


def affordability_insight(user, amount, consent=False):
    evaluation = evaluate_affordability(user, amount)
    summary = build_compact_summary(user)
    ai_available = bool(os.environ.get("GROQ_API_KEY", "").strip())
    version = summary_version(
        summary,
        amount=amount,
        ai=bool(consent and ai_available),
        groq_model=settings.GROQ_MODEL,
    )
    existing = FinancialInsight.objects.filter(owner=user, kind="affordability", summary_hash=version).first()
    if existing:
        return evaluation, existing
    use_ai = consent and ai_available and reserve_ai_call(user)
    is_ai, response = query_ai_coach(summary, "Explain this calculated purchase assessment.", evaluation, allow_ai=use_ai)
    insight, _ = FinancialInsight.objects.get_or_create(owner=user, kind="affordability", summary_hash=version,
        defaults={"title": response["title"], "body": response["body"], "reason": evaluation.reason,
            "amount_paise": amount, "time_period": str(timezone.localdate()),
            "assumptions": evaluation.assumptions, "source": "ai" if is_ai else "deterministic"})
    return evaluation, insight


def generate_daily_insight(user):
    summary = build_compact_summary(user)
    version = summary_version(summary, kind="daily")
    amounts = summary["financial_totals_inr"]
    if summary["pending_review"]:
        title, body = "Review pending movements", "Some movements still need your confirmation. They are excluded from your estimates."
    elif summary["is_partial_period"] or summary["unconfirmed_accounts"]:
        title, body = "Confirm your balance coverage", "Your month-opening balance is incomplete. Confirm balances before relying on the spending forecast."
    elif amounts["cash_backed_allowance_now"] < 0:
        title, body = "Your plan has a cash shortfall", "Cash-backed allowance is negative. Review bills and protected balances before discretionary spending."
    else:
        title, body = "Review your monthly plan", "Check expected income and unpaid bills against your bank records. Forecast income is not cash available today."
    return FinancialInsight.objects.get_or_create(owner=user, kind="daily", summary_hash=version,
        defaults={"title": title, "body": body, "reason": "Triggered by recorded coverage, review status and allowance totals.",
            "time_period": summary["as_of_date"], "assumptions": "Recorded movements may be incomplete; suggestions do not change your goals."})[0]


def goal_steps(goal, today=None):
    """Calculate a pace independently of any model, using integer paise."""
    from core.formatting import format_inr
    from planning.services import allowance_summary
    today = today or timezone.localdate()
    if goal.is_complete:
        return ["You have reached this goal. Confirm the saved amount and choose your next goal when ready."]
    steps = []
    remaining = goal.remaining_paise
    if goal.target_date and goal.target_date > today:
        days = (goal.target_date - today).days
        daily = (remaining + days - 1) // days
        steps.append(f"To cover the remaining {format_inr(remaining)} in {days} days, aim to set aside {format_inr(daily)} per day. This is a required pace, not a confirmed affordable amount.")
    elif goal.target_date:
        steps.append(f"Your target date is today or has passed, with {format_inr(remaining)} still to save. Choose a new date or revise the target.")
    else:
        steps.append(f"Choose a target date for the remaining {format_inr(remaining)} so your savings pace can be calculated.")
    allowance = allowance_summary(goal.owner, today=today)
    steps.append("Record expected income and upcoming bills, and confirm your bank balances before deciding how much to save.")
    if allowance.cash_backed_paise <= 0:
        steps.append("Your recorded cash-backed allowance has no spare room. Review spending, bills, and the goal deadline before setting more money aside.")
    else:
        steps.append("Review your spending allowance and each account's minimum balance before choosing a contribution. Other goals may need the same money.")
    steps.append("Set a monthly savings target in your plan to protect money from everyday spending. A long-term goal alone does not reserve your balance.")
    steps.append("Review progress weekly and update this goal only after you actually set money aside. Goal updates do not move money.")
    return steps


def goal_guidance(goal, consent=False):
    from .ai import prioritize_goal_steps
    steps = goal_steps(goal)
    available = bool(os.environ.get("GROQ_API_KEY", "").strip())
    version = summary_version(steps, goal_id=goal.pk, ai=bool(consent and available), model=settings.GROQ_MODEL)
    existing = FinancialInsight.objects.filter(owner=goal.owner, kind="goal", summary_hash=version).first()
    if existing:
        return existing
    use_ai = consent and available and reserve_ai_call(goal.owner)
    is_ai, ordered = prioritize_goal_steps(steps, allow_ai=use_ai)
    return FinancialInsight.objects.get_or_create(
        owner=goal.owner, kind="goal", summary_hash=version,
        defaults={"title": "Your goal action plan", "body": "\n".join(ordered),
                  "reason": "Based on your goal, deadline and recorded allowance.",
                  "assumptions": "Recorded finances may be incomplete. The savings pace does not establish affordability.",
                  "source": "ai" if is_ai else "deterministic"},
    )[0]
