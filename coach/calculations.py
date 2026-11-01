"""Deterministic financial calculations for the AI coach and affordability checks.

Rule: Always calculate consequences in Python first before asking any language model.
"""

from dataclasses import dataclass
from datetime import date
from django.utils import timezone
from django.db.models import Max
from ledger.models import Transaction

from accounts.services import account_cards
from planning.services import allowance_summary


@dataclass(frozen=True)
class AffordabilityEvaluation:
    requested_amount_paise: int
    can_afford_now: bool
    can_afford_forecast: bool
    cash_backed_paise: int
    forecast_allowance_paise: int
    new_forecast_paise: int
    new_daily_paise: int
    shortfall_paise: int
    verdict: str
    reason: str
    assumptions: str
    coverage_warning: str


def evaluate_affordability(user, amount_paise: int, today: date | None = None) -> AffordabilityEvaluation:
    """Calculate the exact mathematical consequences of spending `amount_paise`."""
    today = today or timezone.localdate()
    allowance = allowance_summary(user, today=today)

    cash_backed = allowance.cash_backed_paise
    forecast = allowance.allowance_paise
    days_left = allowance.days_left

    can_now = cash_backed >= amount_paise
    can_forecast = forecast >= amount_paise
    new_forecast = forecast - amount_paise
    shortfall = max(0, -new_forecast) if new_forecast < 0 else 0

    new_daily = max(0, new_forecast) // days_left if new_forecast > 0 else 0

    coverage_warning = ""
    if allowance.is_partial_period:
        coverage_warning = "Opening balance for this month is not yet confirmed. Calculations use recorded history only."

    # Format Rupee strings for explanation
    req_rs = amount_paise / 100
    cash_rs = cash_backed / 100
    fore_rs = forecast / 100
    daily_rs = new_daily / 100

    if can_now:
        verdict = f"Yes, you can afford ₹{req_rs:,.2f} right now."
        reason = (
            f"You have ₹{cash_rs:,.2f} in cash-backed allowance (after holding ₹{allowance.bills_paise/100:,.2f} "
            f"for upcoming bills and ₹{allowance.protected_paise/100:,.2f} for your protected savings target). "
            f"Spending this leaves about ₹{daily_rs:,.2f}/day for the remaining {days_left} days."
        )
    elif can_forecast:
        verdict = f"Affordable only after your remaining expected income arrives."
        reason = (
            f"Your cash on hand today only supports ₹{cash_rs:,.2f} after holding for bills and protected reserves. "
            f"However, including your ₹{allowance.income_paise/100:,.2f} expected income, your total forecast allowance "
            f"is ₹{fore_rs:,.2f}. Spending ₹{req_rs:,.2f} now before income arrives may risk dipping into bill reserves."
        )
    else:
        verdict = f"Not recommended: spending ₹{req_rs:,.2f} creates a shortfall."
        reason = (
            f"Your current forecast allowance is ₹{fore_rs:,.2f}. Spending ₹{req_rs:,.2f} would exceed your "
            f"discretionary budget by ₹{shortfall/100:,.2f}, eating into protected savings or unpaid bill reserves."
        )

    assumptions = (
        "This is a combined estimate, not a guarantee that the paying account stays above its floor. "
        f"Assumes remaining bills of ₹{allowance.bills_paise/100:,.2f} are paid on time, "
        f"and protected month-end reserves of ₹{allowance.protected_paise/100:,.2f} remain untouched."
    )

    return AffordabilityEvaluation(
        requested_amount_paise=amount_paise,
        can_afford_now=can_now,
        can_afford_forecast=can_forecast,
        cash_backed_paise=cash_backed,
        forecast_allowance_paise=forecast,
        new_forecast_paise=new_forecast,
        new_daily_paise=new_daily,
        shortfall_paise=shortfall,
        verdict=verdict,
        reason=reason,
        assumptions=assumptions,
        coverage_warning=coverage_warning,
    )


def build_compact_summary(user, today: date | None = None) -> dict:
    """Prepare an anonymized, minimal numerical summary for the AI assistant."""
    today = today or timezone.localdate()
    allowance = allowance_summary(user, today=today)

    cards = account_cards(user)
    confirmed_timestamps = [
        card.confirmed_at.strftime("%Y-%m-%d %H:%M")
        for card in cards
        if card.confirmed_at
    ]

    return {
        "as_of_date": today.strftime("%Y-%m-%d"),
        "days_remaining_in_month": allowance.days_left,
        "is_partial_period": allowance.is_partial_period,
        "last_confirmed_checkpoint": max(confirmed_timestamps) if confirmed_timestamps else None,
        "oldest_account_confirmation": min(confirmed_timestamps) if confirmed_timestamps else None,
        "unconfirmed_accounts": sum(card.confirmed_at is None for card in cards),
        "pending_review": Transaction.objects.filter(account__owner=user, status="needs_review").count(),
        "input_revision": str(Transaction.objects.filter(account__owner=user).aggregate(last=Max("updated_at"))["last"]),
        "calculation_version": "2",
        "financial_totals_inr": {
            "estimated_combined_balance": round(allowance.balance_paise / 100, 2),
            "expected_remaining_income": round(allowance.income_paise / 100, 2),
            "unpaid_bills": round(allowance.bills_paise / 100, 2),
            "protected_month_end_savings": round(allowance.protected_paise / 100, 2),
            "discretionary_allowance": round(allowance.allowance_paise / 100, 2),
            "cash_backed_allowance_now": round(allowance.cash_backed_paise / 100, 2),
            "daily_allowance": round(allowance.per_day_paise / 100, 2),
        },
    }
