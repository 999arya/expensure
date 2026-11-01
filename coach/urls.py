from django.urls import path
from . import views

app_name = "coach"

urlpatterns = [
    path("", views.coach_home, name="home"),
    path("goals/<int:pk>/", views.goal_plan, name="goal_plan"),
    path("afford/", views.affordability_check, name="afford"),
    path("insights/<int:pk>/accept/", views.insight_accept, name="insight_accept"),
    path("insights/<int:pk>/dismiss/", views.insight_dismiss, name="insight_dismiss"),
]
