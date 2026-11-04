"""Management command to show Telegram bot status."""
from django.conf import settings
from django.core.management.base import BaseCommand

from telegram_bot.client import TelegramClient
from telegram_bot.models import TelegramAccount, TelegramUpdate

import os


class Command(BaseCommand):
    help = "Show Telegram bot status and statistics."

    def handle(self, *args, **options):
        token = getattr(settings, "TELEGRAM_BOT_TOKEN", None) or os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        if not token:
            self.stderr.write("TELEGRAM_BOT_TOKEN not configured.")
            return

        client = TelegramClient(token)

        # Bot info
        me = client.get_me()
        self.stdout.write(self.style.SUCCESS(f"Bot: @{me.username} ({me.first_name})"))
        self.stdout.write(f"  ID: {me.id}")
        self.stdout.write(f"  Can join groups: {me.can_join_groups}")
        self.stdout.write(f"  Can read all group messages: {me.can_read_all_group_messages}")

        # Webhook info
        info = client.get_webhook_info()
        self.stdout.write(self.style.SUCCESS("Webhook:"))
        for k, v in info.items():
            self.stdout.write(f"  {k}: {v}")

        # Database stats
        total_accounts = TelegramAccount.objects.count()
        active_accounts = TelegramAccount.objects.filter(is_active=True).count()
        total_updates = TelegramUpdate.objects.count()
        error_updates = TelegramUpdate.objects.exclude(status="processed").count()

        self.stdout.write(self.style.SUCCESS("Database:"))
        self.stdout.write(f"  Linked accounts: {total_accounts} ({active_accounts} active)")
        self.stdout.write(f"  Processed updates: {total_updates} ({error_updates} errors)")