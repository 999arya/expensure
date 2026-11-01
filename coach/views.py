from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST, require_http_methods

from core.parsing import parse_inr
from planning.services import allowance_summary

from .models import FinancialInsight
from .services import affordability_insight


@login_required
def coach_home(request):
    insights = FinancialInsight.objects.filter(owner=request.user, status=FinancialInsight.Status.PENDING)
    past_insights = FinancialInsight.objects.filter(owner=request.user).exclude(status=FinancialInsight.Status.PENDING)[:10]
    allowance = allowance_summary(request.user)

    context = {
        "insights": insights,
        "goals": request.user.goals.filter(is_active=True),
        "past_insights": past_insights,
        "allowance": allowance,
    }
    return render(request, "coach/coach.html", context)


@login_required
def affordability_check(request):
    """Handle 'Can I afford ₹X?' queries."""
    if request.method != "POST":
        return redirect("coach:home")

    amount_str = request.POST.get("amount", "").strip()
    try:
        amount_paise = parse_inr(amount_str)
        if amount_paise <= 0:
            raise ValueError
    except (ValueError, TypeError):
        messages.error(request, "Enter a valid positive rupee amount (e.g. 4,000 or 1500.50).")
        return redirect("coach:home")

    if amount_paise > 9_000_000_000_000_000:
        messages.error(request, "Amount exceeds the supported range.")
        return redirect("coach:home")
    evaluation, insight = affordability_insight(request.user, amount_paise, request.POST.get("ai_consent") == "on")
    is_ai = insight.source == FinancialInsight.Source.AI
    coach_res = {"title": insight.title, "verdict": evaluation.verdict, "body": insight.body,
        "reason": evaluation.reason, "assumptions": evaluation.assumptions}

    return render(
        request,
        "coach/affordability_result.html",
        {
            "evaluation": evaluation,
            "insight": insight,
            "coach_response": coach_res,
            "is_ai": is_ai,
        },
    )


@require_POST
@login_required
def insight_accept(request, pk):
    insight = get_object_or_404(FinancialInsight, pk=pk, owner=request.user)
    insight.status = FinancialInsight.Status.ACCEPTED
    insight.save(update_fields=["status", "updated_at"])
    messages.success(request, f"Accepted insight: {insight.title}")
    return redirect("coach:home")


@require_POST
@login_required
def insight_dismiss(request, pk):
    insight = get_object_or_404(FinancialInsight, pk=pk, owner=request.user)
    insight.status = FinancialInsight.Status.DISMISSED
    insight.save(update_fields=["status", "updated_at"])
    messages.info(request, f"Dismissed insight: {insight.title}")
    return redirect("coach:home")


@login_required
@require_http_methods(["GET", "POST"])
def goal_plan(request, pk):
    from planning.models import Goal
    from .services import goal_guidance, goal_steps
    goal = get_object_or_404(Goal, pk=pk, owner=request.user)
    insight = None
    steps = goal_steps(goal)
    if request.method == "POST":
        insight = goal_guidance(goal, request.POST.get("ai_consent") == "on")
        steps = insight.body.splitlines()
    return render(request, "coach/goal_plan.html", {"goal": goal, "steps": steps, "insight": insight})
