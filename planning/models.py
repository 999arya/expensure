"""Goals and the month's plan.

A goal is money set aside on purpose. The app cannot see which rupees are
earmarked, so the saved amount is entered by the user and updated when they
add to it.

Planned income and bills feed the spending allowance:

    allowance = estimated balances + still-expected income − unpaid bills

`R` (still-expected income) only counts items that have not been settled, so it
can never double-count a transaction already inside the balance. `U` (unpaid
bills) only counts items that have not been paid. Settling an item records the
transaction and marks the item at the same time, so paying a bill lowers both
`C` and `U` and leaves the allowance unchanged.
"""

import calendar
from datetime import date, datetime, time

from django.conf import settings
from django.db import models
from django.db.models import F, Q
from django.utils import timezone


class Goal(models.Model):
    """A savings target the user tracks by hand."""

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="goals",
    )
    name = models.CharField(max_length=64)
    target_paise = models.BigIntegerField()
    saved_paise = models.BigIntegerField(default=0)
    target_date = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-is_active", F("target_date").asc(nulls_last=True), "name"]
        constraints = [
            models.CheckConstraint(
                condition=Q(target_paise__gt=0),
                name="goal_target_positive",
            ),
            models.CheckConstraint(
                condition=Q(saved_paise__gte=0),
                name="goal_saved_not_negative",
            ),
        ]

    def __str__(self):
        return self.name

    @property
    def percent(self):
        """Progress for a progress bar, capped at 100."""
        if self.target_paise <= 0:
            return 0
        return min(100, (self.saved_paise * 100) // self.target_paise)

    @property
    def remaining_paise(self):
        return max(0, self.target_paise - self.saved_paise)

    @property
    def is_complete(self):
        return self.saved_paise >= self.target_paise


class PlannedItem(models.Model):
    """Fields shared by expected income and bills."""

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
    )
    description = models.CharField(max_length=200)
    amount_paise = models.BigIntegerField()
    account = models.ForeignKey(
        "accounts.BankAccount",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    settled_transaction = models.ForeignKey(
        "ledger.Transaction",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        abstract = True
        constraints = [
            models.CheckConstraint(
                condition=Q(amount_paise__gt=0),
                name="%(class)s_amount_positive",
            )
        ]

    def __str__(self):
        return self.description


class IncomePlan(PlannedItem):
    """Money expected to arrive, such as a salary or a client payment."""

    class Status(models.TextChoices):
        EXPECTED = "expected", "Expected"
        RECEIVED = "received", "Received"

    expected_on = models.DateField()
    status = models.CharField(
        max_length=8,
        choices=Status.choices,
        default=Status.EXPECTED,
    )

    class Meta(PlannedItem.Meta):
        ordering = ["expected_on", "id"]

    @property
    def is_open(self):
        return self.status == self.Status.EXPECTED


class Bill(PlannedItem):
    """A bill that is expected but not paid yet."""

    class Status(models.TextChoices):
        UNPAID = "unpaid", "Unpaid"
        PAID = "paid", "Paid"

    due_on = models.DateField()
    status = models.CharField(
        max_length=6,
        choices=Status.choices,
        default=Status.UNPAID,
    )

    class Meta(PlannedItem.Meta):
        ordering = ["due_on", "id"]

    @property
    def is_open(self):
        return self.status == self.Status.UNPAID


class MonthlyGoal(models.Model):
    """Monthly net savings target (G)."""

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="monthly_goals",
    )
    month = models.DateField(help_text="First day of the target month")
    savings_paise = models.BigIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-month"]
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "month"],
                name="unique_monthly_goal_per_user",
            ),
            models.CheckConstraint(
                condition=Q(savings_paise__gte=0),
                name="monthly_goal_savings_not_negative",
            ),
        ]

    def __str__(self):
        return f"{self.month:%b %Y}: {self.savings_paise} paise"


class AccountGoal(models.Model):
    """Account-level minimum floor (F) and optional target (T)."""

    account = models.OneToOneField(
        "accounts.BankAccount",
        on_delete=models.CASCADE,
        related_name="goal",
    )
    minimum_floor_paise = models.BigIntegerField(default=0)
    target_paise = models.BigIntegerField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(minimum_floor_paise__gte=0),
                name="account_goal_floor_not_negative",
            ),
            models.CheckConstraint(
                condition=Q(target_paise__isnull=True) | Q(target_paise__gte=0),
                name="account_goal_target_not_negative",
            ),
        ]

    def __str__(self):
        return f"{self.account}: Floor {self.minimum_floor_paise}, Target {self.target_paise}"

    @property
    def effective_target_paise(self):
        """Default unset target to floor; target is at least floor."""
        if self.target_paise is not None:
            return max(self.minimum_floor_paise, self.target_paise)
        return self.minimum_floor_paise


def month_bounds(today=None):
    """First and last day of `today`'s month."""
    today = today or timezone.localdate()
    last_day = calendar.monthrange(today.year, today.month)[1]
    return date(today.year, today.month, 1), date(today.year, today.month, last_day)


def month_end(today=None):
    return month_bounds(today)[1]


def settled_datetime(day, today=None):
    """A sensible occurrence time for an item settled on `day`.

    Today uses the current moment so a movement recorded now lands after the
    latest checkpoint. Other days use noon, which is unambiguous enough and
    lets the review queue handle a checkpoint from the same day.
    """
    today = today or timezone.localdate()
    if day == today:
        return timezone.now()
    return timezone.make_aware(datetime.combine(day, time(12, 0)))
