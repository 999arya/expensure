from django.urls import path

from . import views

app_name = "ingestion"

urlpatterns = [
    path("", views.upload, name="upload"),
    path("history/", views.history, name="history"),
    path("<int:pk>/", views.batch_detail, name="batch_detail"),
    path("<int:pk>/preview/", views.preview, name="preview"),
    path("<int:pk>/confirm/", views.confirm, name="confirm"),
    path("<int:pk>/cancel/", views.cancel, name="cancel"),
    path("<int:pk>/undo/", views.undo, name="undo"),
]
