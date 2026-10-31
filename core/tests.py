from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import BalanceCheckpoint, BankAccount
from ledger.models import Transaction

from .formatting import format_inr

User = get_user_model()


class FormatInrTests(SimpleTestCase):
    def test_zero(self):
        self.assertEqual(format_inr(0), "₹0.00")

    def test_paise_only(self):
        self.assertEqual(format_inr(5), "₹0.05")

    def test_thousands_grouping(self):
        self.assertEqual(format_inr(2_450_000), "₹24,500.00")

    def test_lakh_grouping(self):
        self.assertEqual(format_inr(123_456_789), "₹12,34,567.89")

    def test_negative(self):
        self.assertEqual(format_inr(-500_000), "-₹5,000.00")

    def test_rejects_float(self):
        with self.assertRaises(TypeError):
            format_inr(100.5)


def make_user(username="asha"):
    return User.objects.create_user(username=username, password="test-pass-123")


def make_account(owner, nickname="Kotak Savings", suffix="4821", balance=2_450_000):
    account = BankAccount.objects.create(
        owner=owner,
        bank=BankAccount.Bank.KOTAK,
        nickname=nickname,
        account_suffix=suffix,
    )
    BalanceCheckpoint.objects.create(
        account=account,
        amount_paise=balance,
        effective_at=timezone.now(),
    )
    return account


class DashboardTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.client.force_login(self.user)

    def test_url_reverses_to_root(self):
        self.assertEqual(reverse("core:dashboard"), "/")

    def test_dashboard_requires_login(self):
        self.client.logout()

        response = self.client.get("/")

        self.assertRedirects(response, "/accounts/login/?next=/")

    def test_dashboard_renders(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "dashboard.html")
        self.assertTemplateUsed(response, "base.html")

    def test_dashboard_shows_your_accounts_and_estimate(self):
        make_account(self.user)

        response = self.client.get("/")

        self.assertContains(response, "Kotak Savings")
        self.assertContains(response, "Kotak Mahindra Bank")
        self.assertContains(response, "4821")
        self.assertContains(response, "₹24,500.00")

    def test_dashboard_never_shows_another_users_account(self):
        other = make_user("ravi")
        make_account(other, nickname="Ravi private account", suffix="9999")

        response = self.client.get("/")

        self.assertNotContains(response, "Ravi private account")
        self.assertContains(response, "No accounts yet.")

    def test_empty_dashboard_invites_adding_an_account(self):
        response = self.client.get("/")

        self.assertContains(response, "No accounts yet.")
        self.assertContains(response, "No transactions recorded yet.")
        self.assertContains(response, reverse("accounts:add"))

    def test_review_count_only_counts_your_transactions(self):
        account = make_account(self.user)
        other_account = make_account(make_user("ravi"), suffix="9999")
        Transaction.objects.create(
            account=account,
            occurred_at=timezone.now(),
            amount_paise=100,
            direction=Transaction.Direction.DEBIT,
            description="Mine",
            status=Transaction.Status.NEEDS_REVIEW,
        )
        Transaction.objects.create(
            account=other_account,
            occurred_at=timezone.now(),
            amount_paise=100,
            direction=Transaction.Direction.DEBIT,
            description="Theirs",
            status=Transaction.Status.NEEDS_REVIEW,
        )

        response = self.client.get("/")

        self.assertContains(response, "1 transaction needs review")
        self.assertNotContains(response, "2 transactions need review")


class DashboardStateTests(TestCase):
    """The ?state= preview only exists while DEBUG is on."""

    def setUp(self):
        self.user = make_user()
        self.client.force_login(self.user)
        make_account(self.user)

    @override_settings(DEBUG=True)
    def test_ready_state_shows_data_and_switcher(self):
        response = self.client.get("/?state=ready")

        self.assertContains(response, "Kotak Savings")
        self.assertContains(response, "Preview state:")

    @override_settings(DEBUG=True)
    def test_empty_state(self):
        response = self.client.get("/?state=empty")

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Kotak Savings")
        self.assertContains(response, "No accounts yet.")
        self.assertContains(response, "No transactions recorded yet.")
        self.assertNotContains(response, "Savings goal")

    @override_settings(DEBUG=True)
    def test_loading_state(self):
        response = self.client.get("/?state=loading")

        self.assertContains(response, 'aria-busy="true"')
        self.assertContains(response, "Loading your dashboard")
        self.assertNotContains(response, "Kotak Savings")

    @override_settings(DEBUG=True)
    def test_error_state(self):
        response = self.client.get("/?state=error")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "load your dashboard")
        self.assertContains(response, "Try again")
        self.assertNotContains(response, "Kotak Savings")

    @override_settings(DEBUG=True)
    def test_unknown_state_falls_back_to_ready(self):
        response = self.client.get("/?state=nonsense")

        self.assertContains(response, "Kotak Savings")

    @override_settings(DEBUG=False)
    def test_states_are_ignored_when_debug_is_off(self):
        response = self.client.get("/?state=error")

        self.assertContains(response, "Kotak Savings")
        self.assertNotContains(response, "Preview state:")


class MotionAssetTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.client.force_login(self.user)

    def test_dashboard_loads_motion_assets(self):
        response = self.client.get("/")

        self.assertContains(response, "vendor/gsap/gsap.min.js")
        self.assertContains(response, "js/dashboard.js")


class NavigationTests(TestCase):
    """The bottom bar marks exactly the section you are in."""

    def setUp(self):
        self.user = make_user()
        self.client.force_login(self.user)

    def current_links(self, url):
        response = self.client.get(url)
        return [
            link
            for link in response.content.decode().split("<a ")[1:]
            if 'aria-current="page"' in link.split(">")[0]
        ]

    def test_overview_is_current_on_the_dashboard(self):
        links = self.current_links("/")

        self.assertEqual(len(links), 1)
        self.assertIn('href="/"', links[0])

    def test_activity_is_current_on_the_transaction_list(self):
        links = self.current_links("/transactions/")

        self.assertEqual(len(links), 1)
        self.assertIn('href="/transactions/"', links[0])

    def test_activity_is_current_while_adding_a_transaction(self):
        make_account(self.user)

        links = self.current_links("/transactions/add/")

        self.assertEqual(len(links), 1)
        self.assertIn('href="/transactions/"', links[0])

    def test_add_account_is_current_on_account_creation(self):
        links = self.current_links("/accounts/add/")

        self.assertEqual(len(links), 1)
        self.assertIn('href="/accounts/add/"', links[0])
