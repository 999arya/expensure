"""Regression coverage for Telegram login and private-chat persistence."""
import json
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from telegram_bot.auth import JWKSCache, TelegramUserInfo
from telegram_bot.models import TelegramAccount, TelegramOAuthState, TelegramUpdate


@override_settings(ALLOWED_HOSTS=["testserver"], TELEGRAM_CLIENT_ID="123",
                   TELEGRAM_CLIENT_SECRET="test-secret", TELEGRAM_WEBHOOK_SECRET="webhook-secret")
class TelegramLoginFlowTests(TestCase):
    def test_login_sends_stored_nonce_and_pkce(self):
        response = self.client.get(reverse("telegram_bot:login"))
        params = parse_qs(urlsplit(response.url).query)
        state = TelegramOAuthState.objects.get(pk=params["state"][0])
        self.assertEqual(params.get("nonce"), [state.nonce])
        self.assertEqual(params["code_challenge"], [state.pkce_challenge()])

    @patch("telegram_bot.auth.urllib.request.urlopen")
    def test_jwks_cache_fetches_keys(self, urlopen):
        urlopen.return_value.__enter__.return_value.read.return_value = b'{"keys": []}'
        self.assertIsNone(JWKSCache().get_key("missing"))
        urlopen.assert_called_once()

    @patch("telegram_bot.webhook.TelegramClient")
    @patch("telegram_bot.views.verify_id_token")
    @patch("telegram_bot.views.exchange_code_for_tokens")
    def test_login_then_balance_saves_private_chat(self, exchange, verify, bot_class):
        user = get_user_model().objects.create_user(username="linked-user")
        self.client.force_login(user)
        state = TelegramOAuthState.create("http://testserver/telegram/callback/")
        exchange.return_value = {"id_token": "test-token"}
        verify.return_value = TelegramUserInfo(12345, "linked", "Linked", "User")
        response = self.client.get(reverse("telegram_bot:callback"), {"state": state.state, "code": "test-code"})
        self.assertRedirects(response, reverse("core:dashboard"), fetch_redirect_response=False)
        account = TelegramAccount.objects.get(user=user)
        self.assertEqual(account.telegram_user_id, 12345)
        bot_class.return_value.get_me.return_value.username = "testbot"
        update = {"update_id": 501, "message": {"chat": {"id": 12345, "type": "private"},
                  "from": {"id": 12345}, "text": "/balance"}}
        response = self.client.post(reverse("telegram_bot:webhook"), json.dumps(update),
                                    content_type="application/json",
                                    headers={"X-Telegram-Bot-Api-Secret-Token": "webhook-secret"})
        self.assertEqual(response.status_code, 200)
        account.refresh_from_db()
        self.assertEqual(account.chat_id, 12345)
        self.assertEqual(TelegramUpdate.objects.get(update_id=501).status, "processed")
        self.assertIn("No bank accounts", bot_class.return_value.send_message.call_args.args[1])

    @patch("telegram_bot.webhook.TelegramClient")
    def test_group_cannot_replace_private_chat(self, bot_class):
        user = get_user_model().objects.create_user(username="private-user")
        account = TelegramAccount.objects.create(user=user, telegram_user_id=12345, chat_id=12345)
        update = {"update_id": 502, "message": {"chat": {"id": -100, "type": "group"},
                  "from": {"id": 12345}, "text": "/balance"}}
        self.client.post(reverse("telegram_bot:webhook"), json.dumps(update), content_type="application/json",
                         headers={"X-Telegram-Bot-Api-Secret-Token": "webhook-secret"})
        account.refresh_from_db()
        self.assertEqual(account.chat_id, 12345)
        bot_class.return_value.send_message.assert_not_called()
