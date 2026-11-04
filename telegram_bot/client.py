"""Synchronous Telegram Bot API client (minimal, urllib-based)."""
import json
import logging
import os
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger(__name__)


class BotUser:
    """Telegram Bot user info (from getMe). Accepts arbitrary extra fields."""
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


class TelegramClient:
    """Minimal synchronous client for Bot API calls."""

    def __init__(self, token: str = None):
        if token is None:
            from django.conf import settings
            token = getattr(settings, "TELEGRAM_BOT_TOKEN", "").strip()
        self.token = token or os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        if not self.token:
            raise ValueError("TELEGRAM_BOT_TOKEN not set")
        self.base_url = f"https://api.telegram.org/bot{self.token}"
        self._me: Optional[BotUser] = None

    def _request(self, method: str, payload: dict = None) -> Any:
        url = f"{self.base_url}/{method}"
        data = None
        headers = {"User-Agent": "Expensure/1.0"}
        if payload:
            data = json.dumps(payload).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                result = json.loads(resp.read())
                if not result.get("ok"):
                    raise RuntimeError(f"Telegram API error: {result.get('description')}")
                return result.get("result")
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            logger.error("Telegram API %s failed: %s %s", method, e.code, body)
            raise
        except Exception as e:
            logger.error("Telegram API %s error: %s", method, e)
            raise

    def get_me(self) -> BotUser:
        if self._me is None:
            data = self._request("getMe")
            self._me = BotUser(**data)
        return self._me

    def set_webhook(self, url: str, secret_token: str = None, allowed_updates: list = None) -> bool:
        payload = {"url": url}
        if secret_token:
            payload["secret_token"] = secret_token
        if allowed_updates:
            payload["allowed_updates"] = allowed_updates
        return self._request("setWebhook", payload)

    def delete_webhook(self, drop_pending_updates: bool = True) -> bool:
        payload = {"drop_pending_updates": drop_pending_updates}
        return self._request("deleteWebhook", payload)

    def get_webhook_info(self) -> dict:
        return self._request("getWebhookInfo")

    def send_message(
        self,
        chat_id: int,
        text: str,
        parse_mode: str = "HTML",
        disable_web_page_preview: bool = True,
        reply_markup: dict = None,
    ) -> dict:
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": disable_web_page_preview,
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup
        return self._request("sendMessage", payload)

    def answer_callback_query(self, callback_query_id: str, text: str = None, show_alert: bool = False) -> bool:
        payload = {"callback_query_id": callback_query_id}
        if text:
            payload["text"] = text
        if show_alert:
            payload["show_alert"] = show_alert
        return self._request("answerCallbackQuery", payload)


def build_start_keyboard(bot_username: str) -> dict:
    """Inline keyboard with 'Create or connect account' button opening the web login."""
    # Use the actual web login page, not a deep link back to the bot
    login_url = "https://expensure.onrender.com/accounts/telegram/login/"
    return {
        "inline_keyboard": [
            [{"text": "🔗 Create or connect account", "url": login_url}],
            [{"text": "❓ Help", "callback_data": "help"}],
        ]
    }


def build_help_text(bot_username: str) -> str:
    return (
        f"🤖 <b>Expensure Bot</b>\n\n"
        f"🔐 <b>Account</b>\n"
        f"• /start — Link your Expensure account\n"
        f"• /whoami — Show linked account\n"
        f"• /unlink — Unlink Telegram (type <code>/unlink confirm</code>)\n\n"
        f"📊 <b>Account Info</b>\n"
        f"• /balance — All account balances\n"
        f"• /today — Today's transactions\n"
        f"• /transactions [N] — Recent transactions (default 10, max 50)\n"
        f"• /plan — Monthly plan summary\n\n"
        f"❓ <b>Help</b>\n"
        f"• /help — This message\n\n"
        f"After linking, you'll receive daily briefs and can log expenses from here."
    )


def format_welcome(bot_username: str, display_name: str = None) -> tuple[str, dict]:
    """Welcome message with login button."""
    name = f" {display_name}" if display_name else ""
    text = (
        f"👋 Welcome{name} to <b>Expensure</b>!\n\n"
        f"Press the button below to create an account or connect your existing one."
    )
    keyboard = build_start_keyboard(bot_username)
    return text, keyboard


def format_linked(display_name: str) -> str:
    return f"✅ You're linked as <b>{display_name}</b>. Use /help to see available commands."


def format_already_linked(display_name: str) -> str:
    return f"ℹ️ You're already linked as <b>{display_name}</b>."


def format_unlinked() -> str:
    return "✅ Telegram account unlinked. Your Expensure data is unchanged. Use /start to link again."


def format_unlink_prompt() -> str:
    return (
        "⚠️ This will unlink your Telegram account from Expensure.\n"
        "Your financial data stays on the website.\n\n"
        "Type <code>/unlink confirm</code> to proceed."
    )


def format_unknown_command() -> str:
    return "❓ Unknown command. Use /help to see available commands."