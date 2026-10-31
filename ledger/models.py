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
