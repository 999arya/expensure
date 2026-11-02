from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import PasswordResetView
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils import timezone
from django.views import View
from django.views.generic import CreateView

from core.formatting import format_inr
from ledger.defaults import seed_default_categories

from .forms import (
    BankAccountForm,
    ConfirmBalanceForm,
    DeleteBankAccountForm,
    SignupForm,
)
from .models import BalanceCheckpoint, BankAccount
from .services import account_cards, estimate_balance, latest_checkpoint


class ConfiguredPasswordResetView(PasswordResetView):
    """Avoid promising delivery when the deployment has no email transport."""

    def dispatch(self, request, *args, **kwargs):
        if not getattr(settings, "PASSWORD_RESET_ENABLED", True):
            return render(request, "accounts/password_reset_unavailable.html", status=503)
        return super().dispatch(request, *args, **kwargs)


@transaction.atomic
def signup(request):
    """Create personal details, first account and categories atomically."""
    if request.user.is_authenticated:
        return redirect("core:dashboard")

    data = request.POST if request.method == "POST" else None
    form = SignupForm(data)
    account_form = BankAccountForm(data, owner=None, prefix="bank")
    if request.method == "POST":
        user_valid = form.is_valid()
        account_valid = account_form.is_valid()
        if user_valid and account_valid:
            user = form.save()
            account_form.owner = user
            account_form.save()
            seed_default_categories(user)
            login(request, user)
            messages.success(request, "Your account is ready. Set your first goal to get a step-by-step plan.")
            return redirect("core:dashboard")

    return render(request, "accounts/signup.html", {"form": form, "account_form": account_form})


class AddAccountView(LoginRequiredMixin, CreateView):
    """Add an account and its first checkpoint in one step."""

    model = BankAccount
    form_class = BankAccountForm
    template_name = "accounts/add_account.html"
    success_url = reverse_lazy("core:dashboard")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["owner"] = self.request.user
        return kwargs

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(
            self.request,
            f"{self.object.nickname} added. You can record transactions on it now.",
        )
        return response


@login_required
def account_list(request):
    """Personal details and every bank account in one place."""
    accounts = (
        BankAccount.objects.filter(owner=request.user)
        .prefetch_related("checkpoints")
        .order_by("bank", "nickname")
    )
    rows = []
    for account in accounts:
        checkpoint = latest_checkpoint(account)
        rows.append(
            {
                "account": account,
                "estimate_paise": estimate_balance(account, checkpoint),
                "confirmed_at": checkpoint.effective_at if checkpoint else None,
                "transaction_count": account.transactions.count(),
            }
        )

    return render(
        request,
        "accounts/account_list.html",
        {"account_rows": rows, "cards": account_cards(request.user)},
    )


@login_required
def account_detail(request, pk):
    """One bank account, its balance anchor and recent activity."""
    account = get_object_or_404(
        BankAccount,
        pk=pk,
        owner=request.user,
    )
    checkpoint = latest_checkpoint(account)
    transactions = account.transactions.select_related("category").order_by(
        "-occurred_at", "-id"
    )[:15]
    checkpoints = account.checkpoints.all()[:10]
    batches = account.import_batches.select_related("account").order_by(
        "-created_at", "-id"
    )[:5]

    return render(
        request,
        "accounts/account_detail.html",
        {
            "account": account,
            "checkpoint": checkpoint,
            "estimate_paise": estimate_balance(account, checkpoint),
            "transactions": transactions,
            "checkpoints": checkpoints,
            "batches": batches,
        },
    )


@login_required
def delete_bank_account(request, pk):
    """Delete one bank account and its recorded movements."""
    account = get_object_or_404(
        BankAccount,
        pk=pk,
        owner=request.user,
    )

    if request.method == "POST":
        form = DeleteBankAccountForm(request.POST, account=account)
        if form.is_valid():
            nickname = account.nickname
            account.delete()
            messages.success(
                request,
                f"{nickname} and its transactions were deleted.",
            )
            return redirect("accounts:list")
    else:
        form = DeleteBankAccountForm(account=account)

    return render(
        request,
        "accounts/delete_bank_account.html",
        {
            "account": account,
            "form": form,
            "estimate_paise": estimate_balance(account, latest_checkpoint(account)),
        },
    )


class ConfirmBalanceView(LoginRequiredMixin, View):
    """Record the balance the bank shows now as a new checkpoint.

    Only the owner can do this: the account is looked up with `owner` in the
    query, so another user's id is a 404 rather than a hint that it exists.
    """

    template_name = "accounts/confirm_balance.html"

    def get_account(self):
        return get_object_or_404(
            BankAccount,
            pk=self.kwargs["pk"],
            owner=self.request.user,
            is_active=True,
        )

    def render_form(self, account, form):
        checkpoint = latest_checkpoint(account)
        return render(
            self.request,
            self.template_name,
            {
                "account": account,
                "form": form,
                "estimate_paise": estimate_balance(account, checkpoint),
                "confirmed_at": checkpoint.effective_at if checkpoint else None,
            },
        )

    def get(self, request, pk):
        return self.render_form(self.get_account(), ConfirmBalanceForm())

    def post(self, request, pk):
        account = self.get_account()
        form = ConfirmBalanceForm(request.POST)

        if not form.is_valid():
            return self.render_form(account, form)

        checkpoint = BalanceCheckpoint.objects.create(
            account=account,
            amount_paise=form.cleaned_data["amount"],
            effective_at=form.cleaned_data["effective_at"],
            source=BalanceCheckpoint.Source.MANUAL,
            note="Balance confirmed by you.",
        )

        estimate = estimate_balance(account, latest_checkpoint(account))
        messages.success(
            request,
            f"Balance confirmed as of {timezone.localtime(checkpoint.effective_at):%d %b, %H:%M}. "
            f"Estimated balance for {account.nickname} is {format_inr(estimate)}.",
        )
        return redirect("core:dashboard")


@login_required
def export_user_data(request):
    """Download full JSON export of all personal financial data (Phase 8)."""
    import json

    from django.http import HttpResponse

    from coach.models import AIUsage, FinancialInsight, TargetRecommendation
    from ingestion.models import ImportBatch
    from ledger.models import Category, Transaction, Transfer
    from planning.models import AccountGoal, Bill, Goal, IncomePlan, MonthlyGoal
    from reports.models import MonthlyReportSnapshot, RecurringPaymentRule

    user = request.user
    today_str = timezone.localdate().strftime("%Y%m%d")

    data = {
        "user": {
            "username": user.username,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "email": user.email,
            "date_joined": user.date_joined.isoformat(),
        },
        "accounts": [
            {
                "id": acc.id,
                "bank": acc.bank,
                "nickname": acc.nickname,
                "account_suffix": acc.account_suffix,
                "account_type": acc.account_type,
                "created_at": acc.created_at.isoformat(),
                "checkpoints": [
                    {
                        "amount_paise": cp.amount_paise,
                        "effective_at": cp.effective_at.isoformat(),
                        "source": cp.source,
                        "note": cp.note,
                    }
                    for cp in acc.checkpoints.all()
                ],
            }
            for acc in user.bank_accounts.all()
        ],
        "categories": [
            {
                "id": category.id,
                "name": category.name,
                "kind": category.kind,
                "is_default": category.is_default,
            }
            for category in Category.objects.filter(owner=user)
        ],
        "transactions": [
            {
                "id": txn.id,
                "account_id": txn.account_id,
                "transfer_id": txn.transfer_id,
                "occurred_at": txn.occurred_at.isoformat(),
                "received_at": txn.received_at.isoformat() if txn.received_at else None,
                "amount_paise": txn.amount_paise,
                "direction": txn.direction,
                "category_id": txn.category_id,
                "category": txn.category.name if txn.category else None,
                "description": txn.description,
                "reference": txn.reference,
                "status": txn.status,
                "source": txn.source,
                "dedup_key": txn.dedup_key,
            }
            for txn in Transaction.objects.filter(account__owner=user)
        ],
        "transfers": [
            {
                "id": transfer.id,
                "from_account_id": transfer.from_account_id,
                "to_account_id": transfer.to_account_id,
                "amount_paise": transfer.amount_paise,
                "occurred_at": transfer.occurred_at.isoformat(),
                "description": transfer.description,
                "created_at": transfer.created_at.isoformat(),
            }
            for transfer in Transfer.objects.filter(owner=user)
        ],
        "planning": {
            "goals": [
                {
                    "id": goal.id,
                    "name": goal.name,
                    "target_paise": goal.target_paise,
                    "saved_paise": goal.saved_paise,
                    "target_date": goal.target_date.isoformat() if goal.target_date else None,
                    "is_active": goal.is_active,
                }
                for goal in Goal.objects.filter(owner=user)
            ],
            "monthly_goals": [
                {
                    "id": goal.id,
                    "month": goal.month.isoformat(),
                    "savings_paise": goal.savings_paise,
                }
                for goal in MonthlyGoal.objects.filter(owner=user)
            ],
            "account_goals": [
                {
                    "account_id": goal.account_id,
                    "minimum_floor_paise": goal.minimum_floor_paise,
                    "target_paise": goal.target_paise,
                }
                for goal in AccountGoal.objects.filter(account__owner=user)
            ],
            "income_plans": [
                {
                    "id": item.id,
                    "description": item.description,
                    "amount_paise": item.amount_paise,
                    "expected_on": item.expected_on.isoformat(),
                    "status": item.status,
                    "account_id": item.account_id,
                    "settled_transaction_id": item.settled_transaction_id,
                }
                for item in IncomePlan.objects.filter(owner=user)
            ],
            "bills": [
                {
                    "id": item.id,
                    "description": item.description,
                    "amount_paise": item.amount_paise,
                    "due_on": item.due_on.isoformat(),
                    "status": item.status,
                    "account_id": item.account_id,
                    "settled_transaction_id": item.settled_transaction_id,
                }
                for item in Bill.objects.filter(owner=user)
            ],
        },
        "reports": {
            "recurring_rules": [
                {
                    "id": rule.id,
                    "merchant_name": rule.merchant_name,
                    "typical_amount_paise": rule.typical_amount_paise,
                    "category_id": rule.category_id,
                    "status": rule.status,
                }
                for rule in RecurringPaymentRule.objects.filter(owner=user)
            ],
            "monthly_snapshots": [
                {
                    "id": snapshot.id,
                    "year": snapshot.year,
                    "month": snapshot.month,
                    "income_paise": snapshot.income_paise,
                    "expenses_paise": snapshot.expenses_paise,
                    "savings_paise": snapshot.savings_paise,
                    "generated_at": snapshot.generated_at.isoformat(),
                }
                for snapshot in MonthlyReportSnapshot.objects.filter(owner=user)
            ],
        },
        "coach": {
            "insights": [
                {
                    "id": insight.id,
                    "title": insight.title,
                    "kind": insight.kind,
                    "body": insight.body,
                    "reason": insight.reason,
                    "amount_paise": insight.amount_paise,
                    "time_period": insight.time_period,
                    "assumptions": insight.assumptions,
                    "status": insight.status,
                    "source": insight.source,
                    "summary_hash": insight.summary_hash,
                    "created_at": insight.created_at.isoformat(),
                    "updated_at": insight.updated_at.isoformat(),
                }
                for insight in FinancialInsight.objects.filter(owner=user)
            ],
            "target_recommendations": list(TargetRecommendation.objects.filter(owner=user).values()),
            "ai_usage": [
                {
                    "day": usage.day.isoformat(),
                    "calls": usage.calls,
                }
                for usage in AIUsage.objects.filter(owner=user)
            ],
        },
        "statement_imports": [
            {
                "id": batch.id,
                "account_id": batch.account_id,
                "bank": batch.bank,
                "status": batch.status,
                "original_filename": batch.original_filename,
                "period_start": batch.period_start.isoformat() if batch.period_start else None,
                "period_end": batch.period_end.isoformat() if batch.period_end else None,
                "row_count": batch.row_count,
                "imported_count": batch.imported_count,
                "duplicate_count": batch.duplicate_count,
                "invalid_count": batch.invalid_count,
                "created_at": batch.created_at.isoformat(),
                "completed_at": batch.completed_at.isoformat() if batch.completed_at else None,
            }
            for batch in ImportBatch.objects.filter(owner=user)
        ],
        "export_generated_at": timezone.now().isoformat(),
    }

    from django.core.serializers.json import DjangoJSONEncoder
    json_content = json.dumps(data, indent=2, cls=DjangoJSONEncoder)
    filename = f"expensure-data-{user.username}-{today_str}.json"
    response = HttpResponse(json_content, content_type="application/json")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@login_required
def delete_account(request):
    """Full cascade deletion of user and all associated records."""
    from django.contrib.auth import logout

    if request.method == "POST":
        confirm_name = request.POST.get("confirm_username", "").strip()
        if confirm_name == request.user.username:
            user = request.user
            logout(request)
            user.delete()
            messages.info(request, "Your account and all associated financial records have been permanently deleted.")
            return redirect("accounts:login")
        else:
            messages.error(request, "Username did not match. Deletion cancelled.")

    return render(request, "accounts/delete_account.html")


def privacy_info(request):
    """Public privacy and consent information."""
    return render(request, "accounts/privacy.html")
