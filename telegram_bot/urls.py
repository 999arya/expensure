from django.urls import path
from . import views, webhook

app_name = "telegram_bot"

urlpatterns = [
    # Webhook
    path("webhook/", webhook.TelegramWebhookView.as_view(), name="webhook"),

    # OIDC Login flow
    path("login/", views.TelegramLoginView.as_view(), name="login"),
    path("callback/", views.TelegramCallbackView.as_view(), name="callback"),
    path("complete/", views.TelegramCompleteView.as_view(), name="complete"),

    # Account settings
    path("settings/", views.telegram_settings, name="settings"),
    path("unlink/", views.telegram_unlink, name="unlink"),
]