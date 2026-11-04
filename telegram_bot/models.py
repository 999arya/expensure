"""Telegram identity and webhook deduplication models."""
import hashlib
import hmac
import os
import time
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import models
from django.utils import timezone


class TelegramAccount(models.Model):
    """Links a Django user to their Telegram identity and chat."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="telegram_account",
    )
    telegram_user_id = models.BigIntegerField(unique=True, help_text="Telegram user ID (from 'id' claim).")
    chat_id = models.BigIntegerField(null=True, blank=True, help_text="Chat ID for sending messages; may differ from user ID.")
    username = models.CharField(max_length=64, blank=True, help_text="Telegram @username at link time.")
    first_name = models.CharField(max_length=128, blank=True)
    last_name = models.CharField(max_length=128, blank=True)
    is_active = models.BooleanField(default=True, help_text="If False, bot will not send messages.")
    linked_at = models.DateTimeField(auto_now_add=True)
    last_login_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["chat_id"])]
        constraints = [
            models.UniqueConstraint(
                fields=["telegram_user_id"], name="unique_telegram_user_id"
            )
        ]

    def __str__(self):
        return f"TelegramAccount({self.telegram_user_id}) -> {self.user}"

    def display_name(self):
        parts = []
        if self.first_name:
            parts.append(self.first_name)
        if self.last_name:
            parts.append(self.last_name)
        name = " ".join(parts) if parts else (self.username or f"User {self.telegram_user_id}")
        return name

    def update_profile(self, *, username: str = "", first_name: str = "", last_name: str = "", chat_id: int = None):
        """Refresh the cached profile snapshot from a fresh Telegram response."""
        if username:
            self.username = username
        if first_name:
            self.first_name = first_name
        if last_name:
            self.last_name = last_name
        if chat_id is not None:
            self.chat_id = chat_id
        self.last_login_at = timezone.now()
        self.save(update_fields=["username", "first_name", "last_name", "chat_id", "last_login_at"])


class TelegramUpdate(models.Model):
    """Deduplication record for incoming webhook updates. Stores only metadata, never message content."""

    update_id = models.BigIntegerField(unique=True)
    chat_id = models.BigIntegerField(null=True, blank=True)
    received_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=16, default="processed")
    error_code = models.CharField(max_length=32, blank=True)

    class Meta:
        ordering = ["-received_at"]

    def __str__(self):
        return f"Update {self.update_id} ({self.status})"


class TelegramOAuthState(models.Model):
    """Server-side storage for OIDC state/nonce/PKCE. Tied to the Django session."""

    session_key = models.CharField(max_length=40, db_index=True)
    state = models.CharField(max_length=64)
    nonce = models.CharField(max_length=64)
    code_verifier = models.CharField(max_length=128)
    redirect_uri = models.CharField(max_length=512)
    created_at = models.DateTimeField(auto_now_add=True)
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["state"]), models.Index(fields=["session_key"])]

    def mark_used(self):
        self.used_at = timezone.now()
        self.save(update_fields=["used_at"])

    @classmethod
    def consume(cls, state: str):
        """Atomically consume a state; returns the row or None if invalid/reused."""
        from django.db import transaction
        try:
            with transaction.atomic():
                row = cls.objects.select_for_update().get(state=state)
                if row.used_at is not None:
                    return None
                row.mark_used()
                return row
        except cls.DoesNotExist:
            return None

    @classmethod
    def create_for_session(cls, session_key: str, redirect_uri: str):
        """Generate cryptographically random state, nonce, and PKCE verifier."""
        import secrets
        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)
        code_verifier = secrets.token_urlsafe(64)
        return cls.objects.create(
            session_key=session_key,
            state=state,
            nonce=nonce,
            code_verifier=code_verifier,
            redirect_uri=redirect_uri,
        )

    def pkce_challenge(self) -> str:
        """S256 code challenge for PKCE."""
        digest = hashlib.sha256(self.code_verifier.encode()).digest()
        import base64
        return base64.urlsafe_b64encode(digest).decode().rstrip("=")