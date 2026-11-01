"""Rules shared by manual entry, transfers and later capture.

The review-queue rule lives here so manual entry (Phase 3) and SMS capture
(Phase 6) flag movements identically. Transfers also live here, because a
paired movement must be created atomically: half a transfer is worse than none.
"""

from django.db import transaction as db_transaction
from django.db.models import Count

from accounts.services import latest_checkpoint
from ledger.models import Category, Transaction, Transfer


def status_for_transaction(account, occurred_at):
    """Return POSTED or NEEDS_REVIEW for a new movement on `account`.

    A movement with `occurred_at` equal to or earlier than the account's
    latest checkpoint is ambiguous and waits for review. With no checkpoint,
    there is nothing to conflict with, so the movement is posted at once.
    """
    checkpoint = latest_checkpoint(account)
    if checkpoint is None:
        return Transaction.Status.POSTED

    if occurred_at <= checkpoint.effective_at:
        return Transaction.Status.NEEDS_REVIEW

    return Transaction.Status.POSTED


def reviewable(user):
    """Movements owned by `user` that are waiting for a review decision."""
    return (
        Transaction.objects.filter(
            account__owner=user, status=Transaction.Status.NEEDS_REVIEW
        )
        .select_related("account", "category")
        .prefetch_related("account__checkpoints")
        .order_by("-occurred_at", "-id")
    )


def conflict_checkpoint(txn):
    """The checkpoint `txn` falls inside, or None."""
    return latest_checkpoint(txn.account)


def apply_review_decision(txn, decision):
    """Apply a review decision and return the new status.

    ``approve`` makes the row posted (already inside the confirmed figure).
    ``ignore`` marks it ignored (excluded from estimates).
    Anything else raises ValueError.
    """
    if decision == "approve":
        return Transaction.Status.POSTED
    if decision == "ignore":
        return Transaction.Status.IGNORED
    raise ValueError(f"Unknown review decision: {decision!r}")


def review_count(user):
    return Transaction.objects.filter(
        account__owner=user, status=Transaction.Status.NEEDS_REVIEW
    ).count()


def create_transfer(owner, from_account, to_account, amount_paise, occurred_at, description=""):
    """Create a transfer and its two legs in one atomic step.

    Both accounts must belong to `owner` and must differ. The debit leg leaves
    `from_account`; the credit leg arrives in `to_account`. Each leg gets the
    review status its own account's checkpoint implies, because the two banks
    can know about the movement at different times.
    """
    if from_account.owner_id != owner.pk or to_account.owner_id != owner.pk:
        raise ValueError("Both accounts must belong to you.")
    if from_account.pk == to_account.pk:
        raise ValueError("Choose two different accounts.")
    if amount_paise <= 0:
        raise ValueError("A transfer must be more than zero.")

    with db_transaction.atomic():
        transfer = Transfer.objects.create(
            owner=owner,
            from_account=from_account,
            to_account=to_account,
            amount_paise=amount_paise,
            occurred_at=occurred_at,
            description=description,
        )
        note = description or "Transfer"
        Transaction.objects.create(
            account=from_account,
            occurred_at=occurred_at,
            amount_paise=amount_paise,
            direction=Transaction.Direction.DEBIT,
            description=f"{note} → {to_account.nickname}",
            status=status_for_transaction(from_account, occurred_at),
            transfer=transfer,
        )
        Transaction.objects.create(
            account=to_account,
            occurred_at=occurred_at,
            amount_paise=amount_paise,
            direction=Transaction.Direction.CREDIT,
            description=f"{note} ← {from_account.nickname}",
            status=status_for_transaction(to_account, occurred_at),
            transfer=transfer,
        )

    return transfer


def transfer_list(user):
    """The user's transfers, newest first, with both legs loaded."""
    return (
        Transfer.objects.filter(owner=user)
        .select_related("from_account", "to_account")
        .prefetch_related("legs")
        .annotate(leg_count=Count("legs"))
        .order_by("-occurred_at", "-id")
    )


def unresolved_transfers(user):
    """Transfers that lost a leg, and transfer-labelled credits with no pair.

    Both are excluded from income. A missing leg is far more likely a capture
    gap than a gift, so the app asks rather than counting it.
    """
    broken = (
        Transfer.objects.filter(owner=user)
        .annotate(leg_count=Count("legs"))
        .filter(leg_count__lt=2)
        .select_related("from_account", "to_account")
    )
    loose = (
        Transaction.objects.filter(
            account__owner=user,
            transfer__isnull=True,
            category__kind=Category.Kind.TRANSFER,
            direction=Transaction.Direction.CREDIT,
            status=Transaction.Status.POSTED,
        )
        .select_related("account", "category")
        .order_by("-occurred_at", "-id")
    )
    return broken, loose


def unresolved_transfer_count(user):
    broken, loose = unresolved_transfers(user)
    return broken.count() + loose.count()

