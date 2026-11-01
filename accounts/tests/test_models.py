from datetime import datetime, timezone

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from ..models import BalanceCheckpoint, BankAccount

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


class UserExportAndDeleteTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.client.force_login(self.user)
        self.account = BankAccount.objects.create(
            owner=self.user,
            bank=BankAccount.Bank.KOTAK,
            nickname="Primary Kotak",
            account_suffix="4821",
        )
        BalanceCheckpoint.objects.create(
            account=self.account,
            amount_paise=1_000_000,
            effective_at=datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc),
        )

    def test_export_contains_user_accounts_and_checkpoints(self):
        from django.urls import reverse
        import json

        response = self.client.get(reverse("accounts:export"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/json")

        payload = json.loads(response.content.decode("utf-8"))
        self.assertEqual(payload["user"]["username"], self.user.username)
        self.assertEqual(len(payload["accounts"]), 1)
        self.assertEqual(payload["accounts"][0]["account_suffix"], "4821")
        self.assertEqual(len(payload["accounts"][0]["checkpoints"]), 1)

    def test_export_contains_all_data_domains_without_device_secret(self):
        from django.urls import reverse
        import json
        from ingestion.models import DeviceToken
        from planning.models import Goal

        Goal.objects.create(
            owner=self.user,
            name="Emergency fund",
            target_paise=500_000,
            saved_paise=100_000,
        )
        token, raw_token = DeviceToken.create_token(self.user, "Test phone")

        response = self.client.get(reverse("accounts:export"))
        payload = json.loads(response.content.decode("utf-8"))

        self.assertIn("categories", payload)
        self.assertIn("transfers", payload)
        self.assertEqual(payload["planning"]["goals"][0]["name"], "Emergency fund")
        self.assertEqual(payload["ingestion"]["device_tokens"][0]["name"], "Test phone")
        self.assertNotIn("token_hash", payload["ingestion"]["device_tokens"][0])
        self.assertNotIn(raw_token, response.content.decode("utf-8"))

    def test_delete_account_cancels_on_mismatch(self):
        from django.urls import reverse

        response = self.client.post(
            reverse("accounts:delete"),
            {"confirm_username": "wrong_name"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(User.objects.filter(pk=self.user.pk).exists())

    def test_delete_account_cascades_and_removes_user_data(self):
        from django.urls import reverse

        response = self.client.post(
            reverse("accounts:delete"),
            {"confirm_username": self.user.username},
        )
        self.assertRedirects(response, reverse("accounts:login"))
        self.assertFalse(User.objects.filter(pk=self.user.pk).exists())
        self.assertFalse(BankAccount.objects.filter(owner_id=self.user.pk).exists())
