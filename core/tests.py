from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from .formatting import format_inr


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
    def test_url_reverses_to_root(self):
        self.assertEqual(reverse("core:dashboard"), "/")

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
