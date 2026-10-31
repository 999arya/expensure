from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils import timezone
from django.views import View
from django.views.generic import CreateView

from core.formatting import format_inr
from ledger.defaults import seed_default_categories

from .forms import BankAccountForm, ConfirmBalanceForm, SignupForm
from .models import BalanceCheckpoint, BankAccount
from .services import estimate_balance, latest_checkpoint


@transaction.atomic
def signup(request):
    """Create a user, their starter categories, and log them in.

    The whole thing is one transaction: a user is never left without
    categories because the second write failed.
    """
    if request.user.is_authenticated:
        return redirect("core:dashboard")

    if request.method == "POST":
        form = SignupForm(request.POST)
        if form.is_valid():
            user = form.save()
            seed_default_categories(user)
            login(request, user)
            return redirect("core:dashboard")
    else:
        form = SignupForm()

    return render(request, "accounts/signup.html", {"form": form})


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
