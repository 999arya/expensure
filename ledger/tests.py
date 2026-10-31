from datetime import datetime, timezone

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase

from accounts.models import BankAccount

from .models import Category, Transaction

User = get_user_model()


def make_user(username="asha"):
    return User.objects.create_user(username=username, password="test-pass-123")


def make_account(owner, suffix="4821", bank=BankAccount.Bank.KOTAK):
    return BankAccount.objects.create(
        owner=owner,
        bank=bank,
        nickname="Kotak",
        account_suffix=suffix,
    )


def make_transaction(account, **overrides):
    values = {
        "account": account,
        "occurred_at": datetime(2026, 10, 31, 9, 0, tzinfo=timezone.utc),
        "amount_paise": 48_600,
        "direction": Transaction.Direction.DEBIT,
        "description": "Swiggy",
    }
    values.update(overrides)
    return Transaction.objects.create(**values)


class TransactionTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.account = make_account(self.user)

    def test_amount_must_be_positive(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                make_transaction(self.account, amount_paise=0)

    def test_negative_amount_is_rejected(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                make_transaction(self.account, amount_paise=-100)

    def test_dedup_key_is_unique_per_account(self):
        make_transaction(self.account, dedup_key="KOTAK-20261031-001")

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                make_transaction(self.account, dedup_key="KOTAK-20261031-001")

    def test_blank_dedup_keys_are_allowed_repeatedly(self):
        make_transaction(self.account)
        make_transaction(self.account)

        self.assertEqual(self.account.transactions.count(), 2)

    def test_same_dedup_key_on_another_account_is_allowed(self):
        other = make_account(self.user, suffix="9034", bank=BankAccount.Bank.BANK_OF_BARODA)
        make_transaction(self.account, dedup_key="REF-1")
        make_transaction(other, dedup_key="REF-1")

        self.assertEqual(Transaction.objects.count(), 2)

    def test_transactions_are_ordered_newest_first(self):
        older = make_transaction(
            self.account,
            occurred_at=datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc),
        )
        newer = make_transaction(
            self.account,
            occurred_at=datetime(2026, 10, 30, 9, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(list(self.account.transactions.all()), [newer, older])


class CategoryTests(TestCase):
    def setUp(self):
        self.user = make_user()

    def test_category_name_is_unique_per_owner(self):
        Category.objects.create(owner=self.user, name="Food")

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Category.objects.create(owner=self.user, name="Food")

    def test_two_users_can_use_the_same_name(self):
        other = make_user("ravi")
        Category.objects.create(owner=self.user, name="Food")
        Category.objects.create(owner=other, name="Food")

        self.assertEqual(Category.objects.filter(name="Food").count(), 2)
