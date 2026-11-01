"""Reviewing movements that fall before the last confirmed balance.

When a transaction is dated at or before the account's newest confirmed
checkpoint, the bank may already have included it in that figure. Rather than
guess, the app saves it as ``needs_review`` and waits for a decision:

- **"Already included"** → ``posted``. The estimate is unchanged: the time rule
  already treats the movement as inside the checkpoint.
- **"Ignore"** → ``ignored``. Excluded from the estimate and from reports,
  kept visible with a muted chip so it can be undone later.
- **"Not included"** → no server action. The honest fix is to confirm the
  current balance for that account, which re-anchors the estimate from now.

The status decision rule lives here so manual entry (Phase 3) and later SMS
capture (Phase 6) flag movements identically.
"""

from accounts.services import latest_checkpoint
from ledger.models import Transaction


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
