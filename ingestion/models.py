from django.conf import settings
from django.db import models


class ImportBatch(models.Model):
    """One CSV statement upload.

    The raw file is never stored. The batch keeps only counts, a hash and the
    account it belongs to, so an import can be reviewed or undone later.
    """

    class Status(models.TextChoices):
        PARSED = "parsed", "Ready to import"
        IMPORTED = "imported", "Imported"
        CANCELLED = "cancelled", "Cancelled"
        UNDONE = "undone", "Undone"
        FAILED = "failed", "Failed"

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="import_batches",
    )
    account = models.ForeignKey(
        "accounts.BankAccount",
        on_delete=models.CASCADE,
        related_name="import_batches",
    )
    bank = models.CharField(max_length=32, blank=True)
    status = models.CharField(
        max_length=12,
        choices=Status.choices,
        default=Status.PARSED,
    )
    original_filename = models.CharField(max_length=160, blank=True)
    file_sha256 = models.CharField(max_length=64, blank=True)
    period_start = models.DateField(null=True, blank=True)
    period_end = models.DateField(null=True, blank=True)
    row_count = models.PositiveIntegerField(default=0)
    imported_count = models.PositiveIntegerField(default=0)
    duplicate_count = models.PositiveIntegerField(default=0)
    invalid_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    undone_at = models.DateTimeField(null=True, blank=True)
    error_message = models.CharField(max_length=240, blank=True)
    warning_text = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["owner", "-created_at"])]

    def __str__(self):
        return f"{self.account} · {self.created_at:%d %b %Y}"

    @property
    def ready_count(self):
        return max(0, self.row_count - self.duplicate_count - self.invalid_count)


class ImportRow(models.Model):
    """Temporary staging row used by the preview screen.

    Rows are deleted after the batch is confirmed or cancelled. They do not
    contain the raw CSV file, only the normalized fields needed to review it.
    """

    class Status(models.TextChoices):
        READY = "ready", "Ready"
        DUPLICATE = "duplicate", "Already recorded"
        INVALID = "invalid", "Needs attention"
        IMPORTED = "imported", "Imported"
        SKIPPED = "skipped", "Skipped"

    class Direction(models.TextChoices):
        CREDIT = "credit", "Credit"
        DEBIT = "debit", "Debit"

    batch = models.ForeignKey(
        ImportBatch,
        on_delete=models.CASCADE,
        related_name="rows",
    )
    line_number = models.PositiveIntegerField()
    occurred_at = models.DateTimeField(null=True, blank=True)
    date_only = models.BooleanField(default=True)
    description = models.CharField(max_length=200, blank=True)
    reference = models.CharField(max_length=64, blank=True)
    amount_paise = models.BigIntegerField(null=True, blank=True)
    direction = models.CharField(max_length=6, choices=Direction.choices, blank=True)
    balance_paise = models.BigIntegerField(null=True, blank=True)
    dedup_key = models.CharField(max_length=128, blank=True)
    status = models.CharField(
        max_length=12,
        choices=Status.choices,
        default=Status.READY,
    )
    note = models.CharField(max_length=240, blank=True)

    class Meta:
        ordering = ["line_number"]
        indexes = [models.Index(fields=["batch", "status"])]

    def __str__(self):
        return f"Row {self.line_number} · {self.description[:40]}"
