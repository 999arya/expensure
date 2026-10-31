from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.views.generic import CreateView

from ledger.defaults import seed_default_categories

from .forms import BankAccountForm, SignupForm
from .models import BankAccount


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
