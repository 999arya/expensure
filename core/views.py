from django.shortcuts import render


def dashboard(request):
    # Fictional data for Phase 1. Replaced by core/demo.py in step 3.
    account = {
        "nickname": "Kotak Savings",
        "bank": "Kotak Mahindra Bank",
        "suffix": "4821",
        "balance_display": "₹24,500.00",
        "confirmed_at": "Today, 9:14 am",
        "captured_at": "Today, 11:02 am",
    }
    return render(request, "dashboard.html", {"account": account})
