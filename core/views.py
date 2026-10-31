from django.shortcuts import render

from . import demo


def dashboard(request):
    return render(
        request,
        "dashboard.html",
        {
            "accounts": demo.ACCOUNTS,
            "transactions": demo.TRANSACTIONS,
            "goal": demo.GOAL,
            "review_count": demo.review_count(),
        },
    )
