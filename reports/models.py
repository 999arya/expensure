from django.conf import settings
from django.db import models


class RecurringPaymentRule(models.Model):
    """A recurring payment or merchant pattern confirmed or dismissed by the user."""

    class Status(models.TextChoices):
        SUGGESTED = "suggested", "Suggested"
        CONFIRMED = "confirmed", "Confirmed"
        DISMISSED = "dismissed", "Dismissed"

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="recurring_rules",
    )
    merchant_name = models.CharField(max_length=128)
    typical_amount_paise = models.BigIntegerField(null=True, blank=True)
    category = models.ForeignKey(
        "ledger.Category",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.SUGGESTED,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["merchant_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "merchant_name"],
                name="unique_recurring_rule_per_user",
            )
        ]

    def __str__(self):
        return f"{self.merchant_name} ({self.status})"


class MonthlyReportSnapshot(models.Model):
    """Archived monthly summary for historical retention and scheduled generation."""

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="report_snapshots",
    )
    year = models.PositiveSmallIntegerField()
    month = models.PositiveSmallIntegerField()
    income_paise = models.BigIntegerField(default=0)
    expenses_paise = models.BigIntegerField(default=0)
    savings_paise = models.BigIntegerField(default=0)
    generated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-year", "-month"]
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "year", "month"],
                name="unique_snapshot_per_user_month",
            )
        ]

    def __str__(self):
        return f"{self.owner} · {self.year}-{self.month:02d}"
