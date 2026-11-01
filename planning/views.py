from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.views.generic import CreateView, UpdateView

from accounts.models import BankAccount

from .forms import (
    AccountGoalForm,
    BillForm,
    ContributeForm,
    GoalForm,
    IncomePlanForm,
    MonthlyGoalForm,
)
from .models import AccountGoal, Bill, Goal, IncomePlan, MonthlyGoal, month_bounds
from .services import (
    add_to_goal,
    allowance_summary,
    primary_goal,
    settle_bill,
    settle_income,
)


class AccountsRequiredMixin:
    """Send the user to add an account before planning movements."""

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not BankAccount.objects.filter(
            owner=request.user, is_active=True
        ).exists():
            messages.info(request, "Add an account before planning this month.")
            return redirect("accounts:add")
        return super().dispatch(request, *args, **kwargs)


@login_required
def plan_overview(request):
    """This month's allowance, goals and planned movements."""
    user = request.user
    today = timezone.localdate()
    first_day, _ = month_bounds(today)
    income = IncomePlan.objects.filter(owner=user).select_related("account")
    bills = Bill.objects.filter(owner=user).select_related("account")
    accounts = BankAccount.objects.filter(owner=user, is_active=True).select_related("goal")
    monthly_goal = MonthlyGoal.objects.filter(owner=user, month=first_day).first()

    context = {
        "allowance": allowance_summary(user, today=today),
        "goal": primary_goal(user),
        "goals": Goal.objects.filter(owner=user),
        "income_items": income,
        "bill_items": bills,
        "open_income": income.filter(status=IncomePlan.Status.EXPECTED).count(),
        "open_bills": bills.filter(status=Bill.Status.UNPAID).count(),
        "accounts": accounts,
        "monthly_goal": monthly_goal,
    }
    return render(request, "planning/plan.html", context)


@login_required
def goal_list(request):
    goals = list(Goal.objects.filter(owner=request.user))
    context = {
        "goals": goals,
        "open_goals": [goal for goal in goals if not goal.is_complete],
        "completed_goals": [goal for goal in goals if goal.is_complete],
    }
    return render(request, "planning/goal_list.html", context)


class GoalCreateView(LoginRequiredMixin, CreateView):
    form_class = GoalForm
    template_name = "planning/goal_form.html"
    success_url = reverse_lazy("planning:goals")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["owner"] = self.request.user
        return kwargs

    def get_success_url(self):
        from django.urls import reverse
        return reverse("coach:goal_plan", args=[self.object.pk])

    def form_valid(self, form):
        messages.success(self.request, "Goal saved. Here are your next steps.")
        return super().form_valid(form)


class GoalUpdateView(LoginRequiredMixin, UpdateView):
    form_class = GoalForm
    template_name = "planning/goal_form.html"
    success_url = reverse_lazy("planning:goals")

    def get_queryset(self):
        return Goal.objects.filter(owner=self.request.user)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["owner"] = self.request.user
        return kwargs

    def form_valid(self, form):
        messages.success(self.request, "Goal updated.")
        return super().form_valid(form)


@require_POST
@login_required
def goal_contribute(request, pk):
    goal = get_object_or_404(Goal, pk=pk, owner=request.user)
    form = ContributeForm(request.POST)

    if not form.is_valid():
        messages.error(request, "Enter an amount greater than zero to add to the goal.")
        return redirect("planning:goals")

    add_to_goal(goal, form.cleaned_data["amount"])
    messages.success(request, f"Added to {goal.name}.")
    return redirect("planning:goals")


class IncomeCreateView(LoginRequiredMixin, AccountsRequiredMixin, CreateView):
    form_class = IncomePlanForm
    template_name = "planning/item_form.html"
    success_url = reverse_lazy("planning:plan")
    extra_context = {
        "eyebrow": "MONEY STILL TO COME",
        "title": "Add expected income",
        "submit_label": "Save expected income",
        "cancel_url": "planning:plan",
    }

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["owner"] = self.request.user
        return kwargs

    def form_valid(self, form):
        messages.success(
            self.request,
            "Expected income saved. It counts toward your allowance until it arrives.",
        )
        return super().form_valid(form)


class BillCreateView(LoginRequiredMixin, AccountsRequiredMixin, CreateView):
    form_class = BillForm
    template_name = "planning/item_form.html"
    success_url = reverse_lazy("planning:plan")
    extra_context = {
        "eyebrow": "MONEY STILL TO GO OUT",
        "title": "Add a bill",
        "submit_label": "Save bill",
        "cancel_url": "planning:plan",
    }

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["owner"] = self.request.user
        return kwargs

    def form_valid(self, form):
        messages.success(
            self.request,
            "Bill saved. It is subtracted from your allowance until you pay it.",
        )
        return super().form_valid(form)


@require_POST
@login_required
def income_settle(request, pk):
    item = get_object_or_404(
        IncomePlan, pk=pk, owner=request.user, status=IncomePlan.Status.EXPECTED
    )
    txn = settle_income(item, request.user)

    if txn.status == txn.Status.NEEDS_REVIEW:
        messages.info(
            request,
            "Recorded, but it falls before your confirmed balance. "
            "Decide it in the review queue.",
        )
    else:
        messages.success(request, "Income received. The movement is in your account.")
    return redirect("planning:plan")


@require_POST
@login_required
def bill_settle(request, pk):
    item = get_object_or_404(
        Bill, pk=pk, owner=request.user, status=Bill.Status.UNPAID
    )
    txn = settle_bill(item, request.user)

    if txn.status == txn.Status.NEEDS_REVIEW:
        messages.info(
            request,
            "Recorded, but it falls before your confirmed balance. "
            "Decide it in the review queue.",
        )
    else:
        messages.success(request, "Bill paid. The movement is in your account.")
    return redirect("planning:plan")


@login_required
def monthly_goal_edit(request):
    today = timezone.localdate()
    first_day, _ = month_bounds(today)
    goal, _ = MonthlyGoal.objects.get_or_create(owner=request.user, month=first_day)
    if request.method == "POST":
        form = MonthlyGoalForm(request.POST, instance=goal, owner=request.user, month=first_day)
        if form.is_valid():
            form.save()
            messages.success(request, "Monthly savings target saved.")
            return redirect("planning:plan")
    else:
        form = MonthlyGoalForm(instance=goal, owner=request.user, month=first_day)
    return render(
        request,
        "planning/monthly_goal_form.html",
        {"form": form, "month": first_day},
    )


@login_required
def account_goal_edit(request, account_pk):
    account = get_object_or_404(
        BankAccount, pk=account_pk, owner=request.user, is_active=True
    )
    goal, _ = AccountGoal.objects.get_or_create(account=account)
    if request.method == "POST":
        form = AccountGoalForm(request.POST, instance=goal, account=account)
        if form.is_valid():
            form.save()
            messages.success(request, f"Constraints saved for {account.nickname}.")
            return redirect("planning:plan")
    else:
        form = AccountGoalForm(instance=goal, account=account)
    return render(
        request,
        "planning/account_goal_form.html",
        {"form": form, "account": account},
    )
