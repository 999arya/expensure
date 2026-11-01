import csv
import io
from datetime import date, datetime
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import BalanceCheckpoint, BankAccount
from ledger.defaults import seed_default_categories
from ledger.models import Category, Transaction
from planning.models import MonthlyGoal
from reports.models import RecurringPaymentRule
from reports.services import calculate_monthly_report, export_transactions_csv

User = get_user_model()


def at(y, m, d, h=12, minute=0):
    return timezone.make_aware(datetime(y, m, d, h, minute))


class ReportsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="arun",
            password="testpassword123",
            date_joined=at(2026, 9, 15),  # mid-month signup in Sept
        )
        seed_default_categories(self.user)
        self.account = BankAccount.objects.create(
            owner=self.user,
            bank=BankAccount.Bank.KOTAK,
            nickname="Primary",
            account_suffix="1234",
        )
        BalanceCheckpoint.objects.create(
            account=self.account,
            amount_paise=1_000_000,  # ₹10,000
            effective_at=at(2026, 10, 1, 0, 0),
        )

        self.cat_salary = Category.objects.get(owner=self.user, name="Salary")
        self.cat_food = Category.objects.get(owner=self.user, name="Food & dining")
        self.cat_bills = Category.objects.get(owner=self.user, name="Bills & utilities")

    def test_monthly_report_calculations(self):
        """Completion check: charts match transaction totals, transfers/corrections excluded."""
        # 1. Income: ₹50,000
        Transaction.objects.create(
            account=self.account,
            amount_paise=5_000_000,
            direction=Transaction.Direction.CREDIT,
            category=self.cat_salary,
            status=Transaction.Status.POSTED,
            occurred_at=at(2026, 10, 5),
            description="October Salary",
        )

        # 2. Expenses: Food ₹4,000, Bills ₹2,500
        Transaction.objects.create(
            account=self.account,
            amount_paise=400_000,
            direction=Transaction.Direction.DEBIT,
            category=self.cat_food,
            status=Transaction.Status.POSTED,
            occurred_at=at(2026, 10, 10),
            description="Swiggy",
        )
        Transaction.objects.create(
            account=self.account,
            amount_paise=250_000,
            direction=Transaction.Direction.DEBIT,
            category=self.cat_bills,
            status=Transaction.Status.POSTED,
            occurred_at=at(2026, 10, 12),
            description="Bescom Electric",
        )

        # 3. Refund: ₹500 on Food
        Transaction.objects.create(
            account=self.account,
            amount_paise=50_000,
            direction=Transaction.Direction.CREDIT,
            category=self.cat_food,
            status=Transaction.Status.POSTED,
            occurred_at=at(2026, 10, 15),
            description="Swiggy Refund",
        )

        # 4. Transfer to second account: ₹5,000 (must be excluded from spending and income)
        acc2 = BankAccount.objects.create(
            owner=self.user,
            bank=BankAccount.Bank.BANK_OF_BARODA,
            nickname="Secondary",
            account_suffix="5678",
        )
        from ledger.services import create_transfer

        create_transfer(
            owner=self.user,
            from_account=self.account,
            to_account=acc2,
            amount_paise=500_000,
            occurred_at=at(2026, 10, 16),
            description="Transfer to BoB",
        )

        # Goal: ₹30,000 savings
        MonthlyGoal.objects.create(
            owner=self.user,
            month=date(2026, 10, 1),
            savings_paise=3_000_000,
        )

        report = calculate_monthly_report(self.user, 2026, 10)

        # Income: exactly ₹50,000 (5,000,000 paise). Excludes transfer credit leg.
        self.assertEqual(report.income_paise, 5_000_000)

        # Expenses: Food net (4000 - 500 = 3500) + Bills (2500) = 6000 (600,000 paise)
        # Excludes transfer debit leg!
        self.assertEqual(report.expenses_paise, 600_000)
        self.assertEqual(report.refunds_paise, 50_000)

        # Net savings: 50,000 - 6,000 = 44,000
        self.assertEqual(report.net_savings_paise, 4_400_000)

        # Savings vs Goal: 44,000 - 30,000 = +14,000
        self.assertEqual(report.savings_vs_goal_paise, 1_400_000)

    def test_mid_month_signup_claims_partial_coverage(self):
        """A mid-month signup does not claim complete monthly coverage."""
        report = calculate_monthly_report(self.user, 2026, 9)
        self.assertTrue(report.is_partial)
        self.assertIn("account created on 15 Sep 2026", report.coverage_note)

    def test_month_year_boundary_handling(self):
        """Handling of Dec-Jan year transition."""
        report = calculate_monthly_report(self.user, 2027, 1)
        self.assertEqual(report.preceding_year, 2026)
        self.assertEqual(report.preceding_month, 12)

    def test_recurring_detection_and_confirmation(self):
        """Recurring payment detection suggests repeated merchants."""
        # Swiggy debited twice
        Transaction.objects.create(
            account=self.account,
            amount_paise=350_00,
            direction=Transaction.Direction.DEBIT,
            status=Transaction.Status.POSTED,
            occurred_at=at(2026, 10, 1),
            description="Swiggy",
        )
        Transaction.objects.create(
            account=self.account,
            amount_paise=450_00,
            direction=Transaction.Direction.DEBIT,
            status=Transaction.Status.POSTED,
            occurred_at=at(2026, 10, 15),
            description="Swiggy",
        )

        report = calculate_monthly_report(self.user, 2026, 10)
        self.assertTrue(any(c.merchant_name == "Swiggy" for c in report.recurring_candidates))

        # Confirm recurring payment
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("reports:recurring_confirm"),
            {"merchant": "Swiggy"},
        )
        self.assertRedirects(response, reverse("reports:overview"))

        rule = RecurringPaymentRule.objects.get(owner=self.user, merchant_name="Swiggy")
        self.assertEqual(rule.status, RecurringPaymentRule.Status.CONFIRMED)

        # After confirmation, it should not appear in candidates
        report2 = calculate_monthly_report(self.user, 2026, 10)
        self.assertFalse(any(c.merchant_name == "Swiggy" for c in report2.recurring_candidates))

    def test_csv_export(self):
        Transaction.objects.create(
            account=self.account,
            amount_paise=123_45,
            direction=Transaction.Direction.DEBIT,
            category=self.cat_food,
            status=Transaction.Status.POSTED,
            occurred_at=at(2026, 10, 5, 14, 30),
            description="Lunch",
            reference="UPI123456",
        )

        csv_str = export_transactions_csv(self.user, 2026, 10)
        reader = csv.reader(io.StringIO(csv_str))
        rows = list(reader)
        self.assertEqual(rows[0][0], "Date")
        self.assertIn("123.45", rows[1][5])
        self.assertEqual(rows[1][6], "Food & dining")
        self.assertEqual(rows[1][7], "Lunch")
        self.assertEqual(rows[1][8], "UPI123456")

    def test_cross_user_isolation(self):
        other = User.objects.create_user(username="other", password="password")
        self.client.force_login(other)

        response = self.client.get(reverse("reports:api_data"))
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["income"], 0)
        self.assertEqual(data["expenses"], 0)

    def test_management_command_idempotency(self):
        """Phase 8 check: retrying monthly report command does not create duplicate snapshots."""
        from django.core.management import call_command
        from reports.models import MonthlyReportSnapshot

        call_command("generate_monthly_reports", year=2026, month=10)
        self.assertEqual(
            MonthlyReportSnapshot.objects.filter(owner=self.user, year=2026, month=10).count(), 1
        )

        # Calling again must update and NOT create a second snapshot
        call_command("generate_monthly_reports", year=2026, month=10)
        self.assertEqual(
            MonthlyReportSnapshot.objects.filter(owner=self.user, year=2026, month=10).count(), 1
        )
