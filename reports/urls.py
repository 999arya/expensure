from django.urls import path

from . import views

app_name = "reports"

urlpatterns = [
    path("", views.report_view, name="overview"),
    path("api/data/", views.report_api_data, name="api_data"),
    path("export/csv/", views.export_csv, name="export_csv"),
    path("recurring/confirm/", views.recurring_confirm, name="recurring_confirm"),
    path("recurring/dismiss/", views.recurring_dismiss, name="recurring_dismiss"),
]
