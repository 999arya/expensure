from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from . import demo

# Development-only preview states. Ignored entirely when DEBUG is off.
STATES = ("ready", "empty", "loading", "error")


@login_required
def dashboard(request):
    page_state = "ready"

    if settings.DEBUG:
        requested = request.GET.get("state", "ready")
        if requested in STATES:
            page_state = requested

    accounts = demo.ACCOUNTS
    transactions = demo.TRANSACTIONS
    goal = demo.GOAL

    if page_state == "empty":
        accounts = []
        transactions = []
        goal = None

    context = {
        "accounts": accounts,
        "transactions": transactions,
        "goal": goal,
        "review_count": sum(
            1 for txn in transactions if txn.status == "needs_review"
        ),
        "page_state": page_state,
        "is_debug": settings.DEBUG,
    }
    return render(request, "dashboard.html", context)
