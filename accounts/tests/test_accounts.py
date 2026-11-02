from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from ..models import BalanceCheckpoint, BankAccount

User = get_user_model()

VALID_ACCOUNT = {
    "bank": BankAccount.Bank.KOTAK,
    "nickname": "Kotak salary",
    "account_type": BankAccount.AccountType.SAVINGS,
    "account_suffix": "4821",
    "opening_balance": "24,500.50",
}


def make_user(username="asha"):
    return User.objects.create_user(username=username, password="test-pass-123")


class AddAccountViewTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.client.force_login(self.user)

    def test_add_page_requires_login(self):
        self.client.logout()

        response = self.client.get(reverse("accounts:add"))

        self.assertRedirects(response, "/accounts/login/?next=/accounts/add/")

    def test_add_page_renders(self):
        response = self.client.get(reverse("accounts:add"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Add account")
        self.assertContains(response, "Last 4 digits")
        self.assertContains(response, "Never the full number")

    def test_valid_form_creates_account_and_checkpoint(self):
        response = self.client.post(reverse("accounts:add"), VALID_ACCOUNT)

        self.assertRedirects(response, "/dashboard/")
        account = BankAccount.objects.get()
        self.assertEqual(account.owner, self.user)
        self.assertEqual(account.nickname, "Kotak salary")
        self.assertEqual(account.account_suffix, "4821")

        checkpoint = account.checkpoints.get()
        self.assertEqual(checkpoint.amount_paise, 2_450_050)
        self.assertEqual(checkpoint.source, BalanceCheckpoint.Source.MANUAL)
        self.assertIsNotNone(checkpoint.effective_at)

    def test_success_message_is_shown(self):
        response = self.client.post(reverse("accounts:add"), VALID_ACCOUNT, follow=True)

        self.assertContains(response, "Kotak salary added.")

    def test_suffix_must_be_four_digits(self):
        data = VALID_ACCOUNT | {"account_suffix": "12ab"}

        response = self.client.post(reverse("accounts:add"), data)

        self.assertEqual(response.status_code, 200)
        self.assertIn("account_suffix", response.context["form"].errors)
        self.assertEqual(BankAccount.objects.count(), 0)

    def test_bad_amount_is_rejected(self):
        data = VALID_ACCOUNT | {"opening_balance": "12.345"}

        response = self.client.post(reverse("accounts:add"), data)

        self.assertEqual(response.status_code, 200)
        self.assertIn("opening_balance", response.context["form"].errors)
        self.assertEqual(BankAccount.objects.count(), 0)

    def test_negative_balance_is_allowed(self):
        data = VALID_ACCOUNT | {"opening_balance": "-1,200"}

        self.client.post(reverse("accounts:add"), data)

        checkpoint = BalanceCheckpoint.objects.get()
        self.assertEqual(checkpoint.amount_paise, -120_000)

    def test_duplicate_account_is_rejected_with_a_readable_message(self):
        self.client.post(reverse("accounts:add"), VALID_ACCOUNT)

        response = self.client.post(
            reverse("accounts:add"),
            VALID_ACCOUNT | {"nickname": "Same account again"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "You have already added this account.")
        self.assertEqual(BankAccount.objects.count(), 1)

    def test_same_details_for_another_user_is_allowed(self):
        other = make_user("ravi")
        BankAccount.objects.create(
            owner=other,
            bank=BankAccount.Bank.KOTAK,
            nickname="Ravi Kotak",
            account_suffix="4821",
        )

        response = self.client.post(reverse("accounts:add"), VALID_ACCOUNT)

        self.assertRedirects(response, "/dashboard/")
        self.assertEqual(BankAccount.objects.count(), 2)

    def test_posted_owner_is_ignored(self):
        other = make_user("ravi")

        self.client.post(
            reverse("accounts:add"),
            VALID_ACCOUNT | {"owner": other.pk},
        )

        self.assertEqual(BankAccount.objects.get().owner, self.user)


class BankNameTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.client.force_login(self.user)

    def test_custom_name_is_saved_and_displayed(self):
        response = self.client.post(reverse("accounts:add"), VALID_ACCOUNT | {"bank": "  State   Bank of India  "}, follow=True)
        self.assertContains(response, "State Bank of India")
        self.assertEqual(BankAccount.objects.get().bank, "State Bank of India")

    def test_duplicate_bank_name_ignores_case_and_spacing(self):
        self.client.post(reverse("accounts:add"), VALID_ACCOUNT | {"bank": "HDFC Bank"})
        response = self.client.post(reverse("accounts:add"), VALID_ACCOUNT | {"bank": " hdfc   bank "})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(BankAccount.objects.count(), 1)
        self.assertContains(response, "already added")

    def test_signup_accepts_custom_bank(self):
        from accounts.tests.test_auth import VALID_SIGNUP
        self.client.logout()
        response = self.client.post(reverse("accounts:signup"), VALID_SIGNUP | {"username": "new-user", "bank-bank": "ICICI Bank"})
        self.assertRedirects(response, reverse("core:dashboard"))
        self.assertEqual(BankAccount.objects.get().bank, "ICICI Bank")

    def test_blank_bank_is_rejected(self):
        response = self.client.post(reverse("accounts:add"), VALID_ACCOUNT | {"bank": "   "})
        self.assertIn("bank", response.context["form"].errors)
        self.assertFalse(BankAccount.objects.exists())
