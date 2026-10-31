"""Categories every new user starts with.

These are a starting point, not a fixed taxonomy. A user can rename or add to
them later. Seeding happens once, at signup, inside the same transaction that
creates the user.
"""

from .models import Category

DEFAULT_CATEGORIES = (
    ("Food & dining", Category.Kind.EXPENSE),
    ("Groceries", Category.Kind.EXPENSE),
    ("Transport", Category.Kind.EXPENSE),
    ("Bills & utilities", Category.Kind.EXPENSE),
    ("Rent", Category.Kind.EXPENSE),
    ("Health", Category.Kind.EXPENSE),
    ("Shopping", Category.Kind.EXPENSE),
    ("Entertainment", Category.Kind.EXPENSE),
    ("Education", Category.Kind.EXPENSE),
    ("Salary", Category.Kind.INCOME),
    ("Other income", Category.Kind.INCOME),
    ("Transfer", Category.Kind.TRANSFER),
    ("Refund", Category.Kind.REFUND),
    ("Uncategorised", Category.Kind.OTHER),
)


def seed_default_categories(user):
    """Create the starter categories for `user`.

    Safe to call twice: the unique constraint on (owner, name) plus
    ignore_conflicts means an existing category is left untouched.
    """
    Category.objects.bulk_create(
        [
            Category(owner=user, name=name, kind=kind, is_default=True)
            for name, kind in DEFAULT_CATEGORIES
        ],
        ignore_conflicts=True,
    )
