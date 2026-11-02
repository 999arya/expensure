from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from ledger.defaults import DEFAULT_CATEGORIES, seed_default_categories
from ledger.models import Category

User = get_user_model()

VALID_SIGNUP = {
    "username": "asha",
    "email": "asha@example.com",
    "first_name": "Asha",
    "bank-bank": "kotak",
    "bank-nickname": "Salary",
    "bank-account_type": "savings",
    "bank-account_suffix": "1234",
    "bank-opening_balance": "2500.50",
    "password1": "quiet-river-42",
    "password2": "quiet-river-42",
}


class SignupTests(TestCase):
    def test_signup_page_renders(self):
        response = self.client.get(reverse("accounts:signup"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Create your account")
        self.assertContains(response, "no bank connection")

    def test_signup_creates_user_and_logs_in(self):
        response = self.client.post(reverse("accounts:signup"), VALID_SIGNUP)

        self.assertRedirects(response, "/dashboard/")
        user = User.objects.get(username="asha")
        self.assertTrue(user.check_password("quiet-river-42"))
        self.assertFalse(user.password == "quiet-river-42")
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.pk)

    def test_signup_seeds_default_categories(self):
        self.client.post(reverse("accounts:signup"), VALID_SIGNUP)

        user = User.objects.get(username="asha")
        categories = Category.objects.filter(owner=user)

        self.assertEqual(categories.count(), len(DEFAULT_CATEGORIES))
        self.assertTrue(all(c.is_default for c in categories))
        self.assertEqual(
            categories.filter(kind=Category.Kind.INCOME).count(),
            sum(1 for _, kind in DEFAULT_CATEGORIES if kind == Category.Kind.INCOME),
        )

    def test_signup_rejects_mismatched_passwords(self):
        data = VALID_SIGNUP | {"password2": "something-else-9"}

        response = self.client.post(reverse("accounts:signup"), data)

        self.assertEqual(response.status_code, 200)
        self.assertIn("password2", response.context["form"].errors)
        self.assertFalse(User.objects.filter(username="asha").exists())

    def test_signup_rejects_weak_password(self):
        data = VALID_SIGNUP | {"password1": "12345678", "password2": "12345678"}

        response = self.client.post(reverse("accounts:signup"), data)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username="asha").exists())

    def test_signup_rejects_duplicate_username(self):
        User.objects.create_user(username="asha", password="test-pass-123")

        response = self.client.post(reverse("accounts:signup"), VALID_SIGNUP)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(User.objects.filter(username="asha").count(), 1)

    def test_email_is_stored_lowercase(self):
        self.client.post(
            reverse("accounts:signup"),
            VALID_SIGNUP | {"email": "Asha@Example.COM"},
        )

        self.assertEqual(User.objects.get(username="asha").email, "asha@example.com")

    def test_logged_in_user_is_sent_to_dashboard(self):
        User.objects.create_user(username="asha", password="test-pass-123")
        self.client.login(username="asha", password="test-pass-123")

        response = self.client.get(reverse("accounts:signup"))

        self.assertRedirects(response, "/dashboard/")


class SeedingTests(TestCase):
    def test_seeding_twice_does_not_duplicate(self):
        user = User.objects.create_user(username="asha", password="test-pass-123")

        seed_default_categories(user)
        seed_default_categories(user)

        self.assertEqual(user.categories.count(), len(DEFAULT_CATEGORIES))


class LoginTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="asha", password="test-pass-123")

    def test_login_page_renders(self):
        response = self.client.get(reverse("accounts:login"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Log in")

    def test_login_with_valid_credentials(self):
        response = self.client.post(
            reverse("accounts:login"),
            {"username": "asha", "password": "test-pass-123"},
        )

        self.assertRedirects(response, "/dashboard/")
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.user.pk)

    def test_login_with_wrong_password_shows_error(self):
        response = self.client.post(
            reverse("accounts:login"),
            {"username": "asha", "password": "wrong-password"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Please enter a correct username and password")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_login_honours_safe_next(self):
        response = self.client.post(
            reverse("accounts:login") + "?next=/accounts/signup/",
            {"username": "asha", "password": "test-pass-123"},
        )

        self.assertRedirects(response, "/accounts/signup/", fetch_redirect_response=False)

    def test_login_ignores_external_next(self):
        response = self.client.post(
            reverse("accounts:login") + "?next=https://evil.example/",
            {"username": "asha", "password": "test-pass-123"},
        )

        self.assertRedirects(response, "/dashboard/", fetch_redirect_response=False)


class LogoutTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="asha", password="test-pass-123")
        self.client.force_login(self.user)

    def test_logout_requires_post(self):
        response = self.client.get(reverse("accounts:logout"))

        self.assertEqual(response.status_code, 405)
        self.assertIn("_auth_user_id", self.client.session)

    def test_logout_ends_the_session(self):
        response = self.client.post(reverse("accounts:logout"))

        self.assertRedirects(response, reverse("core:home"))
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_dashboard_is_closed_after_logout(self):
        self.client.post(reverse("accounts:logout"))

        response = self.client.get("/dashboard/")

        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response["Location"])


class HostedPasswordResetTests(TestCase):
    def test_unconfigured_email_shows_unavailable_without_sending(self):
        from django.core import mail
        with self.settings(PASSWORD_RESET_ENABLED=False):
            response = self.client.get(reverse("accounts:password_reset"))
            self.assertContains(response, "Email recovery is not available yet", status_code=503)
            response = self.client.post(reverse("accounts:password_reset"), {"email": "person@example.com"})
            self.assertEqual(response.status_code, 503)
            self.assertEqual(len(mail.outbox), 0)
