"""Fictional Phase 1 data.

Everything in this file is invented. It exists so the dashboard can be designed
before the database schema is settled, and it gets deleted in Phase 2 when real
models replace it.
"""

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


def _at(day: int, hour: int, minute: int) -> datetime:
    """A time in October 2026, Indian Standard Time."""
    return datetime(2026, 10, day, hour, minute, tzinfo=IST)


@dataclass(frozen=True)
class Account:
    nickname: str
    bank: str
    suffix: str
    balance_paise: int
    confirmed_at: datetime
    captured_at: datetime


@dataclass(frozen=True)
class Transaction:
    occurred_at: datetime
    description: str
    category: str
    amount_paise: int
    direction: str  # "credit" or "debit"
    status: str  # "posted" or "needs_review"


@dataclass(frozen=True)
class SavingsGoal:
    name: str
    saved_paise: int
    target_paise: int

    @property
    def percent(self) -> int:
        if self.target_paise <= 0:
            return 0
        return round(self.saved_paise * 100 / self.target_paise)


ACCOUNTS = [
    Account(
        nickname="Kotak Savings",
        bank="Kotak Mahindra Bank",
        suffix="4821",
        balance_paise=2_450_000,
        confirmed_at=_at(31, 9, 14),
        captured_at=_at(31, 11, 2),
    ),
    Account(
        nickname="Bank of Baroda Savings",
        bank="Bank of Baroda",
        suffix="9034",
        balance_paise=6_125_000,
        confirmed_at=_at(30, 8, 40),
        captured_at=_at(31, 10, 15),
    ),
    Account(
        nickname="Bank of Baroda Current",
        bank="Bank of Baroda",
        suffix="1176",
        balance_paise=842_000,
        confirmed_at=_at(28, 17, 5),
        captured_at=_at(29, 12, 30),
    ),
]

TRANSACTIONS = [
    Transaction(
        occurred_at=_at(31, 11, 2),
        description="Swiggy",
        category="Food",
        amount_paise=48_600,
        direction="debit",
        status="posted",
    ),
    Transaction(
        occurred_at=_at(31, 9, 14),
        description="Salary — Acme Studios",
        category="Income",
        amount_paise=8_500_000,
        direction="credit",
        status="posted",
    ),
    Transaction(
        occurred_at=_at(30, 20, 31),
        description="UPI to Ravi Kumar",
        category="Transfer",
        amount_paise=200_000,
        direction="debit",
        status="needs_review",
    ),
    Transaction(
        occurred_at=_at(30, 18, 5),
        description="Electricity — BESCOM",
        category="Bills",
        amount_paise=124_000,
        direction="debit",
        status="posted",
    ),
    Transaction(
        occurred_at=_at(29, 15, 45),
        description="Refund — Amazon",
        category="Shopping",
        amount_paise=149_900,
        direction="credit",
        status="posted",
    ),
    Transaction(
        occurred_at=_at(28, 19, 20),
        description="ATM withdrawal",
        category="Cash",
        amount_paise=500_000,
        direction="debit",
        status="posted",
    ),
]

GOAL = SavingsGoal(
    name="Emergency fund",
    saved_paise=850_000,
    target_paise=1_500_000,
)


def review_count() -> int:
    return sum(1 for txn in TRANSACTIONS if txn.status == "needs_review")
