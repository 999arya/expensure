from django.conf import settings
from django.db import models
from django.db.models import Q


class Category(models.Model):
    """A user-owned spending or income category.

    Categories are per user so two people can use the same name without sharing
    anything.
    """

    class Kind(models.TextChoices):
        INCOME = "income", "Income"
        EXPENSE = "expense", "Expense"
        TRANSFER = "transfer", "Transfer"
        REFUND = "refund", "Refund"
        OTHER = "other", "Other"

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="categories",
    )
    name = models.CharField(max_length=48)
    kind = models.CharField(max_length=8, choices=Kind.choices, default=Kind.EXPENSE)
    is_default = models.BooleanField(default=False)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "name"],
                name="unique_category_per_owner",
            )
        ]

    def __str__(self):
        return self.name


class Transaction(models.Model):
    """A single movement of money on one account.

    Amount is always positive; direction says which way it went. The category
    must belong to the same owner as the account — enforced in forms and
    services, not by the database.
    """

    class Direction(models.TextChoices):
        CREDIT = "credit", "Credit"
        DEBIT = "debit", "Debit"

    class Status(models.TextChoices):
        POSTED = "posted", "Posted"
        NEEDS_REVIEW = "needs_review", "Needs review"
        IGNORED = "ignored", "Ignored"

    class Source(models.TextChoices):
        MANUAL = "manual", "Entered by you"
        SMS = "sms", "Bank SMS"
        IMPORT = "import", "Imported"

    account = models.ForeignKey(
        "accounts.BankAccount",
        on_delete=models.CASCADE,
        related_name="transactions",
    )
    occurred_at = models.DateTimeField(help_text="When it happened at the bank.")
    received_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the app learned about it, if different.",
    )
    amount_paise = models.BigIntegerField()
    direction = models.CharField(max_length=6, choices=Direction.choices)
    category = models.ForeignKey(
        Category,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="transactions",
    )
    description = models.CharField(max_length=200)
    reference = models.CharField(max_length=64, blank=True)
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.POSTED,
    )
    source = models.CharField(
        max_length=8,
        choices=Source.choices,
        default=Source.MANUAL,
    )
    dedup_key = models.CharField(
        max_length=128,
        blank=True,
        help_text="Stable bank reference when one exists. Empty means unknown.",
    )
    transfer = models.ForeignKey(
        "ledger.Transfer",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="legs",
        help_text="Set when this movement is one leg of a transfer.",
    )
    import_batch = models.ForeignKey(
        "ingestion.ImportBatch",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="transactions",
        help_text="Set when this movement came from a CSV statement import.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-occurred_at", "-id"]
        indexes = [models.Index(fields=["account", "-occurred_at"])]
        constraints = [
            models.CheckConstraint(
                condition=Q(amount_paise__gt=0),
                name="txn_amount_positive",
            ),
            models.UniqueConstraint(
                fields=["account", "dedup_key"],
                condition=~Q(dedup_key=""),
                name="unique_txn_dedup_per_account",
            ),
        ]

    def __str__(self):
        return f"{self.description} · {self.amount_paise} paise"

    @property
    def conflict_checkpoint(self):
        """The checkpoint this movement falls inside, or None.

        Uses the prefetched checkpoints when available so a review page with
        many rows stays at one query.
        """
        checkpoints = list(self.account.checkpoints.all())
        if not checkpoints:
            return None
        # Meta ordering is -effective_at, -id, so the first is the latest.
        return checkpoints[0]


class Transfer(models.Model):
    """Money moved between two accounts the same person owns.

    A transfer owns two transaction legs: a debit on `from_account` and a
    credit on `to_account`. Keeping the pair together is what lets reports
    leave it out of income and spending. If a leg is ever deleted, the pair is
    unresolved and the remaining leg is never treated as income.
    """

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="transfers",
    )
    from_account = models.ForeignKey(
        "accounts.BankAccount",
        on_delete=models.CASCADE,
        related_name="outgoing_transfers",
    )
    to_account = models.ForeignKey(
        "accounts.BankAccount",
        on_delete=models.CASCADE,
        related_name="incoming_transfers",
    )
    amount_paise = models.BigIntegerField()
    occurred_at = models.DateTimeField()
    description = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-occurred_at", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=Q(amount_paise__gt=0),
                name="transfer_amount_positive",
            )
        ]

    def __str__(self):
        return (
            f"{self.from_account.nickname} → {self.to_account.nickname} "
            f"· {self.amount_paise} paise"
        )

    @property
    def is_resolved(self):
        """True when both legs still exist."""
        return self.legs.count() >= 2

