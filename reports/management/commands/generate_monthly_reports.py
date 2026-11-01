"""Scheduled Django management command for idempotent monthly report generation.

Idempotency guarantee (Phase 8):
Retrying this job does not create duplicate reports. Uses update_or_create.
"""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils import timezone

from reports.models import MonthlyReportSnapshot
from reports.services import calculate_monthly_report, get_preceding_month

User = get_user_model()


class Command(BaseCommand):
    help = "Generate and archive monthly reports for all users idempotently."

    def add_arguments(self, parser):
        parser.add_argument(
            "--year",
            type=int,
            help="Target year (e.g. 2026)",
        )
        parser.add_argument(
            "--month",
            type=int,
            help="Target month (1-12)",
        )

    def handle(self, *args, **options):
        today = timezone.localdate()
        year = options.get("year")
        month = options.get("month")

        if not year or not month:
            # Default to the preceding (completed) month
            year, month = get_preceding_month(today.year, today.month)

        self.stdout.write(f"Generating reports for {year}-{month:02d}...")

        users = User.objects.filter(is_active=True)
        count = 0
        for user in users:
            report = calculate_monthly_report(user, year, month)
            # Idempotent write using update_or_create on unique (owner, year, month)
            snapshot, created = MonthlyReportSnapshot.objects.update_or_create(
                owner=user,
                year=year,
                month=month,
                defaults={
                    "income_paise": report.income_paise,
                    "expenses_paise": report.expenses_paise,
                    "savings_paise": report.net_savings_paise,
                },
            )
            action = "Created" if created else "Updated"
            self.stdout.write(f"  {action} snapshot for {user.username}: Income Rs.{snapshot.income_paise/100:.2f}, Savings Rs.{snapshot.savings_paise/100:.2f}")
            count += 1

        self.stdout.write(self.style.SUCCESS(f"Successfully processed {count} user report(s) for {year}-{month:02d}."))
