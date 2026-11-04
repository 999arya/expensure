from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("accounts.urls")),
    path("transactions/", include("ledger.urls")),
    path("plan/", include("planning.urls")),
    path("reports/", include("reports.urls")),
    path("statements/", include("ingestion.urls")),
    path("coach/", include("coach.urls")),
    path("telegram/", include("telegram_bot.urls")),
    path("", include("core.urls")),
]
