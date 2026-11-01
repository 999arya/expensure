"""Cross-user isolation and request-integrity checks.

Every test here answers one question: can a signed-in person reach, change, or
infer something that belongs to someone else? These are the tests that keep the
"multi-user" part of the app honest.
"""

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import BalanceCheckpoint, BankAccount
from ledger.defaults import seed_default_categories
from ledger.models import Transaction

User = get_user_model()

# Every page that shows or changes personal data.
PROTECTED_URLS = (
    "/dashboard/",
    "/accounts/add/",
    "/transactions/",
    "/transactions/add/",
    "/reports/",
)


def make_user(username):
    return User.objects.create_user(username=username, password="test-pass-123")


def make_account(owner, nickname, suffix):
    account = BankAccount.objects.create(
        owner=owner,
        bank=BankAccount.Bank.KOTAK,
        nickname=nickname,
        account_suffix=suffix,
    )
    BalanceCheckpoint.objects.create(
        account=account,
        amount_paise=1_000_000,
        effective_at=timezone.now(),
    )
    return account


def add_txn(account, description):
    return Transaction.objects.create(
        account=account,
        occurred_at=timezone.now(),
        amount_paise=10_000,
        direction=Transaction.Direction.DEBIT,
        description=description,
    )


class AnonymousAccessTests(TestCase):
    def test_every_personal_page_requires_login(self):
        for url in PROTECTED_URLS:
            with self.subTest(url=url):
                response = self.client.get(url)

                self.assertEqual(response.status_code, 302)
                self.assertIn("/accounts/login/", response["Location"])
                self.assertIn(f"next={url}", response["Location"])

    def test_anonymous_post_creates_nothing(self):
        response = self.client.post(
            reverse("accounts:add"),
            {
                "bank": BankAccount.Bank.KOTAK,
                "nickname": "Sneaky",
                "account_type": BankAccount.AccountType.SAVINGS,
                "account_suffix": "1111",
                "opening_balance": "100",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(BankAccount.objects.count(), 0)

    def test_admin_is_closed_to_signed_in_non_staff(self):
        self.client.force_login(make_user("asha"))

        response = self.client.get("/admin/")

        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/login/", response["Location"])


class CrossUserReadTests(TestCase):
    def setUp(self):
        self.asha = make_user("asha")
        self.ravi = make_user("ravi")
        seed_default_categories(self.asha)
        seed_default_categories(self.ravi)

        self.asha_account = make_account(self.asha, "Asha Kotak", "1111")
        self.ravi_account = make_account(self.ravi, "Ravi Kotak", "9999")
        add_txn(self.asha_account, "Asha lunch")
        add_txn(self.ravi_account, "Ravi lunch")

        self.client.force_login(self.asha)

    def test_dashboard_shows_only_your_transactions(self):
        response = self.client.get("/dashboard/")

        self.assertContains(response, "Asha lunch")
        self.assertNotContains(response, "Ravi lunch")

    def test_transaction_list_shows_only_your_transactions(self):
        response = self.client.get(reverse("ledger:list"))

        self.assertContains(response, "Asha lunch")
        self.assertNotContains(response, "Ravi lunch")

    def test_filter_dropdown_lists_only_your_accounts(self):
        response = self.client.get(reverse("ledger:list"))

        self.assertContains(response, "Asha Kotak")
        self.assertNotContains(response, "Ravi Kotak")

    def test_another_users_account_id_as_filter_reveals_nothing(self):
        response = self.client.get(
            reverse("ledger:list"), {"account": self.ravi_account.pk}
        )

        self.assertContains(response, "That account does not exist.")
        self.assertNotContains(response, "Asha lunch")
        self.assertNotContains(response, "Ravi lunch")

    def test_another_users_category_id_as_filter_reveals_nothing(self):
        ravi_food = self.ravi.categories.get(name="Food & dining")

        response = self.client.get(
            reverse("ledger:list"), {"category": ravi_food.pk}
        )

        self.assertContains(response, "That category does not exist.")
        self.assertNotContains(response, "Asha lunch")

    def test_nonexistent_filter_ids_are_errors_not_wildcards(self):
        response = self.client.get(
            reverse("ledger:list"), {"account": "999999", "category": "999999"}
        )

        self.assertContains(response, "That account does not exist.")
        self.assertContains(response, "That category does not exist.")
        self.assertNotContains(response, "Asha lunch")


class CrossUserWriteTests(TestCase):
    def setUp(self):
        self.asha = make_user("asha")
        self.ravi = make_user("ravi")
        seed_default_categories(self.asha)
        seed_default_categories(self.ravi)

        self.asha_account = make_account(self.asha, "Asha Kotak", "1111")
        self.ravi_account = make_account(self.ravi, "Ravi Kotak", "9999")
        self.client.force_login(self.asha)

    def transaction_data(self, **overrides):
        data = {
            "account": self.asha_account.pk,
            "occurred_at": "2026-10-31T14:30",
            "amount": "500",
            "direction": Transaction.Direction.DEBIT,
            "category": self.asha.categories.get(name="Food & dining").pk,
            "description": "Lunch",
            "reference": "",
        }
        data.update(overrides)
        return data

    def test_cannot_post_to_another_users_account(self):
        response = self.client.post(
            reverse("ledger:add"),
            self.transaction_data(account=self.ravi_account.pk),
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("account", response.context["form"].errors)
        self.assertFalse(Transaction.objects.exists())

    def test_cannot_post_with_another_users_category(self):
        ravi_food = self.ravi.categories.get(name="Food & dining")

        response = self.client.post(
            reverse("ledger:add"),
            self.transaction_data(category=ravi_food.pk),
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("category", response.context["form"].errors)
        self.assertFalse(Transaction.objects.exists())

    def test_cannot_choose_another_users_account_by_adding_an_account(self):
        response = self.client.post(
            reverse("accounts:add"),
            {
                "bank": BankAccount.Bank.KOTAK,
                "nickname": "Not mine",
                "account_type": BankAccount.AccountType.SAVINGS,
                "account_suffix": "2222",
                "opening_balance": "100",
                "owner": self.ravi.pk,
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            BankAccount.objects.get(account_suffix="2222").owner, self.asha
        )

    def test_inactive_account_is_not_offered(self):
        self.asha_account.is_active = False
        self.asha_account.save()

        response = self.client.get(reverse("ledger:add"))

        # No active accounts left, so the page sends you to add one.
        self.assertRedirects(response, reverse("accounts:add"))

    def test_category_belongs_to_the_same_owner_as_the_account(self):
        """The database allows any category; the form is the guard.

        This test exists so a future service or bulk import knows it must do the
        same check rather than assuming the schema enforces it.
        """
        ravi_food = self.ravi.categories.get(name="Food & dining")
        txn = Transaction.objects.create(
            account=self.asha_account,
            occurred_at=timezone.now(),
            amount_paise=100,
            direction=Transaction.Direction.DEBIT,
            category=ravi_food,
            description="Cross-owner",
        )

        self.assertEqual(txn.category.owner, self.ravi)


class CsrfTests(TestCase):
    def setUp(self):
        self.user = make_user("asha")
        seed_default_categories(self.user)
        self.account = make_account(self.user, "Asha Kotak", "1111")
        self.client = Client(enforce_csrf_checks=True)
        self.client.force_login(self.user)

    def test_add_account_without_token_is_rejected(self):
        response = self.client.post(
            reverse("accounts:add"),
            {
                "bank": BankAccount.Bank.KOTAK,
                "nickname": "No token",
                "account_type": BankAccount.AccountType.SAVINGS,
                "account_suffix": "3333",
                "opening_balance": "100",
            },
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(BankAccount.objects.count(), 1)

    def test_add_transaction_without_token_is_rejected(self):
        response = self.client.post(
            reverse("ledger:add"),
            {
                "account": self.account.pk,
                "occurred_at": "2026-10-31T14:30",
                "amount": "500",
                "direction": Transaction.Direction.DEBIT,
                "description": "No token",
            },
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(Transaction.objects.exists())

    def test_logout_without_token_is_rejected(self):
        response = self.client.post(reverse("accounts:logout"))

        self.assertEqual(response.status_code, 403)
        self.assertIn("_auth_user_id", self.client.session)


class PasswordStorageTests(TestCase):
    def test_signup_never_stores_the_typed_password(self):
        self.client.post(
            reverse("accounts:signup"),
            {
                "username": "asha",
                "email": "asha@example.com",
                "first_name": "Asha",
                "bank-bank": "kotak",
                "bank-nickname": "Salary",
                "bank-account_type": "savings",
                "bank-account_suffix": "1234",
                "bank-opening_balance": "1000",
                "password1": "quiet-river-42",
                "password2": "quiet-river-42",
            },
        )

        user = User.objects.get(username="asha")
        self.assertNotEqual(user.password, "quiet-river-42")
        self.assertNotIn("quiet-river-42", user.password)
        self.assertTrue(user.check_password("quiet-river-42"))
