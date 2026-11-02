import json
from datetime import date, datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import BalanceCheckpoint, BankAccount
from coach.ai import choose_history_target
from coach.history import apply_target, recommend_target, savings_history
from coach.models import TargetRecommendation
from ledger.models import Category, Transaction
from planning.models import Bill, IncomePlan, MonthlyGoal


def at(month, day=1):
    return timezone.make_aware(datetime(2026, month, day, 0, 0))


class HistoryTargetTests(TestCase):
    def setUp(self):
        self.clock = patch("django.utils.timezone.localdate", return_value=date(2026, 11, 1))
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.user = get_user_model().objects.create_user(username="history-user")
        self.other = get_user_model().objects.create_user(username="other-user")
        self.account = BankAccount.objects.create(owner=self.user, bank="HDFC", nickname="Main", account_suffix="1234")
        BalanceCheckpoint.objects.create(account=self.account, effective_at=at(8), amount_paise=1000000)
        self.income = Category.objects.create(owner=self.user, name="Salary", kind="income")
        self.expense = Category.objects.create(owner=self.user, name="Living", kind="expense")
        for month in [8, 9, 10]:
            self.txn(month, "credit", 5000000, self.income)
            self.txn(month, "debit", 3000000, self.expense)
        self.goal = MonthlyGoal.objects.create(owner=self.user, month=date(2026, 11, 1), savings_paise=1800000)
        IncomePlan.objects.create(owner=self.user, account=self.account, description="Next salary", amount_paise=5000000, expected_on=date(2026, 11, 5))
        Bill.objects.create(owner=self.user, account=self.account, description="Rent", amount_paise=3000000, due_on=date(2026, 11, 10))
        self.client.force_login(self.user)

    def txn(self, month, direction, amount, category, **extra):
        return Transaction.objects.create(account=self.account, occurred_at=at(month, 2), amount_paise=amount, direction=direction, category=category, **extra)

    def test_three_month_estimate_and_excluded_transfers(self):
        transfer = Category.objects.create(owner=self.user, name="Transfer", kind="transfer")
        self.txn(10, "credit", 9000000, transfer)
        summary = savings_history(self.user)
        self.assertEqual(summary["issues"], [])
        self.assertEqual(summary["baseline"], 2000000)
        self.assertEqual(summary["options"], {"cautious": 1200000, "balanced": 1600000})

    def test_refunds_reduce_expenses(self):
        self.txn(10, "credit", 100000, self.expense)
        self.assertEqual(savings_history(self.user)["history"][-1]["expenses"], 2900000)

    def test_recent_decline_reduces_target(self):
        self.txn(10, "debit", 1500000, self.expense)
        self.assertEqual(savings_history(self.user)["options"]["cautious"], 300000)

    def test_negative_surplus_never_proposes_negative_target(self):
        self.txn(10, "debit", 3000000, self.expense)
        self.assertEqual(savings_history(self.user)["options"]["balanced"], 0)

    def test_incomplete_history_and_unreviewed_items_block_recommendation(self):
        txn = self.txn(10, "credit", 123, None, status="needs_review")
        self.assertIsNone(recommend_target(self.user))
        txn.delete()
        self.account.checkpoints.all().delete()
        self.assertIsNone(recommend_target(self.user))

    def test_unclassified_credit_blocks_recommendation(self):
        self.txn(10, "credit", 123, None)
        self.assertIsNone(recommend_target(self.user))

    def test_recommendation_does_not_change_target_until_applied(self):
        proposal = recommend_target(self.user)
        self.goal.refresh_from_db()
        self.assertEqual(self.goal.savings_paise, 1800000)
        self.assertEqual(apply_target(self.user, proposal.pk), "applied")
        self.goal.refresh_from_db()
        self.assertEqual(self.goal.savings_paise, 1200000)
        self.assertEqual(apply_target(self.user, proposal.pk), "already_applied")

    def test_manual_change_invalidates_proposal(self):
        proposal = recommend_target(self.user)
        self.goal.savings_paise = 1000000
        self.goal.save()
        self.assertEqual(apply_target(self.user, proposal.pk), "stale")

    def test_new_history_invalidates_proposal(self):
        proposal = recommend_target(self.user)
        self.txn(10, "debit", 1000000, self.expense)
        self.assertEqual(apply_target(self.user, proposal.pk), "stale")

    def test_month_rollover_invalidates_proposal(self):
        proposal = recommend_target(self.user)
        with patch("django.utils.timezone.localdate", return_value=date(2026, 12, 1)):
            self.assertEqual(apply_target(self.user, proposal.pk), "stale")

    def test_preview_get_has_no_side_effects_and_apply_is_private_post(self):
        self.assertContains(self.client.get(reverse("coach:target_review")), "Adapt your savings target")
        self.assertFalse(TargetRecommendation.objects.exists())
        proposal = recommend_target(self.user)
        url = reverse("coach:target_apply", args=[proposal.pk])
        self.assertEqual(self.client.get(url).status_code, 405)
        self.client.force_login(self.other)
        self.assertEqual(self.client.post(url).status_code, 404)

    def test_consent_and_cache(self):
        with patch("coach.history.savings_history", wraps=savings_history), patch("coach.ai.urllib.request.urlopen") as provider:
            proposal = recommend_target(self.user)
            self.assertEqual(recommend_target(self.user).pk, proposal.pk)
            provider.assert_not_called()

    def test_export_includes_target_audit(self):
        proposal = recommend_target(self.user)
        apply_target(self.user, proposal.pk)
        response = self.client.get(reverse("accounts:export"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["coach"]["target_recommendations"][0]["proposed_paise"], 1200000)

    @patch.dict("os.environ", {"GROQ_API_KEY": "test-key"})
    def test_ai_choice_is_bounded_and_invalid_output_falls_back(self):
        with patch("coach.ai.urllib.request.urlopen") as provider:
            reply = provider.return_value.__enter__.return_value
            def result(content):
                return json.dumps({"choices": [{"finish_reason": "stop", "message": {"content": content}}]}).encode()
            reply.read.return_value = result('{"choice":"balanced"}')
            proposal = recommend_target(self.user, consent=True)
            self.assertEqual(proposal.source, "ai")
            self.assertEqual(proposal.proposed_paise, 1600000)
            payload = json.loads(provider.call_args.args[0].data)
            self.assertNotIn("history-user", json.dumps(payload))
            self.assertEqual(recommend_target(self.user, consent=True).pk, proposal.pk)
            self.assertEqual(provider.call_count, 1)
            reply.read.return_value = result('{"choice":999999}')
            self.assertEqual(choose_history_target({}, allow_ai=True), (False, "cautious"))
            provider.side_effect = OSError("offline")
            self.assertEqual(choose_history_target({}, allow_ai=True), (False, "cautious"))

    def test_current_forecast_caps_target(self):
        IncomePlan.objects.filter(owner=self.user).update(amount_paise=3500000)
        summary = savings_history(self.user)
        self.assertEqual(summary["options"]["balanced"], 500000)

    def test_other_users_history_does_not_influence_target(self):
        account = BankAccount.objects.create(owner=self.other, bank="Other", nickname="Other", account_suffix="9999")
        Transaction.objects.create(account=account, occurred_at=at(10, 3), amount_paise=999999999, direction="credit")
        self.assertEqual(savings_history(self.user)["baseline"], 2000000)
