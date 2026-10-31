from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.paginator import Paginator
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.views.generic import CreateView

from accounts.models import BankAccount

from .filters import apply_filters, read_filters
from .forms import TransactionForm
from .models import Transaction


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
        messages.success(self.request, "Transaction recorded.")
        return response
