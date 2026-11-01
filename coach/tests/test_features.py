from datetime import date, datetime
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import BalanceCheckpoint, BankAccount
from ledger.defaults import seed_default_categories
from ledger.models import Transaction
from planning.models import AccountGoal, Bill, MonthlyGoal
from coach.ai import query_ai_coach
from coach.calculations import evaluate_affordability
from coach.models import FinancialInsight

User = get_user_model()


def at(y, m, d, h=12, minute=0):
    return timezone.make_aware(datetime(y, m, d, h, minute))


class CoachTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="arun",
            password="testpassword123",
        )
        seed_default_categories(self.user)
        self.account = BankAccount.objects.create(
            owner=self.user,
            bank=BankAccount.Bank.KOTAK,
            nickname="Primary",
            account_suffix="1234",
        )
        # Starting balance ₹25,000
        BalanceCheckpoint.objects.create(
            account=self.account,
            amount_paise=2_500_000,
            effective_at=at(2026, 10, 1, 0, 0),
        )
        # Unpaid bills U = ₹4,000
        Bill.objects.create(
            owner=self.user,
            description="Electricity",
            amount_paise=400_000,
            due_on=date(2026, 10, 25),
        )
        # Account floor F = ₹5,000
        AccountGoal.objects.create(
            account=self.account,
            minimum_floor_paise=500_000,
            target_paise=500_000,
        )
        # Monthly savings goal G = ₹5,000
        MonthlyGoal.objects.create(
            owner=self.user,
            month=date(2026, 10, 1),
            savings_paise=500_000,
        )
        # Note: B0 = ₹25,000, P = max(5000, 5000, 25000+5000) = ₹30,000
        # If C = 25,000, U = 4,000, P = 30,000 -> allowance = 25,000 - 4,000 - 30,000 = -9,000 (Shortfall)
        # Let's adjust checkpoint to ₹40,000 so allowance is positive
        self.account.checkpoints.all().delete()
        BalanceCheckpoint.objects.create(
            account=self.account,
            amount_paise=4_000_000,  # ₹40,000
            effective_at=at(2026, 10, 1, 0, 0),
        )
        # With B0=₹40,000, G=₹5,000 -> P=₹45,000.
        # Let's add income of ₹20,000 posted in month: C = ₹60,000
        Transaction.objects.create(
            account=self.account,
            amount_paise=2_000_000,
            direction=Transaction.Direction.CREDIT,
            status=Transaction.Status.POSTED,
            occurred_at=at(2026, 10, 5),
            description="Salary",
        )
        # Now:
        # C = ₹60,000
        # U = ₹4,000
        # B0 = ₹40,000, G = ₹5,000 -> P = ₹45,000
        # Cash-backed allowance = C - U - P = 60,000 - 4,000 - 45,000 = ₹11,000
        # Forecast allowance = ₹11,000 (R = 0)

    def test_evaluate_affordability_when_affordable(self):
        eval_res = evaluate_affordability(self.user, 400_000, today=date(2026, 10, 15))  # ₹4,000
        self.assertTrue(eval_res.can_afford_now)
        self.assertTrue(eval_res.can_afford_forecast)
        self.assertEqual(eval_res.cash_backed_paise, 1_100_000)
        self.assertEqual(eval_res.new_forecast_paise, 700_000)  # 11,000 - 4,000 = ₹7,000
        self.assertEqual(eval_res.shortfall_paise, 0)
        self.assertIn("Yes, you can afford", eval_res.verdict)

    def test_evaluate_affordability_when_exceeds_allowance(self):
        eval_res = evaluate_affordability(self.user, 1_500_000, today=date(2026, 10, 15))  # ₹15,000
        self.assertFalse(eval_res.can_afford_now)
        self.assertFalse(eval_res.can_afford_forecast)
        self.assertEqual(eval_res.shortfall_paise, 400_000)  # 15,000 - 11,000 = ₹4,000 shortfall
        self.assertIn("Not recommended", eval_res.verdict)

    def test_fallback_without_ai_key(self):
        """Dashboard and coach work 100% deterministically without an OpenAI API key."""
        is_ai, res = query_ai_coach(
            prompt_context={},
            user_question="Can I afford ₹4,000?",
            affordability_eval=evaluate_affordability(self.user, 400_000, today=date(2026, 10, 15)),
        )
        self.assertFalse(is_ai)
        self.assertEqual(res["source"], "deterministic")
        self.assertIn("Yes, you can afford", res["verdict"])

    def test_insight_accept_and_dismiss(self):
        insight = FinancialInsight.objects.create(
            owner=self.user,
            title="Spend alert",
            body="Consider setting a dining budget.",
            reason="Dining was 40% of expenses.",
        )
        self.assertEqual(insight.status, FinancialInsight.Status.PENDING)

        self.client.force_login(self.user)
        # Accept
        self.client.post(reverse("coach:insight_accept", args=[insight.pk]))
        insight.refresh_from_db()
        self.assertEqual(insight.status, FinancialInsight.Status.ACCEPTED)

        # Dismiss
        self.client.post(reverse("coach:insight_dismiss", args=[insight.pk]))
        insight.refresh_from_db()
        self.assertEqual(insight.status, FinancialInsight.Status.DISMISSED)

    def test_affordability_post_view(self):
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("coach:afford"),
            {"amount": "4,000"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "AFFORDABILITY BREAKDOWN")
        self.assertContains(response, "Requested purchase")

        # Insight was saved
        saved_insight = FinancialInsight.objects.filter(owner=self.user, kind="affordability").first()
        self.assertIsNotNone(saved_insight)
        self.assertEqual(saved_insight.amount_paise, 400_000)
