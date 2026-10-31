from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

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


class DashboardTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="asha", password="test-pass-123")
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

    def test_dashboard_shows_accounts_and_goal(self):
        response = self.client.get("/")

        self.assertContains(response, "Kotak Savings")
        self.assertContains(response, "Bank of Baroda")
        self.assertContains(response, "₹24,500.00")
        self.assertContains(response, "Emergency fund")
        self.assertContains(response, "Needs review")


class DashboardStateTests(TestCase):
    """The ?state= preview only exists while DEBUG is on."""

    def setUp(self):
        self.user = User.objects.create_user(username="asha", password="test-pass-123")
        self.client.force_login(self.user)

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
        self.user = User.objects.create_user(username="asha", password="test-pass-123")
        self.client.force_login(self.user)

    def test_dashboard_loads_motion_assets(self):
        response = self.client.get("/")

        self.assertContains(response, "vendor/gsap/gsap.min.js")
        self.assertContains(response, "js/dashboard.js")
