"""Telegram OIDC authentication: ID token verification and user linking."""
import json
import logging
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Optional

import jwt
from django.conf import settings
from django.contrib.auth import get_user_model, login
from django.core.exceptions import ValidationError
from django.utils import timezone

from .models import TelegramAccount, TelegramOAuthState

logger = logging.getLogger(__name__)

JWKS_URL = "https://oauth.telegram.org/.well-known/jwks.json"
ISSUER = "https://oauth.telegram.org"
TOKEN_URL = "https://oauth.telegram.org/token"


@dataclass
class TelegramUserInfo:
    telegram_user_id: int
    username: str
    first_name: str
    last_name: str
    oidc_subject: str = ""


class TelegramOIDCError(Exception):
    """Raised when OIDC verification fails."""
    pass


class JWKSCache:
    """Simple in-process cache for Telegram's JWKS keys."""

    def __init__(self):
        self._keys: dict[str, jwt.PyJWK] = {}
        self._fetched_at: float = 0
        self._ttl = 3600  # 1 hour

    def get_key(self, kid: str) -> Optional[jwt.PyJWK]:
        if time.time() - self._fetched_at > self._ttl or not self._keys:
            self._fetch()
        return self._keys.get(kid)

    def _fetch(self):
        try:
            with urllib.request.urlopen(JWKS_URL, timeout=5) as resp:
                data = json.loads(resp.read())
            self._keys = {k["kid"]: jwt.PyJWK(k) for k in data.get("keys", [])}
            self._fetched_at = time.time()
        except Exception as e:
            logger.warning("Failed to fetch Telegram JWKS: %s", e)


_jwks_cache = JWKSCache()


def verify_id_token(id_token: str, client_id: str, expected_nonce: str) -> TelegramUserInfo:
    """
    Verify a Telegram ID token.
    Returns TelegramUserInfo on success.
    Raises TelegramOIDCError on any failure.
    """
    # Get the key ID from the header without verifying
    try:
        header = jwt.get_unverified_header(id_token)
    except jwt.PyJWTError as e:
        raise TelegramOIDCError(f"Invalid token header: {e}")

    kid = header.get("kid")
    if not kid:
        raise TelegramOIDCError("Token missing 'kid' header")

    jwk = _jwks_cache.get_key(kid)
    if not jwk:
        raise TelegramOIDCError(f"Unknown key ID: {kid}")

    try:
        payload = jwt.decode(
            id_token,
            jwk.key,
            algorithms=["RS256"],
            audience=client_id,
            issuer=ISSUER,
            options={"require": ["exp", "iat", "iss", "aud", "sub", "nonce"]},
        )
    except jwt.ExpiredSignatureError:
        raise TelegramOIDCError("Token expired")
    except jwt.InvalidAudienceError:
        raise TelegramOIDCError("Invalid audience")
    except jwt.InvalidIssuerError:
        raise TelegramOIDCError("Invalid issuer")
    except jwt.PyJWTError as e:
        raise TelegramOIDCError(f"Token verification failed: {e}")

    # Verify nonce
    if payload.get("nonce") != expected_nonce:
        raise TelegramOIDCError("Nonce mismatch")

    # Extract user info
    # OIDC subjects are not Bot API user IDs. The profile scope supplies id.
    telegram_user_id = payload.get("id")
    if isinstance(telegram_user_id, bool) or not isinstance(telegram_user_id, (int, str)):
        raise TelegramOIDCError("Token missing Telegram profile ID")
    try:
        telegram_user_id = int(telegram_user_id)
    except (ValueError, TypeError):
        raise TelegramOIDCError("Invalid Telegram profile ID")
    if not 0 < telegram_user_id < 2**63:
        raise TelegramOIDCError("Invalid Telegram profile ID")

    return TelegramUserInfo(
        telegram_user_id=int(telegram_user_id),
        username=payload.get("preferred_username", ""),
        first_name=payload.get("given_name") or payload.get("first_name", ""),
        last_name=payload.get("family_name") or payload.get("last_name", ""),
        oidc_subject=str(payload["sub"]),
    )


def exchange_code_for_tokens(code: str, code_verifier: str, client_id: str, client_secret: str, redirect_uri: str) -> dict:
    """Exchange authorization code for access token and ID token."""
    import base64
    credentials = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()

    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "code_verifier": code_verifier,
    }

    req = urllib.request.Request(
        TOKEN_URL,
        data=urllib.parse.urlencode(data).encode(),
        headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "Expensure/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        logger.error("Token exchange failed: %s %s", e.code, body)
        raise TelegramOIDCError(f"Token exchange failed: {e.code}")
    except Exception as e:
        logger.error("Token exchange error: %s", e)
        raise TelegramOIDCError(f"Token exchange error: {e}")


def link_or_create_user(request, user_info: TelegramUserInfo) -> tuple[TelegramAccount, bool]:
    """
    Link Telegram identity to existing user or create a new one.
    Returns (TelegramAccount, created_flag).
    """
    User = get_user_model()

    # Try to find existing link
    try:
        tg_account = TelegramAccount.objects.select_related("user").get(telegram_user_id=user_info.telegram_user_id)
        if request.user.is_authenticated and tg_account.user_id != request.user.pk:
            raise TelegramOIDCError("This Telegram identity is already connected to another account.")
        # Update profile snapshot
        tg_account.update_profile(
            username=user_info.username,
            first_name=user_info.first_name,
            last_name=user_info.last_name,
        )
        return tg_account, False
    except TelegramAccount.DoesNotExist:
        pass

    # Check if the current session already has a logged-in user
    if request.user.is_authenticated:
        existing = TelegramAccount.objects.filter(user=request.user).first()
        if existing:
            # Repair the old sub-as-id mapping only for an authenticated owner
            # whose freshly verified subject matches the stored value.
            if str(existing.telegram_user_id) != user_info.oidc_subject:
                raise TelegramOIDCError("Unlink your current Telegram account in settings before connecting another.")
            existing.telegram_user_id = user_info.telegram_user_id
            existing.chat_id = None
            existing.save(update_fields=["telegram_user_id", "chat_id"])
            existing.update_profile(username=user_info.username, first_name=user_info.first_name,
                                    last_name=user_info.last_name)
            return existing, False
        # Link to the current user
        tg_account = TelegramAccount.objects.create(
            user=request.user,
            telegram_user_id=user_info.telegram_user_id,
            username=user_info.username,
            first_name=user_info.first_name,
            last_name=user_info.last_name,
        )
        return tg_account, False

    # Old links need an authenticated website session to repair safely.
    subject = user_info.oidc_subject
    if subject.isascii() and subject.isdigit() and len(subject) <= 19 and int(subject) < 2**63:
        if TelegramAccount.objects.filter(telegram_user_id=int(subject)).exists():
            raise TelegramOIDCError("Sign in to your existing Expensure account, then connect Telegram again to repair the link.")

    # No existing link and no logged-in user -> need to create a new user
    # We can't create the user here because we need email from the completion page.
    # Store the info in session for the completion step.
    request.session["telegram_pending"] = {
        "telegram_user_id": user_info.telegram_user_id,
        "username": user_info.username,
        "first_name": user_info.first_name,
        "last_name": user_info.last_name,
    }
    # Return a dummy account to signal "needs completion"
    return None, True  # type: ignore


def complete_registration(request, email: str, password: str = "") -> TelegramAccount:
    """Create the Django user and link the pending Telegram identity."""
    pending = request.session.pop("telegram_pending", None)
    if not pending:
        raise ValidationError("No pending Telegram registration.")

    # Validate email
    if not email or "@" not in email:
        raise ValidationError("Enter a valid email address.")

    User = get_user_model()
    base_username = f"tg_{pending['telegram_user_id']}"
    username = base_username
    suffix = 1
    while User.objects.filter(username=username).exists():
        suffix += 1
        username = f"{base_username}_{suffix}"

    user = User.objects.create_user(
        username=username,
        email=email,
        first_name=pending["first_name"],
        last_name=pending["last_name"],
    )
    if password:
        user.set_password(password)
    else:
        user.set_unusable_password()
    user.save()

    tg_account = TelegramAccount.objects.create(
        user=user,
        telegram_user_id=pending["telegram_user_id"],
        username=pending["username"],
        first_name=pending["first_name"],
        last_name=pending["last_name"],
    )
    login(request, user)
    return tg_account


def unlink_telegram(user) -> bool:
    """Unlink Telegram account from user. Returns True if unlinked."""
    try:
        tg_account = user.telegram_account
        tg_account.delete()
        return True
    except TelegramAccount.DoesNotExist:
        return False
