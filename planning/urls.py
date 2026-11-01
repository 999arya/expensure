from django.urls import path

from . import views

app_name = "planning"

urlpatterns = [
    path("", views.plan_overview, name="plan"),
    path("goals/", views.goal_list, name="goals"),
    path("goals/add/", views.GoalCreateView.as_view(), name="goal_add"),
    path("goals/<int:pk>/edit/", views.GoalUpdateView.as_view(), name="goal_edit"),
    path("goals/<int:pk>/add/", views.goal_contribute, name="goal_contribute"),
    path("income/add/", views.IncomeCreateView.as_view(), name="income_add"),
    path("bills/add/", views.BillCreateView.as_view(), name="bill_add"),
    path("income/<int:pk>/settle/", views.income_settle, name="income_settle"),
    path("bills/<int:pk>/settle/", views.bill_settle, name="bill_settle"),
    path("monthly-goal/", views.monthly_goal_edit, name="monthly_goal"),
    path("accounts/<int:account_pk>/constraints/", views.account_goal_edit, name="account_goal"),
]
