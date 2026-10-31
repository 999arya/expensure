from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from ledger.models import Transaction

from .models import BalanceCheckpoint, BankAccount
from .services import account_cards, estimate_balance

User = get_user_model()


def make_user(username="asha"):
    return User.objects.create_user(username=username, password="test-pass-123")


def make_account(owner, suffix="4821", bank=BankAccount.Bank.KOTAK):
    return BankAccount.objects.create(
        owner=owner,
        bank=bank,
        nickname="Kotak savings",
        account_suffix=suffix,
    )


def add_txn(account, amount, direction, occurred_at, status=Transaction.Status.POSTED):
    return Transaction.objects.create(
        account=account,
        occurred_at=occurred_at,
        amount_paise=amount,
        direction=direction,
        description="Test",
        status=status,
    )


class EstimateBalanceTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.account = make_account(self.user)
        self.now = timezone.now()
        self.checkpoint = BalanceCheckpoint.objects.create(
            account=self.account,
            amount_paise=1_000_000,
            effective_at=self.now,
        )

    def test_checkpoint_alone_is_the_estimate(self):
        self.assertEqual(estimate_balance(self.account, self.checkpoint), 1_000_000)

    def test_later_credit_is_added(self):
        add_txn(
            self.account,
            200_000,
            Transaction.Direction.CREDIT,
            self.now + timedelta(hours=1),
        )

        self.assertEqual(estimate_balance(self.account, self.checkpoint), 1_200_000)

    def test_later_debit_is_subtracted(self):
        add_txn(
            self.account,
            50_000,
            Transaction.Direction.DEBIT,
            self.now + timedelta(hours=1),
        )

        self.assertEqual(estimate_balance(self.account, self.checkpoint), 950_000)

    def test_transaction_at_checkpoint_time_is_not_double_counted(self):
        add_txn(self.account, 50_000, Transaction.Direction.DEBIT, self.now)

        self.assertEqual(estimate_balance(self.account, self.checkpoint), 1_000_000)

    def test_transaction_before_checkpoint_is_not_counted(self):
        add_txn(
            self.account,
            50_000,
            Transaction.Direction.DEBIT,
            self.now - timedelta(days=1),
        )

        self.assertEqual(estimate_balance(self.account, self.checkpoint), 1_000_000)

    def test_needs_review_is_not_counted(self):
        add_txn(
            self.account,
            50_000,
            Transaction.Direction.DEBIT,
            self.now + timedelta(hours=1),
            status=Transaction.Status.NEEDS_REVIEW,
        )

        self.assertEqual(estimate_balance(self.account, self.checkpoint), 1_000_000)

    def test_another_accounts_transactions_are_not_counted(self):
        other = make_account(self.user, suffix="9999")
        add_txn(
            other,
            50_000,
            Transaction.Direction.DEBIT,
            self.now + timedelta(hours=1),
        )

        self.assertEqual(estimate_balance(self.account, self.checkpoint), 1_000_000)

    def test_no_checkpoint_means_zero(self):
        self.assertEqual(estimate_balance(self.account, None), 0)


class AccountCardsTests(TestCase):
    def setUp(self):
        self.user = make_user()

    def test_only_own_active_accounts_are_listed(self):
        mine = make_account(self.user)
        make_account(make_user("ravi"), suffix="9999")
        inactive = make_account(self.user, suffix="1111")
        inactive.is_active = False
        inactive.save()

        cards = account_cards(self.user)

        self.assertEqual([card.nickname for card in cards], ["Kotak savings"])

    def test_card_uses_latest_checkpoint(self):
        account = make_account(self.user)
        now = timezone.now()
        BalanceCheckpoint.objects.create(
            account=account, amount_paise=100, effective_at=now - timedelta(days=2)
        )
        latest = BalanceCheckpoint.objects.create(
            account=account, amount_paise=500_000, effective_at=now
        )

        card = account_cards(self.user)[0]

        self.assertEqual(card.balance_paise, 500_000)
        self.assertEqual(card.confirmed_at, latest.effective_at)

    def test_card_reports_latest_activity(self):
        account = make_account(self.user)
        now = timezone.now()
        add_txn(account, 100, Transaction.Direction.DEBIT, now - timedelta(days=3))
        newest = add_txn(account, 100, Transaction.Direction.CREDIT, now)

        card = account_cards(self.user)[0]

        self.assertEqual(card.captured_at, newest.occurred_at)

    def test_card_without_activity_has_no_captured_time(self):
        make_account(self.user)

        card = account_cards(self.user)[0]

        self.assertIsNone(card.captured_at)
        self.assertIsNone(card.confirmed_at)

    def test_card_shows_bank_display_name(self):
        make_account(self.user)

        card = account_cards(self.user)[0]

        self.assertEqual(card.bank, "Kotak Mahindra Bank")
        self.assertEqual(card.suffix, "4821")
