"""Management command to set up Telegram webhook and verify bot."""
import logging
from django.conf import settings
from django.core.management.base import BaseCommand

from telegram_bot.client import TelegramClient

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Set up Telegram webhook and verify bot configuration."

    def add_arguments(self, parser):
        parser.add_argument(
            "--delete",
            action="store_true",
            help="Delete the webhook instead of setting it.",
        )
        parser.add_argument(
            "--info",
            action="store_true",
            help="Show webhook info without changing it.",
        )

    def handle(self, *args, **options):
        token = getattr(settings, "TELEGRAM_BOT_TOKEN", None) or settings.TELEGRAM_BOT_TOKEN if hasattr(settings, "TELEGRAM_BOT_TOKEN") else None
        if not token:
            token = __import__("os").environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        if not token:
            self.stderr.write("TELEGRAM_BOT_TOKEN not configured.")
            return

        client = TelegramClient(token)

        if options["info"]:
            info = client.get_webhook_info()
            self.stdout.write(self.style.SUCCESS("Webhook info:"))
            for k, v in info.items():
                self.stdout.write(f"  {k}: {v}")
            return

        if options["delete"]:
            result = client.delete_webhook()
            self.stdout.write(self.style.SUCCESS(f"Webhook deleted: {result}"))
            return

        # Set webhook
        webhook_url = getattr(settings, "TELEGRAM_WEBHOOK_URL", None) or __import__("os").environ.get("TELEGRAM_WEBHOOK_URL", "").strip()
        secret_token = getattr(settings, "TELEGRAM_WEBHOOK_SECRET", None) or __import__("os").environ.get("TELEGRAM_WEBHOOK_SECRET", "").strip()

        if not webhook_url:
            self.stderr.write("TELEGRAM_WEBHOOK_URL not configured.")
            return

        result = client.set_webhook(webhook_url, secret_token=secret_token if secret_token else None)
        self.stdout.write(self.style.SUCCESS(f"Webhook set: {result}"))

        # Verify
        me = client.get_me()
        self.stdout.write(f"Bot: @{me.username} ({me.first_name})")