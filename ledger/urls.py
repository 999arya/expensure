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
    path("transfers/", views.transfer_list_view, name="transfers"),
    path("transfers/add/", views.AddTransferView.as_view(), name="transfer_add"),
]
