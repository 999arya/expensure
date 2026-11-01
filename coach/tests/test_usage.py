from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from coach.models import AIUsage
from coach.services import reserve_ai_call


@override_settings(AI_DAILY_LIMIT=20)
class DailyUsageTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="usage-test")

    def test_twentieth_call_allowed_twenty_first_blocked(self):
        usage = AIUsage.objects.create(owner=self.user, day=timezone.localdate(), calls=19)
        self.assertTrue(reserve_ai_call(self.user))
        self.assertFalse(reserve_ai_call(self.user))
        usage.refresh_from_db()
        self.assertEqual(usage.calls, 20)

    def test_previous_day_usage_does_not_block_today(self):
        AIUsage.objects.create(owner=self.user, day=timezone.localdate() - timedelta(days=1), calls=20)
        self.assertTrue(reserve_ai_call(self.user))
        self.assertEqual(AIUsage.objects.get(owner=self.user, day=timezone.localdate()).calls, 1)
