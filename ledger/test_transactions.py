from datetime import datetime, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import BalanceCheckpoint, BankAccount
from ledger.defaults import seed_default_categories

from .models import Category, Transaction

User = get_user_model()
TZ = timezone.get_current_timezone()


def make_user(username="asha"):
    return User.objects.create_user(username=username, password="test-pass-123")


def make_account(owner, nickname="Kotak salary", suffix="4821"):
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


def at(year, month, day, hour=10, minute=0):
    return timezone.make_aware(datetime(year, month, day, hour, minute), TZ)


def add_txn(account, when, amount=10_000, direction="debit", category=None, description="Test"):
    return Transaction.objects.create(
        account=account,
        occurred_at=when,
        amount_paise=amount,
        direction=direction,
        category=category,
        description=description,
    )


class AddTransactionTests(TestCase):
    def setUp(self):
        self.user = make_user()
        seed_default_categories(self.user)
        self.account = make_account(self.user)
        self.client.force_login(self.user)
        self.category = self.user.categories.get(name="Food & dining")

    def form_data(self, **overrides):
        data = {
            "account": self.account.pk,
            "occurred_at": "2026-10-31T14:30",
            "amount": "1,234.50",
            "direction": Transaction.Direction.DEBIT,
            "category": self.category.pk,
            "description": "Swiggy",
            "reference": "",
        }
        data.update(overrides)
        return data

    def test_add_page_requires_login(self):
        self.client.logout()

        response = self.client.get(reverse("ledger:add"))

        self.assertRedirects(
            response, "/accounts/login/?next=/transactions/add/"
        )

    def test_add_page_renders(self):
        response = self.client.get(reverse("ledger:add"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Add transaction")
        self.assertContains(response, "Kotak salary")

    def test_only_account_is_preselected(self):
        response = self.client.get(reverse("ledger:add"))

        field = response.context["form"]["account"]

        self.assertEqual(field.value(), self.account.pk)
        self.assertIsNone(field.field.empty_label)

    def test_two_accounts_leave_the_choice_open(self):
        second = make_account(self.user, nickname="BoB", suffix="9034")

        response = self.client.get(reverse("ledger:add"))

        field = response.context["form"]["account"]

        self.assertIn(field.value(), (None, ""))
        self.assertEqual(field.field.empty_label, "---------")
        self.assertEqual(
            set(field.field.queryset.values_list("pk", flat=True)),
            {self.account.pk, second.pk},
        )

    def test_without_an_account_you_are_sent_to_add_one(self):
        self.account.delete()

        response = self.client.get(reverse("ledger:add"), follow=True)

        self.assertRedirects(response, reverse("accounts:add"))
        self.assertContains(response, "Add an account before recording a transaction.")

    def test_valid_transaction_is_saved(self):
        response = self.client.post(reverse("ledger:add"), self.form_data())

        self.assertRedirects(response, reverse("ledger:list"))
        txn = Transaction.objects.get()
        self.assertEqual(txn.account, self.account)
        self.assertEqual(txn.amount_paise, 123_450)
        self.assertEqual(txn.direction, Transaction.Direction.DEBIT)
        self.assertEqual(txn.category, self.category)
        self.assertEqual(txn.description, "Swiggy")
        self.assertEqual(txn.status, Transaction.Status.POSTED)
        self.assertEqual(txn.source, Transaction.Source.MANUAL)
        self.assertEqual(txn.occurred_at, at(2026, 10, 31, 14, 30))

    def test_zero_amount_is_rejected(self):
        response = self.client.post(reverse("ledger:add"), self.form_data(amount="0"))

        self.assertEqual(response.status_code, 200)
        self.assertIn("amount", response.context["form"].errors)
        self.assertEqual(Transaction.objects.count(), 0)

    def test_negative_amount_is_rejected(self):
        response = self.client.post(
            reverse("ledger:add"), self.form_data(amount="-50")
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("amount", response.context["form"].errors)
        self.assertEqual(Transaction.objects.count(), 0)

    def test_malformed_amount_is_rejected(self):
        response = self.client.post(
            reverse("ledger:add"), self.form_data(amount="12.345")
        )

        self.assertIn("amount", response.context["form"].errors)

    def test_description_is_required(self):
        response = self.client.post(
            reverse("ledger:add"), self.form_data(description="")
        )

        self.assertIn("description", response.context["form"].errors)

    def test_another_users_account_is_rejected(self):
        other = make_user("ravi")
        their_account = make_account(other, nickname="Ravi account", suffix="9999")

        response = self.client.post(
            reverse("ledger:add"), self.form_data(account=their_account.pk)
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("account", response.context["form"].errors)
        self.assertEqual(Transaction.objects.count(), 0)

    def test_another_users_category_is_rejected(self):
        other = make_user("ravi")
        seed_default_categories(other)
        their_category = other.categories.get(name="Food & dining")

        response = self.client.post(
            reverse("ledger:add"), self.form_data(category=their_category.pk)
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("category", response.context["form"].errors)
        self.assertEqual(Transaction.objects.count(), 0)

    def test_category_is_optional(self):
        self.client.post(reverse("ledger:add"), self.form_data(category=""))

        self.assertIsNone(Transaction.objects.get().category)

    def test_success_message_is_shown(self):
        response = self.client.post(
            reverse("ledger:add"), self.form_data(), follow=True
        )

        self.assertContains(response, "Transaction recorded.")

    def test_saving_moves_the_estimate(self):
        # The checkpoint is older than the new transaction, so it counts.
        self.account.checkpoints.update(effective_at=at(2026, 10, 1))

        self.client.post(reverse("ledger:add"), self.form_data())

        response = self.client.get("/")

        # 10,000.00 confirmed minus 1,234.50 spent.
        self.assertContains(response, "₹8,765.50")


class TransactionListTests(TestCase):
    def setUp(self):
        self.user = make_user()
        seed_default_categories(self.user)
        self.account = make_account(self.user)
        self.food = self.user.categories.get(name="Food & dining")
        self.transport = self.user.categories.get(name="Transport")
        self.client.force_login(self.user)

    def test_list_requires_login(self):
        self.client.logout()

        response = self.client.get(reverse("ledger:list"))

        self.assertRedirects(
            response, "/accounts/login/?next=/transactions/"
        )

    def test_list_shows_only_your_transactions(self):
        add_txn(self.account, at(2026, 10, 31), description="Mine")
        other = make_account(make_user("ravi"), nickname="Ravi account", suffix="9999")
        add_txn(other, at(2026, 10, 31), description="Theirs")

        response = self.client.get(reverse("ledger:list"))

        self.assertContains(response, "Mine")
        self.assertNotContains(response, "Theirs")

    def test_date_range_filter(self):
        add_txn(self.account, at(2026, 10, 1), description="First")
        add_txn(self.account, at(2026, 10, 15), description="Middle")
        add_txn(self.account, at(2026, 11, 2), description="Later")

        response = self.client.get(
            reverse("ledger:list"), {"from": "2026-10-10", "to": "2026-10-31"}
        )

        self.assertContains(response, "Middle")
        self.assertNotContains(response, "First")
        self.assertNotContains(response, "Later")
        self.assertContains(response, "1 transaction")

    def test_whole_to_day_is_included(self):
        add_txn(self.account, at(2026, 10, 31, 23, 59), description="Late night")

        response = self.client.get(reverse("ledger:list"), {"to": "2026-10-31"})

        self.assertContains(response, "Late night")

    def test_category_filter(self):
        add_txn(self.account, at(2026, 10, 20), description="Lunch", category=self.food)
        add_txn(
            self.account, at(2026, 10, 21), description="Bus", category=self.transport
        )

        response = self.client.get(
            reverse("ledger:list"), {"category": self.food.pk}
        )

        self.assertContains(response, "Lunch")
        self.assertNotContains(response, "Bus")

    def test_account_filter(self):
        second = make_account(self.user, nickname="BoB", suffix="9034")
        add_txn(self.account, at(2026, 10, 20), description="From Kotak")
        add_txn(second, at(2026, 10, 20), description="From BoB")

        response = self.client.get(
            reverse("ledger:list"), {"account": second.pk}
        )

        self.assertContains(response, "From BoB")
        self.assertNotContains(response, "From Kotak")

    def test_invalid_date_shows_a_message(self):
        response = self.client.get(reverse("ledger:list"), {"from": "31/10/2026"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "From date must look like 2026-10-31.")

    def test_reversed_range_shows_a_message(self):
        response = self.client.get(
            reverse("ledger:list"), {"from": "2026-11-01", "to": "2026-10-01"}
        )

        self.assertContains(response, "The from date is after the to date.")

    def test_another_users_category_filter_reveals_nothing(self):
        other = make_user("ravi")
        seed_default_categories(other)
        their_category = other.categories.get(name="Food & dining")
        add_txn(self.account, at(2026, 10, 20), description="Mine")

        response = self.client.get(
            reverse("ledger:list"), {"category": their_category.pk}
        )

        self.assertContains(response, "That category does not exist.")
        self.assertNotContains(response, "Mine")

    def test_filter_narrows_the_list(self):
        for day in range(1, 32):
            add_txn(self.account, at(2026, 10, day), description=f"Day {day}")
        for day in range(1, 21):
            add_txn(self.account, at(2026, 9, day), description=f"Sep {day}")

        response = self.client.get(
            reverse("ledger:list"), {"from": "2026-10-01", "to": "2026-10-31"}
        )

        self.assertEqual(len(response.context["transactions"]), 31)
        self.assertContains(response, "Day 31")
        self.assertNotContains(response, "Sep 1")

    def test_second_page(self):
        for i in range(51):
            add_txn(
                self.account,
                at(2026, 10, 1) + timedelta(minutes=i),
                description=f"Row {i}",
            )

        first = self.client.get(reverse("ledger:list"))
        second = self.client.get(reverse("ledger:list"), {"page": 2})

        self.assertEqual(first.context["page_obj"].paginator.num_pages, 2)
        self.assertEqual(len(first.context["transactions"]), 50)
        self.assertEqual(len(second.context["transactions"]), 1)

    def test_empty_list_invites_adding(self):
        response = self.client.get(reverse("ledger:list"))

        self.assertContains(response, "No transactions recorded yet.")
        self.assertContains(response, reverse("ledger:add"))


class CategoryOwnershipTests(TestCase):
    def test_categories_are_never_shared(self):
        asha = make_user("asha")
        ravi = make_user("ravi")
        seed_default_categories(asha)
        seed_default_categories(ravi)

        self.assertEqual(Category.objects.filter(owner=asha).count(), 14)
        self.assertFalse(
            asha.categories.filter(pk__in=ravi.categories.values("pk")).exists()
        )
