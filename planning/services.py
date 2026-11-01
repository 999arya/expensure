"""Allowance, goal and settlement rules.

The allowance is an estimate for the rest of the month:

    allowance = estimated balances + still-expected income − unpaid bills

Everything is integer paise. A settled item records the matching transaction at
the same time, so `C` and either `R` or `U` move together and nothing is counted
twice.
"""

from dataclasses import dataclass
from datetime import date, datetime, time

from django.db import transaction as db_transaction
from django.db.models import BigIntegerField, Case, F, Sum, Value, When
from django.utils import timezone

from accounts.models import BankAccount
from accounts.services import account_cards
from ledger.models import Category, Transaction
from ledger.services import status_for_transaction

from .models import (
    AccountGoal,
    Bill,
    Goal,
    IncomePlan,
    MonthlyGoal,
    month_bounds,
    settled_datetime,
)


@dataclass(frozen=True)
class Allowance:
    balance_paise: int
    income_paise: int
    bills_paise: int
    allowance_paise: int
    per_day_paise: int
    days_left: int
    month_end: date
    # Section 4 financial rules fields:
    b0_paise: int | None = None
    savings_goal_paise: int = 0
    floors_paise: int = 0
    account_targets_paise: int = 0
    protected_paise: int = 0
    cash_backed_paise: int = 0
    is_partial_period: bool = False

    @property
    def is_negative(self):
        return self.allowance_paise < 0

    @property
    def shortfall_paise(self):
        return max(0, -self.allowance_paise)

    @property
    def has_plan(self):
        return bool(self.income_paise or self.bills_paise or self.savings_goal_paise)


def _sum_paise(queryset):
    return queryset.aggregate(total=Sum("amount_paise"))["total"] or 0


def calculate_opening_balance(accounts, month_start):
    """Combined month-opening balance B0 across active accounts, if known.

    If any active account has no checkpoint at or before month_start, B0 cannot
    be honestly determined without guessing. In that case, returns None.
    """
    if not accounts:
        return 0

    total_b0 = 0
    for account in accounts:
        cp = account.checkpoints.filter(effective_at__lte=month_start).order_by(
            "-effective_at", "-id"
        ).first()
        if cp is None:
            return None

        # Add posted movements between checkpoint and month_start
        net_between = (
            account.transactions.filter(
                occurred_at__gt=cp.effective_at,
                occurred_at__lte=month_start,
                status=Transaction.Status.POSTED,
            ).aggregate(
                total=Sum(
                    Case(
                        When(
                            direction=Transaction.Direction.CREDIT,
                            then=F("amount_paise"),
                        ),
                        When(
                            direction=Transaction.Direction.DEBIT,
                            then=-F("amount_paise"),
                        ),
                        default=Value(0),
                        output_field=BigIntegerField(),
                    )
                )
            )["total"]
            or 0
        )
        total_b0 += cp.amount_paise + net_between

    return total_b0


def allowance_summary(user, today=None):
    """Spending allowance according to Section 4 planning model:

    - C: current combined estimated balance
    - R: remaining confirmed income for this month
    - U: expected remaining unpaid bills
    - B0: combined month-opening balance (if known)
    - G: desired additional savings this month (MonthlyGoal)
    - F: sum of chosen minimum account balances (AccountGoal)
    - T: sum of account end-of-month targets
    - Protected month-end amount P = max(F, T, B0 + G) if B0 known else max(F, T)
    - Forecast discretionary allowance = C + R − U − P
    - Cash-backed allowance now = C − U − P
    """
    today = today or timezone.localdate()
    first_day, end = month_bounds(today)
    month_start = timezone.make_aware(datetime.combine(first_day, time(0, 0)))

    cards = account_cards(user)
    balance = sum(card.balance_paise for card in cards)

    # R — income that has not been settled, so it is not already in `balance`.
    income = _sum_paise(
        IncomePlan.objects.filter(
            owner=user,
            status=IncomePlan.Status.EXPECTED,
            expected_on__lte=end,
        )
    )

    # U — bills that have not been paid, so they are not already in `balance`.
    bills = _sum_paise(
        Bill.objects.filter(
            owner=user,
            status=Bill.Status.UNPAID,
            due_on__lte=end,
        )
    )

    # Active accounts
    active_accounts = list(
        BankAccount.objects.filter(owner=user, is_active=True).prefetch_related(
            "checkpoints", "transactions"
        )
    )

    # B0 — opening balance if known
    b0 = calculate_opening_balance(active_accounts, month_start)
    is_partial = b0 is None

    # G — monthly savings target
    m_goal = MonthlyGoal.objects.filter(owner=user, month=first_day).first()
    g = m_goal.savings_paise if m_goal else 0

    # F & T — account minimum floors and end-of-month targets
    account_goals = {
        ag.account_id: ag
        for ag in AccountGoal.objects.filter(account__in=active_accounts)
    }
    f = sum(
        account_goals[a.id].minimum_floor_paise
        for a in active_accounts
        if a.id in account_goals
    )
    t = sum(
        account_goals[a.id].effective_target_paise
        for a in active_accounts
        if a.id in account_goals
    )

    # Protected amount P = max(F, T, B0 + G)
    if b0 is not None:
        protected = max(f, t, b0 + g)
    else:
        protected = max(f, t)

    allowance = balance + income - bills - protected
    cash_backed = balance - bills - protected

    days_left = (end - today).days + 1
    if days_left < 1:
        days_left = 1
    per_day = allowance // days_left if allowance >= 0 else -((-allowance) // days_left)

    return Allowance(
        balance_paise=balance,
        income_paise=income,
        bills_paise=bills,
        allowance_paise=allowance,
        per_day_paise=per_day,
        days_left=days_left,
        month_end=end,
        b0_paise=b0,
        savings_goal_paise=g,
        floors_paise=f,
        account_targets_paise=t,
        protected_paise=protected,
        cash_backed_paise=cash_backed,
        is_partial_period=is_partial,
    )


def primary_goal(user):
    """The goal to feature on the dashboard, or None."""
    return (
        Goal.objects.filter(owner=user, is_active=True)
        .order_by(F("target_date").asc(nulls_last=True), "created_at")
        .first()
    )


def add_to_goal(goal, amount_paise):
    """Add a contribution without losing a concurrent one."""
    if amount_paise <= 0:
        raise ValueError("Add an amount greater than zero.")

    Goal.objects.filter(pk=goal.pk).update(
        saved_paise=F("saved_paise") + amount_paise
    )
    goal.refresh_from_db()
    return goal


def _category_for(user, kind, preferred_name):
    category = Category.objects.filter(owner=user, name=preferred_name).first()
    if category is not None:
        return category
    return Category.objects.filter(owner=user, kind=kind).order_by("id").first()


def _account_for(item, user):
    if item.account_id and item.account.owner_id == user.pk and item.account.is_active:
        return item.account
    return BankAccount.objects.filter(owner=user, is_active=True).first()


def settle_income(item, user, day=None):
    """Record the credit and mark the plan item received, atomically."""
    return _settle(
        item=item,
        user=user,
        day=day,
        direction=Transaction.Direction.CREDIT,
        category_kind=Category.Kind.INCOME,
        category_name="Other income",
        settled_status=IncomePlan.Status.RECEIVED,
    )


def settle_bill(item, user, day=None):
    """Record the debit and mark the bill paid, atomically."""
    return _settle(
        item=item,
        user=user,
        day=day,
        direction=Transaction.Direction.DEBIT,
        category_kind=Category.Kind.EXPENSE,
        category_name="Bills & utilities",
        settled_status=Bill.Status.PAID,
    )


def _settle(item, user, day, direction, category_kind, category_name, settled_status):
    if item.owner_id != user.pk:
        raise ValueError("This item belongs to someone else.")

    account = _account_for(item, user)
    if account is None:
        raise ValueError("Add an account before settling a plan item.")

    day = day or timezone.localdate()
    occurred_at = settled_datetime(day)

    with db_transaction.atomic():
        txn = Transaction.objects.create(
            account=account,
            occurred_at=occurred_at,
            amount_paise=item.amount_paise,
            direction=direction,
            category=_category_for(user, category_kind, category_name),
            description=item.description,
            status=status_for_transaction(account, occurred_at),
            source=Transaction.Source.MANUAL,
        )
        item.account = account
        item.settled_transaction = txn
        item.status = settled_status
        item.save(update_fields=["account", "settled_transaction", "status"])

    return txn
