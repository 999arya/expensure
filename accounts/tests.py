from datetime import datetime, timezone

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from .models import BalanceCheckpoint, BankAccount

User = get_user_model()


def make_user(username="asha"):
    return User.objects.create_user(username=username, password="test-pass-123")


class BankAccountTests(TestCase):
    def setUp(self):
        self.user = make_user()

    def test_suffix_must_be_four_digits(self):
        account = BankAccount(
            owner=self.user,
            bank=BankAccount.Bank.KOTAK,
            nickname="Kotak",
            account_suffix="12ab",
        )

        with self.assertRaises(ValidationError):
            account.full_clean()

    def test_duplicate_account_is_rejected(self):
        BankAccount.objects.create(
            owner=self.user,
            bank=BankAccount.Bank.KOTAK,
            nickname="Kotak",
            account_suffix="4821",
        )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                BankAccount.objects.create(
                    owner=self.user,
                    bank=BankAccount.Bank.KOTAK,
                    nickname="Kotak again",
                    account_suffix="4821",
                )

    def test_same_suffix_at_another_bank_is_allowed(self):
        BankAccount.objects.create(
            owner=self.user,
            bank=BankAccount.Bank.KOTAK,
            nickname="Kotak",
            account_suffix="4821",
        )
        BankAccount.objects.create(
            owner=self.user,
            bank=BankAccount.Bank.BANK_OF_BARODA,
            nickname="BoB",
            account_suffix="4821",
        )

        self.assertEqual(self.user.bank_accounts.count(), 2)

    def test_same_account_at_another_owner_is_allowed(self):
        other = make_user("ravi")
        BankAccount.objects.create(
            owner=self.user,
            bank=BankAccount.Bank.KOTAK,
            nickname="Kotak",
            account_suffix="4821",
        )
        BankAccount.objects.create(
            owner=other,
            bank=BankAccount.Bank.KOTAK,
            nickname="Kotak",
            account_suffix="4821",
        )

        self.assertEqual(BankAccount.objects.count(), 2)


class BalanceCheckpointTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.account = BankAccount.objects.create(
            owner=self.user,
            bank=BankAccount.Bank.KOTAK,
            nickname="Kotak",
            account_suffix="4821",
        )

    def test_checkpoints_are_ordered_newest_first(self):
        older = BalanceCheckpoint.objects.create(
            account=self.account,
            amount_paise=1_000_000,
            effective_at=datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc),
        )
        newer = BalanceCheckpoint.objects.create(
            account=self.account,
            amount_paise=2_000_000,
            effective_at=datetime(2026, 10, 5, 9, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(list(self.account.checkpoints.all()), [newer, older])

    def test_recorded_at_is_set_on_save(self):
        checkpoint = BalanceCheckpoint.objects.create(
            account=self.account,
            amount_paise=1_000_000,
            effective_at=datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc),
        )

        self.assertIsNotNone(checkpoint.recorded_at)
