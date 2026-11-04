"""OIDC views for Telegram Login: start, callback, completion, unlink, settings."""
import logging
import urllib.parse
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseBadRequest, HttpResponseRedirect, HttpResponse
from django.shortcuts import render, redirect
from django.urls import reverse
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator

from .models import TelegramAccount, TelegramOAuthState
from .auth import (
    verify_id_token,
    exchange_code_for_tokens,
    link_or_create_user,
    complete_registration,
    unlink_telegram,
    TelegramOIDCError,
)

logger = logging.getLogger(__name__)


def _get_oidc_config(request):
    """Get OIDC config from settings/env."""
    client_id = getattr(settings, "TELEGRAM_CLIENT_ID", None) or os.environ.get("TELEGRAM_CLIENT_ID", "").strip()
    client_secret = getattr(settings, "TELEGRAM_CLIENT_SECRET", None) or os.environ.get("TELEGRAM_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        return None, None
    return client_id, client_secret


def _build_redirect_uri(request):
    """Build the absolute redirect URI for the callback."""
    return request.build_absolute_uri(reverse("telegram_bot:callback"))


def _generate_pkce():
    """Generate PKCE code_verifier and code_challenge."""
    import secrets, hashlib, base64
    code_verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(code_verifier.encode()).digest()
    code_challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    return code_verifier, code_challenge


class TelegramLoginView(View):
    """Initiate Telegram OIDC login flow."""

    def get(self, request):
        client_id, client_secret = _get_oidc_config(request)
        if not client_id:
            messages.error(request, "Telegram login is not configured.")
            return redirect("accounts:login")

        # Create state in database (keyed by state, not session)
        state_row = TelegramOAuthState.create(_build_redirect_uri(request))
        code_verifier, code_challenge = _generate_pkce()

        # Build authorization URL
        scope = "openid profile"
        params = {
            "client_id": client_id,
            "redirect_uri": state_row.redirect_uri,
            "response_type": "code",
            "scope": scope,
            "state": state_row.state,
            "code_challenge": state_row.pkce_challenge(),
            "code_challenge_method": "S256",
        }
        auth_url = "https://oauth.telegram.org/auth?" + urllib.parse.urlencode(params)
        return HttpResponseRedirect(auth_url)


class TelegramCallbackView(View):
    """Handle Telegram OIDC callback."""

    def get(self, request):
        client_id, client_secret = _get_oidc_config(request)
        if not client_id:
            messages.error(request, "Telegram login is not configured.")
            return redirect("accounts:login")

        # Check for errors from Telegram
        error = request.GET.get("error")
        if error:
            error_desc = request.GET.get("error_description", error)
            logger.warning("Telegram OAuth error: %s - %s", error, error_desc)
            messages.error(request, f"Telegram login failed: {error_desc}")
            return redirect("accounts:login")

        # Get and validate state
        state = request.GET.get("state")
        code = request.GET.get("code")
        if not state or not code:
            messages.error(request, "Invalid callback: missing state or code.")
            return redirect("accounts:login")

        # Consume state from database (keyed by state)
        state_row = TelegramOAuthState.consume(state)
        if not state_row:
            messages.error(request, "Invalid or expired login session. Please try again.")
            return redirect("accounts:login")

        # Exchange code for tokens
        try:
            tokens = exchange_code_for_tokens(
                code=code,
                code_verifier=state_row.code_verifier,
                client_id=client_id,
                client_secret=client_secret,
                redirect_uri=state_row.redirect_uri,
            )
        except TelegramOIDCError as e:
            logger.error("Token exchange failed: %s", e)
            messages.error(request, "Failed to complete login. Please try again.")
            return redirect("accounts:login")

        id_token = tokens.get("id_token")
        if not id_token:
            messages.error(request, "No ID token received from Telegram.")
            return redirect("accounts:login")

        # Verify ID token
        try:
            user_info = verify_id_token(id_token, client_id, state_row.nonce)
        except TelegramOIDCError as e:
            logger.error("ID token verification failed: %s", e)
            messages.error(request, "Login verification failed. Please try again.")
            return redirect("accounts:login")

        # Link or create user
        tg_account, created = link_or_create_user(request, user_info)

        if created:
            # New user needs to complete registration (provide email)
            return redirect("telegram_bot:complete")
        else:
            # Existing user linked - log them in
            from django.contrib.auth import login
            login(request, tg_account.user)
            messages.success(request, f"Welcome back, {tg_account.display_name()}!")
            return redirect("core:dashboard")


class TelegramCompleteView(View):
    """Complete registration for new Telegram users (collect email, optional password)."""

    def get(self, request):
        pending = request.session.get("telegram_pending")
        if not pending:
            messages.error(request, "No pending registration. Please start over.")
            return redirect("telegram_bot:login")

        return render(request, "telegram_bot/complete.html", {
            "display_name": f"{pending.get('first_name', '')} {pending.get('last_name', '')}".strip() or pending.get('username', 'User'),
        })

    def post(self, request):
        pending = request.session.get("telegram_pending")
        if not pending:
            messages.error(request, "No pending registration. Please start over.")
            return redirect("telegram_bot:login")

        email = request.POST.get("email", "").strip().lower()
        password = request.POST.get("password", "")
        password_confirm = request.POST.get("password_confirm", "")

        # Validate
        errors = []
        if not email:
            errors.append("Email is required.")
        elif "@" not in email:
            errors.append("Enter a valid email address.")
        if password and password != password_confirm:
            errors.append("Passwords do not match.")
        if password and len(password) < 8:
            errors.append("Password must be at least 8 characters.")

        if errors:
            for err in errors:
                messages.error(request, err)
            return render(request, "telegram_bot/complete.html", {
                "display_name": f"{pending.get('first_name', '')} {pending.get('last_name', '')}".strip() or pending.get('username', 'User'),
            })

        # Complete registration
        try:
            from .auth import complete_registration
            tg_account = complete_registration(request, email, password)
            messages.success(request, f"Account created! Welcome, {tg_account.display_name()}.")
            return redirect("core:dashboard")
        except Exception as e:
            logger.exception("Registration completion failed")
            messages.error(request, "Failed to create account. Please try again.")
            return redirect("telegram_bot:complete")


@login_required
def telegram_unlink(request):
    """Unlink Telegram account (POST only, with confirmation)."""
    if request.method != "POST":
        return HttpResponseBadRequest("POST required")

    confirm = request.POST.get("confirm")
    if confirm != "yes":
        messages.error(request, "Confirmation required.")
        return redirect("telegram_bot:settings")

    if unlink_telegram(request.user):
        messages.success(request, "Telegram account unlinked.")
    else:
        messages.info(request, "No Telegram account was linked.")
    return redirect("telegram_bot:settings")


@login_required
def telegram_settings(request):
    """Telegram account settings page."""
    try:
        tg_account = request.user.telegram_account
    except TelegramAccount.DoesNotExist:
        tg_account = None

    return render(request, "telegram_bot/settings.html", {
        "tg_account": tg_account,
    })


# Import os at top level for _get_oidc_config
import os