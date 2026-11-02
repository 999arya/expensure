from django.conf import settings
from django.db import models


class FinancialInsight(models.Model):
    """Rule-triggered or AI-generated financial suggestions and affordability assessments."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending review"
        ACCEPTED = "accepted", "Accepted"
        DISMISSED = "dismissed", "Dismissed"

    class Source(models.TextChoices):
        DETERMINISTIC = "deterministic", "Rule Calculation"
        AI = "ai", "AI Assistant"

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="financial_insights",
    )
    title = models.CharField(max_length=128)
    kind = models.CharField(max_length=32, default="general")
    body = models.TextField()
    reason = models.TextField(help_text="Explicit justification based on numbers")
    amount_paise = models.BigIntegerField(null=True, blank=True)
    time_period = models.CharField(max_length=64, blank=True)
    assumptions = models.TextField(blank=True, help_text="Assumptions made in calculation")
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
    )
    source = models.CharField(
        max_length=16,
        choices=Source.choices,
        default=Source.DETERMINISTIC,
    )
    summary_hash = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [models.UniqueConstraint(fields=["owner", "kind", "summary_hash"], condition=~models.Q(summary_hash=""), name="insight_version_once")]

    def __str__(self):
        return f"{self.title} ({self.status})"


class AIUsage(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    day = models.DateField()
    calls = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["owner", "day"], name="ai_usage_owner_day")]


class TargetRecommendation(models.Model):
    """An auditable proposal; applying it is a separate, explicit action."""
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="target_recommendations")
    month = models.DateField()
    previous_paise = models.BigIntegerField()
    proposed_paise = models.BigIntegerField()
    snapshot = models.JSONField()
    snapshot_hash = models.CharField(max_length=64)
    source = models.CharField(max_length=16, choices=FinancialInsight.Source.choices, default="deterministic")
    choice = models.CharField(max_length=16)
    created_at = models.DateTimeField(auto_now_add=True)
    applied_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [models.CheckConstraint(condition=models.Q(proposed_paise__gte=0), name="recommended_target_nonnegative")]
