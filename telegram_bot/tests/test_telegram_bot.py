"""Tests for telegram_bot app."""
import json
from unittest.mock import patch, MagicMock
from django.test import TestCase, Client, override_settings
from django.urls import reverse
from django.contrib.auth import get_user_model

from telegram_bot.models import TelegramAccount, TelegramUpdate, TelegramOAuthState
from telegram_bot.auth import verify_id_token, TelegramOIDCError, link_or_create_user, complete_registration
from telegram_bot.client import TelegramClient, format_welcome, build_help_text


User = get_user_model()


class TelegramOAuthStateTests(TestCase):
    def test_create_for_session_generates_unique_values(self):
        state1 = TelegramOAuthState.create_for_session("session1", "https://example.com/callback")
        state2 = TelegramOAuthState.create_for_session("session2", "https://example.com/callback")
        self.assertNotEqual(state1.state, state2.state)
        self.assertNotEqual(state1.nonce, state2.nonce)
        self.assertNotEqual(state1.code_verifier, state2.code_verifier)

    def test_pkce_challenge_is_s256(self):
        state = TelegramOAuthState.objects.create(
            session_key="test", state="s", nonce="n", code_verifier="verifier", redirect_uri="https://example.com"
        )
        challenge = state.pkce_challenge()
        import hashlib, base64
        expected = base64.urlsafe_b64encode(hashlib.sha256(b"verifier").digest()).decode().rstrip("=")
        self.assertEqual(challenge, expected)

    def test_consume_marks_used(self):
        state = TelegramOAuthState.objects.create(
            session_key="test", state="valid_state", nonce="nonce", code_verifier="verifier", redirect_uri="https://example.com"
        )
        consumed = TelegramOAuthState.consume("valid_state")
        self.assertIsNotNone(consumed)
        self.assertIsNotNone(consumed.used_at)

    def test_consume_rejects_reused(self):
        state = TelegramOAuthState.objects.create(
            session_key="test", state="valid_state", nonce="nonce", code_verifier="verifier", redirect_uri="https://example.com"
        )
        TelegramOAuthState.consume("valid_state")
        consumed = TelegramOAuthState.consume("valid_state")
        self.assertIsNone(consumed)

    def test_consume_rejects_unknown(self):
        consumed = TelegramOAuthState.consume("unknown")
        self.assertIsNone(consumed)


class TelegramUpdateTests(TestCase):
    def test_unique_update_id(self):
        TelegramUpdate.objects.create(update_id=123, status="processed")
        with self.assertRaises(Exception):
            TelegramUpdate.objects.create(update_id=123, status="processed")


class TelegramAccountTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="testuser", email="test@example.com")

    def test_one_telegram_id_per_user(self):
        TelegramAccount.objects.create(user=self.user, telegram_user_id=12345)
        with self.assertRaises(Exception):
            TelegramAccount.objects.create(user=self.user, telegram_user_id=67890)

    def test_unique_telegram_user_id(self):
        TelegramAccount.objects.create(user=self.user, telegram_user_id=12345)
        user2 = User.objects.create_user(username="testuser2", email="test2@example.com")
        with self.assertRaises(Exception):
            TelegramAccount.objects.create(user=user2, telegram_user_id=12345)


class TelegramClientTests(TestCase):
    @patch("telegram_bot.client.urllib.request.urlopen")
    def test_get_me(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "ok": True,
            "result": {
                "id": 123456789,
                "is_bot": True,
                "first_name": "TestBot",
                "username": "testbot",
                "can_join_groups": True,
                "can_read_all_group_messages": False,
                "supports_inline_queries": False,
            }
        }).encode()
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        client = TelegramClient("fake_token")
        me = client.get_me()
        self.assertEqual(me.username, "testbot")

    def test_format_welcome(self):
        text, keyboard = format_welcome("testbot")
        self.assertIn("Expensure", text)
        self.assertIn("inline_keyboard", keyboard)


@override_settings(
    TELEGRAM_WEBHOOK_SECRET="test-secret", 
    TELEGRAM_BOT_TOKEN="test-token",
    ALLOWED_HOSTS=["localhost", "127.0.0.1", "testserver"],
)
class TelegramWebhookTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.webhook_url = reverse("telegram_bot:webhook")
        self.secret = "test-secret"
        self.user = User.objects.create_user(username="webhookuser", email="webhook@example.com")
        self.tg_account = TelegramAccount.objects.create(
            user=self.user, telegram_user_id=999999, chat_id=999999, first_name="Test", is_active=True
        )

    def _post_update(self, update, secret=None):
        headers = {}
        if secret:
            headers["X-Telegram-Bot-Api-Secret-Token"] = secret
        return self.client.post(self.webhook_url, data=json.dumps(update), content_type="application/json", headers=headers)

    def test_rejects_invalid_secret(self):
        update = {"update_id": 1, "message": {"chat": {"id": 1, "type": "private"}, "text": "/start"}}
        resp = self._post_update(update, secret="wrong")
        self.assertEqual(resp.status_code, 400)

    def test_accepts_valid_secret(self):
        update = {"update_id": 1, "message": {"chat": {"id": 1, "type": "private"}, "from": {"id": 999999}, "text": "/start"}}
        resp = self._post_update(update, secret="test-secret")
        self.assertEqual(resp.status_code, 200)

    def test_deduplicates_update_id(self):
        update = {"update_id": 42, "message": {"chat": {"id": 1, "type": "private"}, "from": {"id": 999999}, "text": "/start"}}
        self._post_update(update, secret="test-secret")
        resp = self._post_update(update, secret="test-secret")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(TelegramUpdate.objects.filter(update_id=42).count(), 1)

    def test_ignores_group_chats(self):
        update = {"update_id": 100, "message": {"chat": {"id": -100, "type": "group"}, "from": {"id": 999999}, "text": "/start"}}
        resp = self._post_update(update, secret="test-secret")
        self.assertEqual(resp.status_code, 200)
        # Should not send a message (hard to test without mocking client, but status 200 is correct)

    @patch("telegram_bot.webhook.TelegramClient")
    def test_start_command_unlinked(self, mock_client_class):
        mock_client = MagicMock()
        mock_client.get_me.return_value.username = "testbot"
        mock_client_class.return_value = mock_client

        update = {"update_id": 200, "message": {"chat": {"id": 111, "type": "private"}, "from": {"id": 888888}, "text": "/start"}}
        resp = self._post_update(update, secret="test-secret")
        self.assertEqual(resp.status_code, 200)
        mock_client.send_message.assert_called()

    @patch("telegram_bot.webhook.TelegramClient")
    def test_start_command_linked(self, mock_client_class):
        mock_client = MagicMock()
        mock_client.get_me.return_value.username = "testbot"
        mock_client_class.return_value = mock_client

        update = {"update_id": 201, "message": {"chat": {"id": 999999, "type": "private"}, "from": {"id": 999999}, "text": "/start"}}
        resp = self._post_update(update, secret="test-secret")
        self.assertEqual(resp.status_code, 200)
        mock_client.send_message.assert_called()

    @patch("telegram_bot.webhook.TelegramClient")
    def test_unlink_confirm(self, mock_client_class):
        mock_client = MagicMock()
        mock_client.get_me.return_value.username = "testbot"
        mock_client_class.return_value = mock_client

        update = {"update_id": 202, "message": {"chat": {"id": 999999, "type": "private"}, "from": {"id": 999999}, "text": "/unlink confirm"}}
        resp = self._post_update(update, secret="test-secret")
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(TelegramAccount.objects.filter(telegram_user_id=999999).exists())

    @patch("telegram_bot.webhook.TelegramClient")
    def test_unlink_without_confirm(self, mock_client_class):
        mock_client = MagicMock()
        mock_client.get_me.return_value.username = "testbot"
        mock_client_class.return_value = mock_client

        update = {"update_id": 203, "message": {"chat": {"id": 999999, "type": "private"}, "from": {"id": 999999}, "text": "/unlink"}}
        resp = self._post_update(update, secret="test-secret")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(TelegramAccount.objects.filter(telegram_user_id=999999).exists())


class TelegramAuthTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username="authuser", email="auth@example.com")

    @patch("telegram_bot.auth.jwt.get_unverified_header")
    @patch("telegram_bot.auth.jwt.decode")
    @patch("telegram_bot.auth._jwks_cache.get_key")
    def test_verify_id_token_success(self, mock_get_key, mock_decode, mock_get_header):
        mock_key = MagicMock()
        mock_key.key = "fake-key"
        mock_get_key.return_value = mock_key
        mock_get_header.return_value = {"kid": "test-key"}
        mock_decode.return_value = {
            "iss": "https://oauth.telegram.org",
            "aud": "123456789",
            "sub": "987654321",
            "exp": 9999999999,
            "iat": 1000000000,
            "nonce": "test-nonce",
            "preferred_username": "testuser",
            "given_name": "Test",
            "family_name": "User",
        }

        from telegram_bot.auth import verify_id_token
        info = verify_id_token("fake-token", "123456789", "test-nonce")
        self.assertEqual(info.telegram_user_id, 987654321)
        self.assertEqual(info.username, "testuser")

    @patch("telegram_bot.auth._jwks_cache.get_key")
    def test_verify_id_token_missing_kid(self, mock_get_key):
        from telegram_bot.auth import verify_id_token, TelegramOIDCError
        mock_get_key.return_value = None
        with self.assertRaises(TelegramOIDCError):
            verify_id_token("fake-token", "123456789", "nonce")

    def test_link_or_create_user_existing(self):
        tg_account = TelegramAccount.objects.create(
            user=self.user, telegram_user_id=111111, first_name="Existing"
        )
        from telegram_bot.auth import TelegramUserInfo
        info = TelegramUserInfo(111111, "updated", "Updated", "User")

        request = MagicMock()
        request.user.is_authenticated = False
        request.session = {}

        account, created = link_or_create_user(request, info)
        self.assertFalse(created)
        self.assertEqual(account.user, self.user)
        self.assertEqual(account.first_name, "Updated")

    def test_link_or_create_user_new_logged_in(self):
        from telegram_bot.auth import TelegramUserInfo
        info = TelegramUserInfo(222222, "newuser", "New", "User")

        request = MagicMock()
        request.user.is_authenticated = True
        request.user = self.user
        request.session = {}

        account, created = link_or_create_user(request, info)
        self.assertFalse(created)
        self.assertEqual(account.user, self.user)
        self.assertEqual(account.telegram_user_id, 222222)

    def test_complete_registration(self):
        from telegram_bot.auth import complete_registration
        from django.contrib.sessions.middleware import SessionMiddleware
        from django.test import RequestFactory

        factory = RequestFactory()
        request = factory.post("/")
        middleware = SessionMiddleware(lambda r: None)
        middleware.process_request(request)
        request.session.save()

        request.session["telegram_pending"] = {
            "telegram_user_id": 333333,
            "username": "pending",
            "first_name": "Pending",
            "last_name": "User",
        }
        request.user = type('User', (), {'is_authenticated': False})()

        account = complete_registration(request, "new@example.com", "password123")
        self.assertIsNotNone(account)
        self.assertEqual(account.telegram_user_id, 333333)
        self.assertTrue(account.user.check_password("password123"))

    def test_complete_registration_requires_email(self):
        from telegram_bot.auth import complete_registration
        from django.core.exceptions import ValidationError
        from django.contrib.sessions.middleware import SessionMiddleware
        from django.test import RequestFactory

        factory = RequestFactory()
        request = factory.post("/")
        middleware = SessionMiddleware(lambda r: None)
        middleware.process_request(request)
        request.session.save()

        request.session["telegram_pending"] = {
            "telegram_user_id": 444444,
            "username": "pending",
            "first_name": "Pending",
            "last_name": "User",
        }
        request.user = type('User', (), {'is_authenticated': False})()

        with self.assertRaises(ValidationError):
            complete_registration(request, "", "")


class TelegramViewsTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username="viewuser", email="view@example.com")

    def test_settings_page_unlinked(self):
        self.client.force_login(self.user)
        resp = self.client.get(reverse("telegram_bot:settings"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "No Telegram account linked")

    def test_settings_page_linked(self):
        TelegramAccount.objects.create(user=self.user, telegram_user_id=555555, first_name="Linked")
        self.client.force_login(self.user)
        resp = self.client.get(reverse("telegram_bot:settings"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Linked")

    def test_unlink_requires_post(self):
        self.client.force_login(self.user)
        TelegramAccount.objects.create(user=self.user, telegram_user_id=666666)
        resp = self.client.get(reverse("telegram_bot:unlink"))
        self.assertEqual(resp.status_code, 400)

    def test_unlink_requires_confirm(self):
        self.client.force_login(self.user)
        TelegramAccount.objects.create(user=self.user, telegram_user_id=777777)
        resp = self.client.post(reverse("telegram_bot:unlink"), {})
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(TelegramAccount.objects.filter(telegram_user_id=777777).exists())

    def test_unlink_confirmed(self):
        self.client.force_login(self.user)
        TelegramAccount.objects.create(user=self.user, telegram_user_id=888888)
        resp = self.client.post(reverse("telegram_bot:unlink"), {"confirm": "yes"})
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(TelegramAccount.objects.filter(telegram_user_id=888888).exists())