from django.contrib.auth import login
from django.db import transaction
from django.shortcuts import redirect, render

from ledger.defaults import seed_default_categories

from .forms import SignupForm


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
