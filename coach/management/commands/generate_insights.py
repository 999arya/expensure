from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from coach.services import generate_daily_insight

class Command(BaseCommand):
    help = "Generate versioned deterministic insights; no paid API calls."
    def handle(self, **options):
        count = 0
        for user in get_user_model().objects.filter(is_active=True).iterator():
            generate_daily_insight(user)
            count += 1
        self.stdout.write(f"Processed {count} users.")
