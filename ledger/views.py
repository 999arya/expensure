from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.views.decorators.http import require_POST
from django.views.generic import CreateView

from accounts.models import BankAccount

from .filters import apply_filters, read_filters
from .forms import TransactionForm
from .models import Transaction
from .services import apply_review_decision, conflict_checkpoint, review_count, reviewable

REVIEW_APPROVE_MESSAGE = "Marked as already included in that balance."
REVIEW_IGNORE_MESSAGE = "Ignored. It stays out of your balance and reports."


@login_required
def transaction_list(request):
    filters = read_filters(request.GET, request.user)

    queryset = Transaction.objects.filter(
        account__owner=request.user
    ).select_related("account", "category")
    queryset = apply_filters(queryset, filters).order_by("-occurred_at", "-id")

    page = Paginator(queryset, 50).get_page(request.GET.get("page"))

    context = {
        "page_obj": page,
        "transactions": page.object_list,
        "filters": filters,
        "accounts": BankAccount.objects.filter(owner=request.user, is_active=True),
        "categories": request.user.categories.all(),
        "total_count": page.paginator.count,
        "review_count": review_count(request.user),
    }
    return render(request, "ledger/transaction_list.html", context)


class AddTransactionView(LoginRequiredMixin, CreateView):
    """Record one transaction by hand."""

    form_class = TransactionForm
    template_name = "ledger/add_transaction.html"
    success_url = reverse_lazy("ledger:list")

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not BankAccount.objects.filter(
            owner=request.user, is_active=True
        ).exists():
            messages.info(request, "Add an account before recording a transaction.")
            return redirect("accounts:add")
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["owner"] = self.request.user
        return kwargs

    def form_valid(self, form):
        response = super().form_valid(form)
        if self.object.status == Transaction.Status.NEEDS_REVIEW:
            messages.success(
                self.request,
                (
                    "Recorded. It falls before your last confirmed balance, so "
                    "it is waiting for your decision."
                ),
            )
        else:
            messages.success(self.request, "Transaction recorded.")
        return response


@login_required
def transaction_review(request):
    """Movements waiting for a review decision."""
    transactions = reviewable(request.user)

    context = {
        "transactions": transactions,
        "review_count": transactions.count(),
    }
    return render(request, "ledger/review.html", context)


@require_POST
@login_required
def review_decide(request, pk):
    """Approve or ignore one review-waiting transaction.

    Approve → posted: already inside the confirmed balance, estimate unchanged.
    Ignore → ignored: excluded from estimates and reports.
    """
    txn = get_object_or_404(
        Transaction.objects.filter(account__owner=request.user).select_related(
            "account", "category"
        ),
        pk=pk,
        status=Transaction.Status.NEEDS_REVIEW,
    )

    decision = request.POST.get("decision")
    if decision not in ("approve", "ignore"):
        messages.error(request, "Choose 'Already included' or 'Ignore'.")
        return redirect("ledger:review")

    txn.status = apply_review_decision(txn, decision)
    txn.save()

    if decision == "approve":
        messages.success(request, REVIEW_APPROVE_MESSAGE)
    else:
        messages.success(request, REVIEW_IGNORE_MESSAGE)

    return redirect("ledger:review")
