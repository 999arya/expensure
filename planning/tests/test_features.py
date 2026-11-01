from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from ledger.defaults import seed_default_categories
from ledger.models import Transaction
from ledger.tests.test_transactions import at, make_account, make_user
from planning.forms import GoalForm
from planning.models import AccountGoal, Bill, Goal, IncomePlan, MonthlyGoal, month_bounds
from planning.services import (
    add_to_goal,
    allowance_summary,
    primary_goal,
    settle_bill,
    settle_income,
)

User = get_user_model()


def today():
    return timezone.localdate()


def this_month(day=10):
    first, last = month_bounds()
    return first.replace(day=min(day, last.day))


def next_month():
    first, _ = month_bounds()
    return (first + timedelta(days=32)).replace(day=1)


def make_goal(owner, name="Emergency fund", target=100_000, saved=25_000, target_date=None):
    return Goal.objects.create(
        owner=owner,
        name=name,
        target_paise=target,
        saved_paise=saved,
        target_date=target_date,
    )


def make_income(owner, amount=30_000, expected_on=None, account=None, status=None):
    return IncomePlan.objects.create(
        owner=owner,
        description="Salary",
        amount_paise=amount,
        expected_on=expected_on or this_month(),
        account=account,
        status=status or IncomePlan.Status.EXPECTED,
    )


def make_bill(owner, amount=20_000, due_on=None, account=None, status=None):
    return Bill.objects.create(
        owner=owner,
        description="Electricity",
        amount_paise=amount,
        due_on=due_on or this_month(),
        account=account,
        status=status or Bill.Status.UNPAID,
    )


class GoalModelTests(TestCase):
    def setUp(self):
        self.user = make_user()

    def test_percent_and_remaining(self):
        goal = make_goal(self.user, target=100_000, saved=25_000)

        self.assertEqual(goal.percent, 25)
        self.assertEqual(goal.remaining_paise, 75_000)
        self.assertFalse(goal.is_complete)

    def test_percent_is_capped_at_one_hundred(self):
        goal = make_goal(self.user, target=100_000, saved=250_000)

        self.assertEqual(goal.percent, 100)
        self.assertEqual(goal.remaining_paise, 0)
        self.assertTrue(goal.is_complete)

    def test_zero_target_never_divides_by_zero(self):
        goal = Goal(
            owner=self.user,
            name="Odd",
            target_paise=0,
            saved_paise=0,
        )

        self.assertEqual(goal.percent, 0)

    def test_add_to_goal_adds_exact_paise(self):
        goal = make_goal(self.user, saved=25_000)

        add_to_goal(goal, 1_250)

        goal.refresh_from_db()
        self.assertEqual(goal.saved_paise, 26_250)

    def test_add_to_goal_rejects_zero(self):
        goal = make_goal(self.user)

        with self.assertRaises(ValueError):
            add_to_goal(goal, 0)

    def test_primary_goal_prefers_the_nearest_target_date(self):
        make_goal(self.user, name="Later", target_date=date(2027, 12, 1))
        nearest = make_goal(self.user, name="Sooner", target_date=date(2027, 3, 1))
        make_goal(self.user, name="No date", target_date=None)

        self.assertEqual(primary_goal(self.user), nearest)

    def test_primary_goal_is_per_user(self):
        other = make_user("ravi")
        make_goal(other, name="Ravi goal")

        self.assertIsNone(primary_goal(self.user))


class GoalFormTests(TestCase):
    def setUp(self):
        self.user = make_user()

    def test_indian_grouping_is_parsed_into_paise(self):
        form = GoalForm(
            data={
                "name": "Laptop",
                "target": "1,25,000.50",
                "saved": "25,000",
                "target_date": "2027-01-31",
            },
            owner=self.user,
        )

        self.assertTrue(form.is_valid(), form.errors)
        goal = form.save()
        self.assertEqual(goal.target_paise, 12_500_050)
        self.assertEqual(goal.saved_paise, 2_500_000)
        self.assertEqual(goal.owner, self.user)

    def test_edit_shows_exact_decimal_text(self):
        goal = make_goal(self.user, target=12_500_050, saved=2_500_000)

        form = GoalForm(instance=goal, owner=self.user)

        self.assertEqual(form.fields["target"].initial, "125000.50")
        self.assertEqual(form.fields["saved"].initial, "25000.00")

    def test_target_must_be_positive(self):
        form = GoalForm(
            data={"name": "Laptop", "target": "0", "saved": "0"},
            owner=self.user,
        )

        self.assertFalse(form.is_valid())
        self.assertIn("target", form.errors)

    def test_saved_cannot_be_negative(self):
        form = GoalForm(
            data={"name": "Laptop", "target": "1000", "saved": "-5"},
            owner=self.user,
        )

        self.assertFalse(form.is_valid())
        self.assertIn("saved", form.errors)


class GoalViewTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.client.force_login(self.user)

    def test_goal_list_requires_login(self):
        self.client.logout()

        response = self.client.get(reverse("planning:goals"))

        self.assertRedirects(
            response, f"/accounts/login/?next={reverse('planning:goals')}"
        )

    def test_create_goal(self):
        response = self.client.post(
            reverse("planning:goal_add"),
            {
                "name": "Emergency fund",
                "target": "1,00,000",
                "saved": "25,000",
                "target_date": "2027-06-30",
            },
        )

        goal = Goal.objects.get()
        self.assertRedirects(response, reverse("coach:goal_plan", args=[goal.pk]))
        self.assertEqual(goal.owner, self.user)
        self.assertEqual(goal.target_paise, 10_000_000)
        self.assertEqual(goal.saved_paise, 2_500_000)

    def test_edit_goal(self):
        goal = make_goal(self.user, name="Old name")

        response = self.client.post(
            reverse("planning:goal_edit", args=[goal.pk]),
            {
                "name": "New name",
                "target": "2,00,000",
                "saved": "50,000",
                "target_date": "",
            },
        )

        self.assertRedirects(response, reverse("planning:goals"))
        goal.refresh_from_db()
        self.assertEqual(goal.name, "New name")
        self.assertEqual(goal.target_paise, 20_000_000)

    def test_cannot_edit_someone_elses_goal(self):
        other = make_user("ravi")
        goal = make_goal(other, name="Ravi goal")

        response = self.client.get(reverse("planning:goal_edit", args=[goal.pk]))

        self.assertEqual(response.status_code, 404)

    def test_contribute_to_goal(self):
        goal = make_goal(self.user, saved=25_000)

        response = self.client.post(
            reverse("planning:goal_contribute", args=[goal.pk]),
            {"amount": "1,500.50"},
        )

        self.assertRedirects(response, reverse("planning:goals"))
        goal.refresh_from_db()
        self.assertEqual(goal.saved_paise, 175_050)

    def test_contribute_rejects_zero(self):
        goal = make_goal(self.user, saved=25_000)

        response = self.client.post(
            reverse("planning:goal_contribute", args=[goal.pk]),
            {"amount": "0"},
        )

        self.assertRedirects(response, reverse("planning:goals"))
        goal.refresh_from_db()
        self.assertEqual(goal.saved_paise, 25_000)

    def test_contribute_is_post_only(self):
        goal = make_goal(self.user)

        response = self.client.get(
            reverse("planning:goal_contribute", args=[goal.pk])
        )

        self.assertEqual(response.status_code, 405)

    def test_cannot_contribute_to_someone_elses_goal(self):
        other = make_user("ravi")
        goal = make_goal(other)

        response = self.client.post(
            reverse("planning:goal_contribute", args=[goal.pk]),
            {"amount": "100"},
        )

        self.assertEqual(response.status_code, 404)

    def test_list_shows_open_and_complete_goals(self):
        make_goal(self.user, name="Open goal", saved=10_000)
        make_goal(self.user, name="Done goal", target=10_000, saved=10_000)

        response = self.client.get(reverse("planning:goals"))

        self.assertContains(response, "Open goal")
        self.assertContains(response, "Done goal")
        self.assertContains(response, "Complete")


class AllowanceTests(TestCase):
    def setUp(self):
        self.user = make_user()
        seed_default_categories(self.user)
        self.account = make_account(
            self.user, nickname="Kotak salary", suffix="4821", confirmed_at=at(2026, 10, 1)
        )

    def test_formula_is_balances_plus_income_minus_bills(self):
        from ledger.tests.test_transactions import add_txn

        add_txn(
            self.account,
            at(2026, 10, 5),
            amount=50_000,
            direction=Transaction.Direction.CREDIT,
        )
        make_income(self.user, amount=30_000, expected_on=date(2026, 10, 20))
        make_bill(self.user, amount=20_000, due_on=date(2026, 10, 25))

        summary = allowance_summary(self.user, today=date(2026, 10, 15))

        self.assertEqual(summary.balance_paise, 1_050_000)
        self.assertEqual(summary.income_paise, 30_000)
        self.assertEqual(summary.bills_paise, 20_000)
        self.assertEqual(summary.allowance_paise, 1_060_000)
        self.assertTrue(summary.has_plan)

    def test_items_after_this_month_are_left_out(self):
        make_income(self.user, amount=90_000, expected_on=next_month())
        make_bill(self.user, amount=80_000, due_on=next_month())

        summary = allowance_summary(self.user, today=today())

        self.assertEqual(summary.income_paise, 0)
        self.assertEqual(summary.bills_paise, 0)

    def test_overdue_items_are_still_counted(self):
        make_income(self.user, amount=10_000, expected_on=date(2026, 9, 1))
        make_bill(self.user, amount=5_000, due_on=date(2026, 9, 1))

        summary = allowance_summary(self.user, today=date(2026, 10, 15))

        self.assertEqual(summary.income_paise, 10_000)
        self.assertEqual(summary.bills_paise, 5_000)

    def test_settled_items_are_left_out(self):
        make_income(self.user, amount=10_000, status=IncomePlan.Status.RECEIVED)
        make_bill(self.user, amount=5_000, status=Bill.Status.PAID)

        summary = allowance_summary(self.user, today=today())

        self.assertEqual(summary.income_paise, 0)
        self.assertEqual(summary.bills_paise, 0)

    def test_per_day_is_shared_over_the_days_left(self):
        summary = allowance_summary(self.user, today=date(2026, 10, 15))

        self.assertEqual(summary.days_left, 17)
        self.assertEqual(summary.per_day_paise, summary.allowance_paise // 17)

    def test_negative_allowance_is_flagged(self):
        make_bill(self.user, amount=2_000_000, due_on=date(2026, 10, 20))

        summary = allowance_summary(self.user, today=date(2026, 10, 15))

        self.assertTrue(summary.is_negative)

    def test_no_accounts_means_zero_balance(self):
        self.account.delete()

        summary = allowance_summary(self.user)

        self.assertEqual(summary.balance_paise, 0)

    def test_section_4_financial_model_example(self):
        """Matches Section 4 example:
        B0=₹5,000, C=₹25,000 after ₹20,000 income, R=₹0, U=₹4,000, G=₹5,000,
        F=T=₹5,000. P=₹10,000 and discretionary allowance=₹11,000.
        Paying ₹1,000 of bills leaves allowance at ₹11,000.
        """
        # Configure account starting on Oct 1 with B0=5000 (500_00 paise)
        from accounts.models import BalanceCheckpoint

        self.account.checkpoints.all().delete()
        BalanceCheckpoint.objects.create(
            account=self.account,
            amount_paise=500_000,  # 5,000 Rs
            effective_at=at(2026, 10, 1, 0, 0),
        )
        # Income of 20,000 Rs posted during October
        Transaction.objects.create(
            account=self.account,
            amount_paise=2_000_000,  # 20,000 Rs
            direction=Transaction.Direction.CREDIT,
            status=Transaction.Status.POSTED,
            occurred_at=at(2026, 10, 5, 12, 0),
        )
        # Account floor F = 5000, target T = 5000
        AccountGoal.objects.create(
            account=self.account,
            minimum_floor_paise=500_000,
            target_paise=500_000,
        )
        # Desired savings G = 5000
        MonthlyGoal.objects.create(
            owner=self.user,
            month=date(2026, 10, 1),
            savings_paise=500_000,
        )
        # Unpaid bills U = 4000 (split into 3000 and 1000)
        make_bill(self.user, amount=300_000, due_on=date(2026, 10, 25))
        bill2 = make_bill(self.user, amount=100_000, due_on=date(2026, 10, 20))

        summary = allowance_summary(self.user, today=date(2026, 10, 15))

        self.assertEqual(summary.balance_paise, 2_500_000)  # C = 25,000 Rs
        self.assertEqual(summary.income_paise, 0)          # R = 0
        self.assertEqual(summary.bills_paise, 400_000)     # U = 4,000 Rs
        self.assertEqual(summary.b0_paise, 500_000)        # B0 = 5,000 Rs
        self.assertEqual(summary.savings_goal_paise, 500_000) # G = 5,000 Rs
        self.assertEqual(summary.floors_paise, 500_000)    # F = 5,000 Rs
        self.assertEqual(summary.account_targets_paise, 500_000) # T = 5,000 Rs
        self.assertEqual(summary.protected_paise, 1_000_000) # P = max(5000, 5000, 5000+5000) = 10,000 Rs
        self.assertEqual(summary.allowance_paise, 1_100_000) # 25,000 + 0 - 4000 - 10000 = 11,000 Rs
        self.assertEqual(summary.cash_backed_paise, 1_100_000)

        # Paying 1,000 Rs of those bills reduces C and U by 1,000 Rs, allowance stays 11,000 Rs
        settle_bill(bill2, self.user, day=date(2026, 10, 16))

        summary2 = allowance_summary(self.user, today=date(2026, 10, 16))
        self.assertEqual(summary2.balance_paise, 2_400_000)
        self.assertEqual(summary2.bills_paise, 300_000)
        self.assertEqual(summary2.allowance_paise, 1_100_000)


class SettleBillTests(TestCase):
    def setUp(self):
        self.user = make_user()
        seed_default_categories(self.user)
        self.account = make_account(
            self.user, nickname="Kotak salary", suffix="4821", confirmed_at=at(2026, 10, 1)
        )

    def test_paying_a_bill_lowers_balance_and_unpaid_bills_equally(self):
        """Decision 004: the allowance does not jump when a bill is paid."""
        bill = make_bill(self.user, amount=1_000_00, due_on=date(2026, 10, 20))
        before = allowance_summary(self.user, today=date(2026, 10, 15))

        txn = settle_bill(bill, self.user, day=date(2026, 10, 15))

        bill.refresh_from_db()
        after = allowance_summary(self.user, today=date(2026, 10, 15))

        self.assertEqual(bill.status, Bill.Status.PAID)
        self.assertEqual(bill.settled_transaction, txn)
        self.assertEqual(txn.direction, Transaction.Direction.DEBIT)
        self.assertEqual(txn.amount_paise, 1_000_00)
        self.assertEqual(after.balance_paise, before.balance_paise - 1_000_00)
        self.assertEqual(after.bills_paise, before.bills_paise - 1_000_00)
        self.assertEqual(after.allowance_paise, before.allowance_paise)

    def test_bill_uses_its_account_and_expense_category(self):
        bob = make_account(self.user, nickname="BoB", suffix="1111")
        bill = make_bill(self.user, amount=500_00, account=bob)

        txn = settle_bill(bill, self.user, day=date(2026, 10, 15))

        self.assertEqual(txn.account, bob)
        self.assertEqual(txn.category.kind, "expense")
        self.assertEqual(txn.description, bill.description)

    def test_a_backdated_bill_waits_for_review(self):
        bill = make_bill(self.user, amount=500_00)

        txn = settle_bill(bill, self.user, day=date(2026, 9, 20))

        self.assertEqual(txn.status, Transaction.Status.NEEDS_REVIEW)

    def test_another_users_bill_is_rejected(self):
        other = make_user("ravi")
        bill = make_bill(other)

        with self.assertRaises(ValueError):
            settle_bill(bill, self.user)


class SettleIncomeTests(TestCase):
    def setUp(self):
        self.user = make_user()
        seed_default_categories(self.user)
        self.account = make_account(
            self.user, nickname="Kotak salary", suffix="4821", confirmed_at=at(2026, 10, 1)
        )

    def test_receiving_income_lowers_expected_income_by_the_same_amount(self):
        item = make_income(self.user, amount=30_000, expected_on=date(2026, 10, 20))
        before = allowance_summary(self.user, today=date(2026, 10, 15))

        txn = settle_income(item, self.user, day=date(2026, 10, 15))

        item.refresh_from_db()
        after = allowance_summary(self.user, today=date(2026, 10, 15))

        self.assertEqual(item.status, IncomePlan.Status.RECEIVED)
        self.assertEqual(txn.direction, Transaction.Direction.CREDIT)
        self.assertEqual(after.balance_paise, before.balance_paise + 30_000)
        self.assertEqual(after.income_paise, 0)
        self.assertEqual(after.allowance_paise, before.allowance_paise)

    def test_income_uses_an_income_category(self):
        item = make_income(self.user)

        txn = settle_income(item, self.user, day=date(2026, 10, 15))

        self.assertEqual(txn.category.kind, "income")

    def test_another_users_income_is_rejected(self):
        other = make_user("ravi")
        item = make_income(other)

        with self.assertRaises(ValueError):
            settle_income(item, self.user)


class PlanViewTests(TestCase):
    def setUp(self):
        self.user = make_user()
        seed_default_categories(self.user)
        self.account = make_account(self.user, nickname="Kotak salary", suffix="4821")
        self.client.force_login(self.user)

    def test_plan_requires_login(self):
        self.client.logout()

        response = self.client.get(reverse("planning:plan"))

        self.assertRedirects(
            response, f"/accounts/login/?next={reverse('planning:plan')}"
        )

    def test_plan_shows_allowance_and_items(self):
        make_income(self.user, amount=30_000, expected_on=this_month())
        make_bill(self.user, amount=20_000, due_on=this_month())
        make_goal(self.user, name="Emergency fund")

        response = self.client.get(reverse("planning:plan"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "SPENDING ALLOWANCE")
        self.assertContains(response, "Salary")
        self.assertContains(response, "Electricity")
        self.assertContains(response, "Emergency fund")

    def test_plan_works_without_an_account(self):
        self.account.delete()

        response = self.client.get(reverse("planning:plan"))

        self.assertEqual(response.status_code, 200)

    def test_add_income_needs_an_account(self):
        self.account.delete()

        response = self.client.get(reverse("planning:income_add"))

        self.assertRedirects(response, reverse("accounts:add"))

    def test_add_bill_needs_an_account(self):
        self.account.delete()

        response = self.client.get(reverse("planning:bill_add"))

        self.assertRedirects(response, reverse("accounts:add"))

    def test_create_income(self):
        response = self.client.post(
            reverse("planning:income_add"),
            {
                "description": "Client payment",
                "amount": "40,000",
                "expected_on": this_month().isoformat(),
                "account": self.account.pk,
            },
        )

        self.assertRedirects(response, reverse("planning:plan"))

    def test_monthly_goal_create_and_update(self):
        response = self.client.post(
            reverse("planning:monthly_goal"),
            {"savings": "15,000.50"},
        )
        self.assertRedirects(response, reverse("planning:plan"))
        m_goal = MonthlyGoal.objects.get(owner=self.user)
        self.assertEqual(m_goal.savings_paise, 1_500_050)

    def test_account_goal_create_and_update(self):
        response = self.client.post(
            reverse("planning:account_goal", args=[self.account.pk]),
            {"minimum_floor": "10,000", "target": "25,000"},
        )
        self.assertRedirects(response, reverse("planning:plan"))
        ag = AccountGoal.objects.get(account=self.account)
        self.assertEqual(ag.minimum_floor_paise, 1_000_000)
        self.assertEqual(ag.target_paise, 2_500_000)

    def test_account_goal_cannot_target_someone_elses_account(self):
        other = make_user("otheruser")
        other_acc = make_account(other, nickname="Other Bank", suffix="9999")
        response = self.client.get(
            reverse("planning:account_goal", args=[other_acc.pk])
        )
        self.assertEqual(response.status_code, 404)
        item = IncomePlan.objects.get()
        self.assertEqual(item.owner, self.user)
        self.assertEqual(item.amount_paise, 4_000_000)

    def test_create_bill(self):
        response = self.client.post(
            reverse("planning:bill_add"),
            {
                "description": "Rent",
                "amount": "12,000",
                "due_on": this_month().isoformat(),
                "account": self.account.pk,
            },
        )

        self.assertRedirects(response, reverse("planning:plan"))
        item = Bill.objects.get()
        self.assertEqual(item.owner, self.user)
        self.assertEqual(item.amount_paise, 1_200_000)

    def test_settle_bill_from_the_plan_page(self):
        bill = make_bill(self.user, amount=20_000, due_on=today())

        response = self.client.post(
            reverse("planning:bill_settle", args=[bill.pk])
        )

        self.assertRedirects(response, reverse("planning:plan"))
        bill.refresh_from_db()
        self.assertEqual(bill.status, Bill.Status.PAID)
        self.assertEqual(Transaction.objects.filter(account=self.account).count(), 1)

    def test_settle_income_from_the_plan_page(self):
        item = make_income(self.user, amount=30_000, expected_on=today())

        response = self.client.post(
            reverse("planning:income_settle", args=[item.pk])
        )

        self.assertRedirects(response, reverse("planning:plan"))
        item.refresh_from_db()
        self.assertEqual(item.status, IncomePlan.Status.RECEIVED)

    def test_monthly_goal_create_and_update(self):
        response = self.client.post(
            reverse("planning:monthly_goal"),
            {"savings": "15,000.50"},
        )
        self.assertRedirects(response, reverse("planning:plan"))
        m_goal = MonthlyGoal.objects.get(owner=self.user)
        self.assertEqual(m_goal.savings_paise, 1_500_050)

    def test_account_goal_create_and_update(self):
        response = self.client.post(
            reverse("planning:account_goal", args=[self.account.pk]),
            {"minimum_floor": "10,000", "target": "25,000"},
        )
        self.assertRedirects(response, reverse("planning:plan"))
        ag = AccountGoal.objects.get(account=self.account)
        self.assertEqual(ag.minimum_floor_paise, 1_000_000)
        self.assertEqual(ag.target_paise, 2_500_000)

    def test_account_goal_cannot_target_someone_elses_account(self):
        other = make_user("otheruser")
        other_acc = make_account(other, nickname="Other Bank", suffix="9999")
        response = self.client.get(
            reverse("planning:account_goal", args=[other_acc.pk])
        )
        self.assertEqual(response.status_code, 404)

    def test_settle_is_post_only(self):
        bill = make_bill(self.user)

        response = self.client.get(reverse("planning:bill_settle", args=[bill.pk]))

        self.assertEqual(response.status_code, 405)

    def test_cannot_settle_someone_elses_bill(self):
        other = make_user("ravi")
        bill = make_bill(other)

        response = self.client.post(reverse("planning:bill_settle", args=[bill.pk]))

        self.assertEqual(response.status_code, 404)

    def test_cannot_settle_a_paid_bill_twice(self):
        bill = make_bill(self.user, status=Bill.Status.PAID)

        response = self.client.post(reverse("planning:bill_settle", args=[bill.pk]))

        self.assertEqual(response.status_code, 404)

    def test_cannot_settle_someone_elses_income(self):
        other = make_user("ravi")
        item = make_income(other)

        response = self.client.post(reverse("planning:income_settle", args=[item.pk]))

        self.assertEqual(response.status_code, 404)


class DashboardPlanningTests(TestCase):
    def setUp(self):
        self.user = make_user()
        seed_default_categories(self.user)
        self.account = make_account(self.user, nickname="Kotak salary", suffix="4821")
        self.client.force_login(self.user)

    def test_dashboard_shows_the_allowance_and_goal(self):
        make_goal(self.user, name="Emergency fund", target=100_000, saved=25_000)
        make_income(self.user, amount=30_000, expected_on=this_month())

        response = self.client.get(reverse("core:dashboard"))

        self.assertContains(response, "Spending allowance")
        self.assertContains(response, "Emergency fund")
        self.assertContains(response, "25%")
        self.assertContains(response, "Open plan")

    @override_settings(DEBUG=True)
    def test_empty_preview_hides_planning(self):
        make_goal(self.user, name="Emergency fund")

        response = self.client.get("/?state=empty")

        self.assertNotContains(response, "Emergency fund")
        self.assertNotContains(response, "Spending allowance")
