from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from ledger.models import Transaction

from .models import BalanceCheckpoint, BankAccount
from .services import estimate_balance, latest_checkpoint

User = get_user_model()


def make_user(username="asha"):
    return User.objects.create_user(username=username, password="test-pass-123")


def make_account(owner, suffix="4821", balance=1_000_000, confirmed_at=None):
    account = BankAccount.objects.create(
        owner=owner,
        bank=BankAccount.Bank.KOTAK,
        nickname="Kotak salary",
        account_suffix=suffix,
    )
    BalanceCheckpoint.objects.create(
        account=account,
        amount_paise=balance,
        effective_at=confirmed_at or timezone.now() - timedelta(days=1),
    )
    return account


def add_txn(account, when, amount, direction=Transaction.Direction.DEBIT):
    return Transaction.objects.create(
        account=account,
        occurred_at=when,
        amount_paise=amount,
        direction=direction,
        description="Test",
    )


def local_input(value):
    """Format an aware datetime the way a datetime-local input sends it."""
    return timezone.localtime(value).strftime("%Y-%m-%dT%H:%M")


class ConfirmBalanceViewTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.account = make_account(self.user)
        self.client.force_login(self.user)
        self.url = reverse("accounts:confirm", args=[self.account.pk])

    def post(self, follow=False, **overrides):
        data = {
            "amount": "5,000",
            "effective_at": local_input(timezone.now()),
        }
        data.update(overrides)
        return self.client.post(self.url, data, follow=follow)

    def test_requires_login(self):
        self.client.logout()

        response = self.client.get(self.url)

        self.assertRedirects(
            response, f"/accounts/login/?next={self.url}"
        )

    def test_another_users_account_is_not_found(self):
        other = make_user("ravi")
        their_account = make_account(other, suffix="9999")
        url = reverse("accounts:confirm", args=[their_account.pk])

        response = self.client.get(url)

        self.assertEqual(response.status_code, 404)

    def test_inactive_account_is_not_found(self):
        self.account.is_active = False
        self.account.save()

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 404)

    def test_page_shows_the_current_estimate(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Kotak salary")
        self.assertContains(response, "₹10,000.00")
        self.assertContains(response, "Confirm this balance")

    def test_page_works_before_any_checkpoint_exists(self):
        self.account.checkpoints.all().delete()

        response = self.client.get(self.url)

        self.assertContains(response, "₹0.00")
        self.assertContains(response, "never")

    def test_confirming_creates_a_checkpoint(self):
        response = self.post()

        self.assertRedirects(response, "/")
        checkpoint = self.account.checkpoints.order_by("-effective_at").first()
        self.assertEqual(checkpoint.amount_paise, 500_000)
        self.assertEqual(checkpoint.source, BalanceCheckpoint.Source.MANUAL)
        self.assertEqual(checkpoint.note, "Balance confirmed by you.")

    def test_estimate_becomes_the_confirmed_amount(self):
        # Both movements are in the past, so the typed figure already includes
        # them and they must stop counting.
        add_txn(self.account, timezone.now() - timedelta(days=2), 50_000)
        add_txn(
            self.account,
            timezone.now() - timedelta(hours=2),
            20_000,
            Transaction.Direction.CREDIT,
        )

        self.post()

        self.assertEqual(
            estimate_balance(self.account, latest_checkpoint(self.account)),
            500_000,
        )

    def test_movements_after_the_confirmation_still_count(self):
        confirmed_at = timezone.now() - timedelta(hours=3)

        self.post(effective_at=local_input(confirmed_at))
        add_txn(
            self.account,
            confirmed_at + timedelta(hours=1),
            20_000,
            Transaction.Direction.CREDIT,
        )
        add_txn(self.account, confirmed_at + timedelta(hours=2), 5_000)

        self.assertEqual(
            estimate_balance(self.account, latest_checkpoint(self.account)),
            515_000,
        )

    def test_negative_balance_is_allowed(self):
        self.post(amount="-1,200.50")

        checkpoint = self.account.checkpoints.order_by("-effective_at").first()

        self.assertEqual(checkpoint.amount_paise, -120_050)

    def test_bad_amount_is_rejected(self):
        response = self.post(amount="12.345")

        self.assertEqual(response.status_code, 200)
        self.assertIn("amount", response.context["form"].errors)
        self.assertEqual(self.account.checkpoints.count(), 1)

    def test_missing_amount_is_rejected(self):
        response = self.post(amount="")

        self.assertIn("amount", response.context["form"].errors)
        self.assertEqual(self.account.checkpoints.count(), 1)

    def test_future_time_is_rejected(self):
        future = timezone.now() + timedelta(days=1)

        response = self.post(effective_at=local_input(future))

        self.assertEqual(response.status_code, 200)
        self.assertIn("effective_at", response.context["form"].errors)
        self.assertEqual(self.account.checkpoints.count(), 1)

    def test_backdated_confirmation_is_kept_as_history(self):
        older = timezone.now() - timedelta(days=5)

        self.post(effective_at=local_input(older))

        self.assertEqual(self.account.checkpoints.count(), 2)
        # The newest checkpoint still anchors the estimate.
        self.assertEqual(
            latest_checkpoint(self.account).amount_paise, 1_000_000
        )

    def test_message_reports_the_new_estimate(self):
        response = self.post(amount="7,500", follow=True)

        self.assertContains(response, "Balance confirmed as of")
        self.assertContains(response, "₹7,500.00")

    def test_dashboard_card_links_to_confirm(self):
        response = self.client.get("/")

        self.assertContains(response, self.url)
        self.assertContains(response, "Confirm balance")
