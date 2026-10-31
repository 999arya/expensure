from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from accounts.services import account_cards
from ledger.models import Transaction

# Development-only preview states. Ignored entirely when DEBUG is off.
STATES = ("ready", "empty", "loading", "error")


@login_required
def dashboard(request):
    page_state = "ready"

    if settings.DEBUG:
        requested = request.GET.get("state", "ready")
        if requested in STATES:
            page_state = requested

    accounts = account_cards(request.user)
    transactions = (
        Transaction.objects.filter(account__owner=request.user)
        .select_related("category", "account")
        .order_by("-occurred_at", "-id")[:6]
    )
    review_count = Transaction.objects.filter(
        account__owner=request.user,
        status=Transaction.Status.NEEDS_REVIEW,
    ).count()

    if page_state == "empty":
        accounts = []
        transactions = []
        review_count = 0

    context = {
        "accounts": accounts,
        "transactions": transactions,
        "goal": None,
        "review_count": review_count,
        "page_state": page_state,
        "is_debug": settings.DEBUG,
    }
    return render(request, "dashboard.html", context)
