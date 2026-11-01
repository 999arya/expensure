from django.urls import path

from . import views

app_name = "ledger"

urlpatterns = [
    path("", views.transaction_list, name="list"),
    path("add/", views.AddTransactionView.as_view(), name="add"),
    path("review/", views.transaction_review, name="review"),
    path(
        "review/<int:pk>/decide/",
        views.review_decide,
        name="review_decide",
    ),
]
