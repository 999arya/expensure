"""Monthly reports, aggregations, recurring payment detection, and exports.

Financial rules:
- Monthly net savings = classified external income − net expenses.
- Refunds reduce expenses in the month received, shown as a separate refunds line.
- Transfers, opening balances, balance adjustments, and unclassified credits are excluded.
- Partial months (e.g. mid-month signup or current unfinished month) are explicitly labeled.
- Charts match transaction totals exactly in integer paise.
"""

import calendar
import csv
import io
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time

from django.db.models import BigIntegerField, Case, F, Sum, Value, When
from django.utils import timezone

from accounts.models import BankAccount
from accounts.services import estimate_balance
from ledger.models import Category, Transaction
from planning.models import MonthlyGoal

from .models import RecurringPaymentRule


@dataclass(frozen=True)
class CategorySpending:
    category_id: int | None
    name: str
    debits_paise: int
    refunds_paise: int
    net_paise: int
    percent: int


@dataclass(frozen=True)
class AccountCashFlow:
    account_id: int
    nickname: str
    bank: str
    suffix: str
    opening_balance_paise: int | None
    credits_paise: int
    debits_paise: int
    net_flow_paise: int
    closing_balance_paise: int


@dataclass(frozen=True)
class RecurringCandidate:
    merchant_name: str
    count: int
    typical_amount_paise: int
    category_name: str | None


@dataclass(frozen=True)
class MonthlyReport:
    year: int
    month: int
    first_day: date
    last_day: date
    is_partial: bool
    coverage_note: str

    income_paise: int
    expenses_paise: int
    refunds_paise: int
    net_savings_paise: int

    savings_goal_paise: int
    savings_vs_goal_paise: int

    categories: list[CategorySpending]
    accounts_cash_flow: list[AccountCashFlow]

    # Preceding month comparison
    has_preceding: bool
    preceding_year: int
    preceding_month: int
    preceding_income_paise: int
    preceding_expenses_paise: int
    preceding_savings_paise: int
    income_diff_paise: int
    expenses_diff_paise: int
    savings_diff_paise: int

    recurring_candidates: list[RecurringCandidate]


def get_month_range(year: int, month: int):
    """Start and end timestamps for the given year and month in local time."""
    first_day = date(year, month, 1)
    last_day_num = calendar.monthrange(year, month)[1]
    last_day = date(year, month, last_day_num)
    start_dt = timezone.make_aware(datetime.combine(first_day, time.min))
    end_dt = timezone.make_aware(datetime.combine(last_day, time.max))
    return first_day, last_day, start_dt, end_dt


def get_preceding_month(year: int, month: int):
    if month == 1:
        return year - 1, 12
    return year, month - 1


def calculate_monthly_report(user, year: int, month: int) -> MonthlyReport:
    first_day, last_day, start_dt, end_dt = get_month_range(year, month)
    today = timezone.localdate()

    # Determine coverage / partial month
    # If the user joined mid-month or first transaction happened mid-month, or current month has not ended
    is_partial = False
    coverage_note = "Full monthly coverage"

    user_joined_date = timezone.localdate(user.date_joined)
    if user_joined_date > first_day and user_joined_date <= last_day:
        is_partial = True
        coverage_note = f"Partial month: account created on {user_joined_date:%d %b %Y}."
    elif today <= last_day and (today.year, today.month) == (year, month):
        is_partial = True
        coverage_note = f"In-progress month up to {today:%d %b %Y}."

    # Posted transactions in this period
    txns = Transaction.objects.filter(
        account__owner=user,
        occurred_at__gte=start_dt,
        occurred_at__lte=end_dt,
        status=Transaction.Status.POSTED,
    ).select_related("category", "account", "transfer")

    # Exclude transfers: transfer__isnull=False or category__kind=TRANSFER
    external_txns = txns.filter(
        transfer__isnull=True,
    ).exclude(category__kind=Category.Kind.TRANSFER)

    # 1. Income: external posted credits with an INCOME category
    income_paise = (
        external_txns.filter(
            direction=Transaction.Direction.CREDIT,
            category__kind=Category.Kind.INCOME,
        ).aggregate(total=Sum("amount_paise"))["total"]
        or 0
    )

    # 2. Expenses and Refunds by Category:
    # Debits in expense categories or uncategorized
    # Credits in expense categories count as refunds (Decision 003)
    cat_debits = defaultdict(int)
    cat_refunds = defaultdict(int)
    cat_names = {}

    for t in external_txns:
        cid = t.category_id
        cname = t.category.name if t.category else "Uncategorized"
        cat_names[cid] = cname

        if t.direction == Transaction.Direction.DEBIT:
            cat_debits[cid] += t.amount_paise
        elif t.direction == Transaction.Direction.CREDIT:
            if t.category and t.category.kind in (Category.Kind.EXPENSE, Category.Kind.REFUND):
                cat_refunds[cid] += t.amount_paise

    total_debits = sum(cat_debits.values())
    total_refunds = sum(cat_refunds.values())
    net_expenses_paise = max(0, total_debits - total_refunds)

    category_list: list[CategorySpending] = []
    all_cat_keys = set(cat_debits.keys()) | set(cat_refunds.keys())
    for cid in all_cat_keys:
        d = cat_debits[cid]
        r = cat_refunds[cid]
        net = max(0, d - r)
        pct = (net * 100 // net_expenses_paise) if net_expenses_paise > 0 else 0
        category_list.append(
            CategorySpending(
                category_id=cid,
                name=cat_names.get(cid, "Uncategorized"),
                debits_paise=d,
                refunds_paise=r,
                net_paise=net,
                percent=pct,
            )
        )
    category_list.sort(key=lambda c: c.net_paise, reverse=True)

    # Net savings
    net_savings_paise = income_paise - net_expenses_paise

    # Goal comparison
    m_goal = MonthlyGoal.objects.filter(owner=user, month=first_day).first()
    savings_goal_paise = m_goal.savings_paise if m_goal else 0
    savings_vs_goal_paise = net_savings_paise - savings_goal_paise

    # 3. Bank-wise cash flow
    accounts_cash_flow: list[AccountCashFlow] = []
    user_accounts = BankAccount.objects.filter(owner=user, is_active=True).prefetch_related(
        "checkpoints", "transactions"
    )

    for acc in user_accounts:
        # Checkpoint before start_dt to find opening balance
        cp_before = (
            acc.checkpoints.filter(effective_at__lte=start_dt)
            .order_by("-effective_at", "-id")
            .first()
        )
        opening_paise = None
        if cp_before:
            net_before = (
                acc.transactions.filter(
                    occurred_at__gt=cp_before.effective_at,
                    occurred_at__lt=start_dt,
                    status=Transaction.Status.POSTED,
                ).aggregate(
                    total=Sum(
                        Case(
                            When(direction=Transaction.Direction.CREDIT, then=F("amount_paise")),
                            When(direction=Transaction.Direction.DEBIT, then=-F("amount_paise")),
                            default=Value(0),
                            output_field=BigIntegerField(),
                        )
                    )
                )["total"]
                or 0
            )
            opening_paise = cp_before.amount_paise + net_before

        # Movements in month for this account (including internal transfers)
        acc_in_month = acc.transactions.filter(
            occurred_at__gte=start_dt,
            occurred_at__lte=end_dt,
            status=Transaction.Status.POSTED,
        )
        acc_credits = (
            acc_in_month.filter(direction=Transaction.Direction.CREDIT).aggregate(
                total=Sum("amount_paise")
            )["total"]
            or 0
        )
        acc_debits = (
            acc_in_month.filter(direction=Transaction.Direction.DEBIT).aggregate(
                total=Sum("amount_paise")
            )["total"]
            or 0
        )
        acc_net = acc_credits - acc_debits

        # Latest checkpoint to compute estimated closing balance
        latest_cp = acc.checkpoints.order_by("-effective_at", "-id").first()
        closing_paise = estimate_balance(acc, latest_cp) if latest_cp else 0

        accounts_cash_flow.append(
            AccountCashFlow(
                account_id=acc.pk,
                nickname=acc.nickname,
                bank=acc.get_bank_display(),
                suffix=acc.account_suffix,
                opening_balance_paise=opening_paise,
                credits_paise=acc_credits,
                debits_paise=acc_debits,
                net_flow_paise=acc_net,
                closing_balance_paise=closing_paise,
            )
        )

    # 4. Preceding month comparison
    prec_year, prec_month = get_preceding_month(year, month)
    prec_first, prec_last, prec_start, prec_end = get_month_range(prec_year, prec_month)

    prec_txns = Transaction.objects.filter(
        account__owner=user,
        occurred_at__gte=prec_start,
        occurred_at__lte=prec_end,
        status=Transaction.Status.POSTED,
        transfer__isnull=True,
    ).exclude(category__kind=Category.Kind.TRANSFER)

    has_preceding = prec_txns.exists()
    prec_income = (
        prec_txns.filter(
            direction=Transaction.Direction.CREDIT,
            category__kind=Category.Kind.INCOME,
        ).aggregate(total=Sum("amount_paise"))["total"]
        or 0
    )
    prec_debits = (
        prec_txns.filter(direction=Transaction.Direction.DEBIT).aggregate(
            total=Sum("amount_paise")
        )["total"]
        or 0
    )
    prec_refunds = (
        prec_txns.filter(
            direction=Transaction.Direction.CREDIT,
            category__kind__in=[Category.Kind.EXPENSE, Category.Kind.REFUND],
        ).aggregate(total=Sum("amount_paise"))["total"]
        or 0
    )
    prec_expenses = max(0, prec_debits - prec_refunds)
    prec_savings = prec_income - prec_expenses

    # 5. Recurring payment candidates
    recurring_candidates = detect_recurring_candidates(user)

    return MonthlyReport(
        year=year,
        month=month,
        first_day=first_day,
        last_day=last_day,
        is_partial=is_partial,
        coverage_note=coverage_note,
        income_paise=income_paise,
        expenses_paise=net_expenses_paise,
        refunds_paise=total_refunds,
        net_savings_paise=net_savings_paise,
        savings_goal_paise=savings_goal_paise,
        savings_vs_goal_paise=savings_vs_goal_paise,
        categories=category_list,
        accounts_cash_flow=accounts_cash_flow,
        has_preceding=has_preceding,
        preceding_year=prec_year,
        preceding_month=prec_month,
        preceding_income_paise=prec_income,
        preceding_expenses_paise=prec_expenses,
        preceding_savings_paise=prec_savings,
        income_diff_paise=income_paise - prec_income,
        expenses_diff_paise=net_expenses_paise - prec_expenses,
        savings_diff_paise=net_savings_paise - prec_savings,
        recurring_candidates=recurring_candidates,
    )


def detect_recurring_candidates(user) -> list[RecurringCandidate]:
    """Find repeated debit transactions that look like recurring bills or subscriptions."""
    # Exclude already processed rules
    handled = set(
        RecurringPaymentRule.objects.filter(owner=user).values_list("merchant_name", flat=True)
    )

    debits = (
        Transaction.objects.filter(
            account__owner=user,
            direction=Transaction.Direction.DEBIT,
            status=Transaction.Status.POSTED,
            transfer__isnull=True,
        )
        .exclude(description="")
        .select_related("category")
    )

    merchant_counts: dict[str, list[Transaction]] = defaultdict(list)
    for t in debits:
        norm = t.description.strip().title()
        if norm and norm not in handled:
            merchant_counts[norm].append(t)

    candidates = []
    for merchant, txns in merchant_counts.items():
        if len(txns) >= 2:
            # Average or most recent amount
            avg_amount = sum(t.amount_paise for t in txns) // len(txns)
            cat = txns[-1].category.name if txns[-1].category else None
            candidates.append(
                RecurringCandidate(
                    merchant_name=merchant,
                    count=len(txns),
                    typical_amount_paise=avg_amount,
                    category_name=cat,
                )
            )

    candidates.sort(key=lambda c: c.count, reverse=True)
    return candidates[:5]


def export_transactions_csv(user, year: int, month: int) -> str:
    """Generate RFC 4180 compliant CSV of transactions for the requested month."""
    _, _, start_dt, end_dt = get_month_range(year, month)
    txns = (
        Transaction.objects.filter(
            account__owner=user,
            occurred_at__gte=start_dt,
            occurred_at__lte=end_dt,
        )
        .select_related("account", "category")
        .order_by("occurred_at", "id")
    )

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "Date",
            "Time (IST)",
            "Account",
            "Bank",
            "Direction",
            "Amount (INR)",
            "Category",
            "Description",
            "Reference",
            "Status",
        ]
    )

    for t in txns:
        local_dt = timezone.localtime(t.occurred_at)
        amount_inr = f"{t.amount_paise / 100:.2f}"
        cat_name = t.category.name if t.category else "Uncategorized"
        writer.writerow(
            [
                local_dt.strftime("%Y-%m-%d"),
                local_dt.strftime("%H:%M:%S"),
                f"{t.account.nickname} (····{t.account.account_suffix})",
                t.account.get_bank_display(),
                t.get_direction_display(),
                amount_inr,
                cat_name,
                t.description,
                t.reference or "",
                t.get_status_display(),
            ]
        )

    return output.getvalue()
