"""Read models for the dashboard.

A balance is an estimate: the last balance you confirmed, plus everything
recorded after it. The rule lives in one place so the dashboard, reports and
later features cannot drift apart.
"""

from dataclasses import dataclass
from datetime import datetime

from django.db.models import BigIntegerField, Case, F, Sum, Value, When

from ledger.models import Transaction

from .models import BankAccount


@dataclass(frozen=True)
class AccountCard:
    id: int
    nickname: str
    bank: str
    suffix: str
    balance_paise: int
    confirmed_at: datetime | None
    captured_at: datetime | None


def estimate_balance(account, checkpoint):
    """Confirmed balance plus posted movements strictly after it.

    Transactions with `occurred_at` equal to or before the checkpoint are
    already inside the confirmed figure and must not be counted twice.
    """
    if checkpoint is None:
        return 0

    net = (
        account.transactions.filter(
            occurred_at__gt=checkpoint.effective_at,
            status=Transaction.Status.POSTED,
        )
        .aggregate(
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

    return checkpoint.amount_paise + net


def latest_checkpoint(account):
    """The checkpoint the estimate is anchored to, or None."""
    return account.checkpoints.order_by("-effective_at", "-id").first()


def account_cards(user):
    """One card per active account, newest information included."""
    accounts = (
        BankAccount.objects.filter(owner=user, is_active=True)
        .prefetch_related("checkpoints")
        .order_by("bank", "nickname")
    )

    cards = []
    for account in accounts:
        # Model ordering is -effective_at, -id, so the first is the latest.
        checkpoint = next(iter(account.checkpoints.all()), None)
        latest = account.transactions.order_by("-occurred_at", "-id").first()

        cards.append(
            AccountCard(
                id=account.pk,
                nickname=account.nickname,
                bank=account.get_bank_display(),
                suffix=account.account_suffix,
                balance_paise=estimate_balance(account, checkpoint),
                confirmed_at=checkpoint.effective_at if checkpoint else None,
                captured_at=latest.occurred_at if latest else None,
            )
        )

    return cards
