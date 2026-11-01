from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import BankAccount
from accounts.tests.test_auth import VALID_SIGNUP
from coach.services import goal_steps
from planning.models import Goal


class JourneyTests(TestCase):
    def test_public_home_and_private_dashboard(self):
        response = self.client.get("/")
        self.assertTemplateUsed(response, "home.html")
        self.assertContains(response, reverse("accounts:signup"))
        self.assertContains(response, reverse("accounts:login"))
        self.assertRedirects(self.client.get("/dashboard/"), "/accounts/login/?next=/dashboard/")

    def test_signup_to_goal_to_guidance(self):
        response = self.client.post(reverse("accounts:signup"), VALID_SIGNUP, follow=True)
        user = get_user_model().objects.get(username="asha")
        self.assertEqual(user.first_name, "Asha")
        account = BankAccount.objects.get(owner=user)
        self.assertEqual(account.checkpoints.get().amount_paise, 250050)
        self.assertContains(response, "Set your first goal")
        response = self.client.post(reverse("planning:goal_add"), {
            "name": "Emergency fund", "target": "10000", "saved": "1000",
            "target_date": (timezone.localdate() + timedelta(days=90)).isoformat(),
        })
        goal = Goal.objects.get(owner=user)
        self.assertRedirects(response, reverse("coach:goal_plan", args=[goal.pk]))
        response = self.client.get(response.url)
        self.assertContains(response, "Your next steps")
        self.assertContains(response, "100.00 per day")

    def test_invalid_bank_details_do_not_create_user(self):
        response = self.client.post(reverse("accounts:signup"), VALID_SIGNUP | {"bank-account_suffix": "123456"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("account_suffix", response.context["account_form"].errors)
        self.assertFalse(get_user_model().objects.filter(username="asha").exists())
        self.assertFalse(BankAccount.objects.exists())

    def test_signup_rolls_back_when_account_save_fails(self):
        with patch("accounts.forms.BankAccountForm.save", side_effect=RuntimeError("failed")):
            with self.assertRaises(RuntimeError):
                self.client.post(reverse("accounts:signup"), VALID_SIGNUP)
        self.assertFalse(get_user_model().objects.filter(username="asha").exists())

    def test_guidance_is_private_and_get_never_calls_ai(self):
        owner = get_user_model().objects.create_user(username="owner")
        other = get_user_model().objects.create_user(username="other")
        goal = Goal.objects.create(owner=owner, name="Private", target_paise=10000)
        self.client.force_login(other)
        url = reverse("coach:goal_plan", args=[goal.pk])
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.post(url, {"ai_consent": "on"}).status_code, 404)
        self.client.force_login(owner)
        with patch("coach.ai.urllib.request.urlopen") as provider:
            self.assertContains(self.client.get(url), "Choose a target date")
            self.assertContains(self.client.post(url), "CALCULATED GUIDANCE")
            provider.assert_not_called()

    def test_due_and_completed_goals_do_not_divide_by_zero(self):
        owner = get_user_model().objects.create_user(username="owner")
        goal = Goal.objects.create(owner=owner, name="Goal", target_paise=10000, target_date=timezone.localdate())
        self.assertIn("today or has passed", goal_steps(goal)[0])
        goal.saved_paise = goal.target_paise
        self.assertIn("reached this goal", goal_steps(goal)[0])


class GoalAITests(TestCase):
    @patch.dict("os.environ", {"GROQ_API_KEY": "test-key"})
    def test_ai_can_only_reorder_existing_steps(self):
        import json
        from coach.ai import prioritize_goal_steps
        steps = ["Verify balance", "Review bills"]
        with patch("coach.ai.urllib.request.urlopen") as provider:
            reply = provider.return_value.__enter__.return_value
            reply.read.return_value = json.dumps({"choices": [{"finish_reason": "stop", "message": {"content": '{"order": [1, 0]}'}}]}).encode()
            self.assertEqual(prioritize_goal_steps(steps, allow_ai=True), (True, list(reversed(steps))))
            reply.read.return_value = json.dumps({"choices": [{"finish_reason": "stop", "message": {"content": '{"order": [1, 99]}'}}]}).encode()
            self.assertEqual(prioritize_goal_steps(steps, allow_ai=True), (False, steps))
            provider.side_effect = OSError("Unavailable")
            self.assertEqual(prioritize_goal_steps(steps, allow_ai=True), (False, steps))
