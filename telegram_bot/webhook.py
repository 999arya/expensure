"""Webhook handler for Telegram updates (Phase 1: login + account info)."""
import json
import logging
import os
import hmac
from datetime import datetime
from django.conf import settings
from django.http import HttpResponse, HttpResponseBadRequest
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from django.utils import timezone

from .models import TelegramAccount, TelegramUpdate
from .client import TelegramClient, build_help_text, format_welcome, format_linked, format_already_linked, format_unlinked, format_unlink_prompt, format_unknown_command

# Import existing services
from accounts.services import account_cards
from ledger.models import Transaction
from ledger.services import status_for_transaction
from planning.services import allowance_summary, primary_goal
from core.formatting import format_inr

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

        # --- Account info commands (require linked account) ---
        elif cmd == "/balance":
            if not is_linked:
                client.send_message(chat_id, "🔒 Please link your account first. Use /start")
                return
            self._cmd_balance(client, chat_id, tg_account)

        elif cmd == "/today":
            if not is_linked:
                client.send_message(chat_id, "🔒 Please link your account first. Use /start")
                return
            self._cmd_today(client, chat_id, tg_account)

        elif cmd == "/transactions":
            if not is_linked:
                client.send_message(chat_id, "🔒 Please link your account first. Use /start")
                return
            self._cmd_transactions(client, chat_id, tg_account, args)

        elif cmd == "/plan":
            if not is_linked:
                client.send_message(chat_id, "🔒 Please link your account first. Use /start")
                return
            self._cmd_plan(client, chat_id, tg_account)

        else:
            client.send_message(chat_id, format_unknown_command())

    # --- Account info commands (require linked account) ---

    def _cmd_balance(self, client: TelegramClient, chat_id: int, tg_account: TelegramAccount):
        """Show all account balances."""
        accounts = account_cards(tg_account.user)
        if not accounts:
            client.send_message(chat_id, "📭 No bank accounts added yet. Add one from the web app.")
            return

        lines = ["💰 <b>Account Balances</b>\n"]
        total = 0
        for acc in accounts:
            bal = format_inr(acc.balance_paise)
            total += acc.balance_paise
            confirmed = acc.confirmed_at.strftime("%d %b %Y") if acc.confirmed_at else "Never"
            lines.append(
                f"• <b>{acc.nickname}</b> ({acc.bank} · ····{acc.suffix})\n"
                f"  Balance: <code>{bal}</code>  (confirmed {confirmed})"
            )
        lines.append(f"\n<b>Total: {format_inr(total)}</b>")
        lines.append("\n<i>Balances are estimates: last confirmed + recorded movements since.</i>")
        client.send_message(chat_id, "\n".join(lines))

    def _cmd_today(self, client: TelegramClient, chat_id: int, tg_account: TelegramAccount):
        """Show today's transactions."""
        today_start = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
        txns = (
            Transaction.objects.filter(account__owner=tg_account.user, occurred_at__gte=today_start)
            .select_related("account", "category")
            .order_by("-occurred_at")
        )

        if not txns.exists():
            client.send_message(chat_id, "📭 No transactions recorded today.")
            return

        lines = ["📅 <b>Today's Transactions</b>\n"]
        total_in = 0
        total_out = 0
        for t in txns[:10]:
            sign = "+" if t.direction == Transaction.Direction.CREDIT else "−"
            amt = format_inr(t.amount_paise)
            cat = t.category.name if t.category else "Uncategorized"
            acc = t.account.nickname
            time_str = timezone.localtime(t.occurred_at).strftime("%H:%M")
            lines.append(f"{time_str}  {sign}<code>{amt}</code>  {cat}  ({acc})")
            if t.direction == Transaction.Direction.CREDIT:
                total_in += t.amount_paise
            else:
                total_out += t.amount_paise

        lines.append(f"\n🟢 In: <code>{format_inr(total_in)}</code>  🔴 Out: <code>{format_inr(total_out)}</code>")
        lines.append(f"Net: <code>{'+' if total_in >= total_out else ''}{format_inr(total_in - total_out)}</code>")
        client.send_message(chat_id, "\n".join(lines))

    def _cmd_transactions(self, client: TelegramClient, chat_id: int, tg_account: TelegramAccount, args: list):
        """Show recent transactions (default 10, or /transactions N)."""
        try:
            limit = int(args[0]) if args else 10
            limit = min(max(limit, 1), 50)
        except (ValueError, IndexError):
            limit = 10

        txns = (
            Transaction.objects.filter(account__owner=tg_account.user)
            .select_related("account", "category")
            .order_by("-occurred_at")[:limit]
        )

        if not txns:
            client.send_message(chat_id, "📭 No transactions yet.")
            return

        lines = [f"📋 <b>Last {len(txns)} Transactions</b>\n"]
        for t in txns:
            sign = "+" if t.direction == Transaction.Direction.CREDIT else "−"
            amt = format_inr(t.amount_paise)
            cat = t.category.name if t.category else "Uncategorized"
            acc = t.account.nickname
            date_str = timezone.localtime(t.occurred_at).strftime("%d %b %H:%M")
            status_emoji = "✅" if t.status == Transaction.Status.POSTED else "⏳" if t.status == Transaction.Status.NEEDS_REVIEW else "🚫"
            lines.append(f"{status_emoji} {date_str}  {sign}<code>{amt}</code>  {cat}  ({acc})")
            if t.description:
                lines.append(f"    <i>{t.description[:80]}</i>")

        client.send_message(chat_id, "\n".join(lines))

    def _cmd_plan(self, client: TelegramClient, chat_id: int, tg_account: TelegramAccount):
        """Show monthly plan summary."""
        allowance = allowance_summary(tg_account.user)
        goal = primary_goal(tg_account.user)

        lines = ["📊 <b>Monthly Plan</b>\n"]
        lines.append(f"💵 <b>Cash-backed allowance:</b> <code>{format_inr(allowance.cash_backed_paise)}</code>")
        lines.append(f"📈 <b>Forecast allowance:</b> <code>{format_inr(allowance.forecast_allowance_paise)}</code>")
        lines.append(f"💰 <b>Daily safe-to-spend:</b> <code>{format_inr(allowance.daily_paise)}</code>/day")

        if allowance.income_paise or allowance.bills_paise:
            lines.append(f"\n💵 Expected income: <code>{format_inr(allowance.income_paise)}</code>")
            lines.append(f"🧾 Unpaid bills: <code>{format_inr(allowance.bills_paise)}</code>")

        if goal:
            remaining = goal.remaining_paise
            days_left = (goal.target_date - timezone.localdate()).days if goal.target_date else None
            lines.append(f"\n🎯 <b>Primary Goal:</b> {goal.name}")
            lines.append(f"   Remaining: <code>{format_inr(remaining)}</code>")
            if days_left and days_left > 0:
                daily = (remaining + days_left - 1) // days_left
                lines.append(f"   Target: {goal.target_date.strftime('%d %b %Y')}  →  <code>{format_inr(daily)}</code>/day")
            elif days_left is not None:
                lines.append(f"   ⚠️ Target date passed")

        if allowance.pending_review_count:
            lines.append(f"\n⏳ <b>{allowance.pending_review_count} transactions need review</b> — check the web app.")

        client.send_message(chat_id, "\n".join(lines))