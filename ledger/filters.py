"""Reading and validating the transaction list filters.

Filters are parsed separately from the view so the rules can be tested
directly, and so a bad date shows a message instead of a 500.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from django.utils import timezone

from accounts.models import BankAccount

from .models import Category


@dataclass
class Filters:
    date_from: date | None = None
    date_to: date | None = None
    category: Category | None = None
    account: BankAccount | None = None
    errors: list[str] = field(default_factory=list)

    @property
    def is_active(self):
        return any((self.date_from, self.date_to, self.category, self.account))


def _parse_date(raw, label, errors):
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        errors.append(f"{label} must look like 2026-10-31.")
        return None


def read_filters(params, user):
    """Turn query parameters into a Filters object.

    Unknown or other users' category and account ids are ignored, so a
    tampered URL can neither widen nor narrow what you are allowed to see.
    """
    errors = []

    filters = Filters(
        date_from=_parse_date(params.get("from", "").strip(), "From date", errors),
        date_to=_parse_date(params.get("to", "").strip(), "To date", errors),
    )

    category_id = params.get("category", "").strip()
    if category_id:
        filters.category = Category.objects.filter(
            owner=user, pk=category_id
        ).first()
        if filters.category is None:
            errors.append("That category does not exist.")

    account_id = params.get("account", "").strip()
    if account_id:
        filters.account = BankAccount.objects.filter(
            owner=user, pk=account_id
        ).first()
        if filters.account is None:
            errors.append("That account does not exist.")

    if filters.date_from and filters.date_to and filters.date_from > filters.date_to:
        errors.append("The from date is after the to date.")
        filters.date_from = None
        filters.date_to = None

    filters.errors = errors
    return filters


def apply_filters(queryset, filters):
    """Apply date and ownership filters to a transaction queryset.

    If any filter was unusable, return nothing rather than quietly showing an
    unfiltered list: the user asked a question and deserves an empty, explained
    answer instead of a misleading one.
    """
    if filters.errors:
        return queryset.none()

    tz = timezone.get_current_timezone()

    if filters.date_from:
        start = timezone.make_aware(
            datetime.combine(filters.date_from, time.min), tz
        )
        queryset = queryset.filter(occurred_at__gte=start)

    if filters.date_to:
        # Exclusive upper bound, so the whole "to" day is included.
        end = timezone.make_aware(
            datetime.combine(filters.date_to + timedelta(days=1), time.min), tz
        )
        queryset = queryset.filter(occurred_at__lt=end)

    if filters.category:
        queryset = queryset.filter(category=filters.category)

    if filters.account:
        queryset = queryset.filter(account=filters.account)

    return queryset
