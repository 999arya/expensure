"""Webhook handler for Telegram updates (Phase 1: /start, /help, /whoami, /unlink)."""
import json
import logging
import os
import hmac
from django.conf import settings
from django.http import HttpResponse, HttpResponseBadRequest
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator

from .models import TelegramAccount, TelegramUpdate
from .client import TelegramClient, build_help_text, format_welcome, format_linked, format_already_linked, format_unlinked, format_unlink_prompt, format_unknown_command

logger = logging.getLogger(__name__)


@method_decorator(csrf_exempt, name="dispatch")
class TelegramWebhookView(View):
    """Handle incoming Telegram webhook updates."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._client: TelegramClient = None
        self._secret_token: str = None

    def _get_client(self) -> TelegramClient:
        if self._client is None:
            self._client = TelegramClient()
        return self._client

    def _get_secret_token(self) -> str:
        if self._secret_token is None:
            from django.conf import settings
            self._secret_token = getattr(settings, "TELEGRAM_WEBHOOK_SECRET", "").strip()
        return self._secret_token

    def _verify_secret(self, request) -> bool:
        secret = self._get_secret_token()
        if not secret:
            logger.warning("TELEGRAM_WEBHOOK_SECRET not set; rejecting webhook")
            return False
        provided = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        return hmac.compare_digest(provided, secret)

    def _is_private_chat(self, chat) -> bool:
        return chat.get("type") == "private"

    def post(self, request):
        # Verify secret token
        if not self._verify_secret(request):
            return HttpResponseBadRequest("Invalid secret token")

        # Parse update
        try:
            update = json.loads(request.body)
        except json.JSONDecodeError:
            return HttpResponseBadRequest("Invalid JSON")

        update_id = update.get("update_id")
        if update_id is None:
            return HttpResponseBadRequest("Missing update_id")

        # Deduplicate
        if TelegramUpdate.objects.filter(update_id=update_id).exists():
            logger.debug("Duplicate update_id: %s", update_id)
            return HttpResponse("OK")

        # Record update (status will be updated after processing)
        tg_update = TelegramUpdate.objects.create(update_id=update_id, status="received")

        try:
            self._process_update(update)
            tg_update.status = "processed"
        except Exception as e:
            logger.exception("Error processing update %s", update_id)
            tg_update.status = "error"
            tg_update.error_code = type(e).__name__
        finally:
            if "message" in update:
                chat = update["message"].get("chat", {})
                tg_update.chat_id = chat.get("id")
            tg_update.save(update_fields=["status", "error_code", "chat_id"])

        return HttpResponse("OK")

    def _process_update(self, update: dict):
        client = self._get_client()

        # Handle message
        if "message" in update:
            msg = update["message"]
            chat = msg.get("chat", {})
            if not self._is_private_chat(chat):
                return  # Silently ignore non-private chats

            chat_id = chat["id"]
            text = msg.get("text", "").strip()
            from_user = msg.get("from", {})
            telegram_user_id = from_user.get("id")

            # Find linked account
            try:
                tg_account = TelegramAccount.objects.select_related("user").get(
                    telegram_user_id=telegram_user_id, is_active=True
                )
                is_linked = True
            except TelegramAccount.DoesNotExist:
                is_linked = False
                tg_account = None

            # Handle commands
            if text.startswith("/"):
                self._handle_command(client, chat_id, text, tg_account, is_linked, telegram_user_id)
            else:
                # Non-command text - show help
                client.send_message(chat_id, format_unknown_command())

        # Handle callback queries (inline button presses)
        elif "callback_query" in update:
            cb = update["callback_query"]
            cb_id = cb["id"]
            data = cb.get("data", "")
            chat_id = cb["message"]["chat"]["id"]

            if data == "help":
                client.answer_callback_query(cb_id, text=build_help_text(client.get_me().username), show_alert=True)
            else:
                client.answer_callback_query(cb_id)

    def _handle_command(
        self,
        client: TelegramClient,
        chat_id: int,
        text: str,
        tg_account: TelegramAccount,
        is_linked: bool,
        telegram_user_id: int,
    ):
        parts = text.split()
        cmd = parts[0].lower()
        args = parts[1:] if len(parts) > 1 else []

        bot_username = client.get_me().username

        if cmd == "/start":
            if is_linked:
                client.send_message(chat_id, format_already_linked(tg_account.display_name()))
            else:
                welcome_text, keyboard = format_welcome(bot_username)
                client.send_message(chat_id, welcome_text, reply_markup=keyboard)

        elif cmd == "/help":
            client.send_message(chat_id, build_help_text(bot_username))

        elif cmd == "/whoami":
            if is_linked:
                client.send_message(chat_id, format_linked(tg_account.display_name()))
            else:
                welcome_text, keyboard = format_welcome(bot_username)
                client.send_message(chat_id, "You're not linked yet." + welcome_text, reply_markup=keyboard)

        elif cmd == "/unlink":
            if not is_linked:
                client.send_message(chat_id, "You're not linked.")
                return
            if args and args[0].lower() == "confirm":
                tg_account.delete()
                client.send_message(chat_id, format_unlinked())
            else:
                client.send_message(chat_id, format_unlink_prompt())

        else:
            client.send_message(chat_id, format_unknown_command())