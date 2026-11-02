from django.contrib.auth import views as auth_views
from django.urls import path, reverse_lazy

from . import views

app_name = "accounts"

urlpatterns = [
    path("signup/", views.signup, name="signup"),
    path(
        "login/",
        auth_views.LoginView.as_view(template_name="accounts/login.html"),
        name="login",
    ),
    path("password-reset/", views.ConfiguredPasswordResetView.as_view(template_name="accounts/password_reset_form.html", email_template_name="accounts/password_reset_email.txt", subject_template_name="accounts/password_reset_subject.txt", success_url=reverse_lazy("accounts:password_reset_done")), name="password_reset"),
    path("password-reset/sent/", auth_views.PasswordResetDoneView.as_view(template_name="accounts/password_reset_done.html"), name="password_reset_done"),
    path("reset/<uidb64>/<token>/", auth_views.PasswordResetConfirmView.as_view(template_name="accounts/password_reset_confirm.html", success_url=reverse_lazy("accounts:password_reset_complete")), name="password_reset_confirm"),
    path("reset/complete/", auth_views.PasswordResetCompleteView.as_view(template_name="accounts/password_reset_complete.html"), name="password_reset_complete"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("", views.account_list, name="list"),
    path("add/", views.AddAccountView.as_view(), name="add"),
    path("<int:pk>/confirm/", views.ConfirmBalanceView.as_view(), name="confirm"),
    path("<int:pk>/delete/", views.delete_bank_account, name="delete_bank"),
    path("<int:pk>/", views.account_detail, name="detail"),
    path("export/", views.export_user_data, name="export"),
    path("delete/", views.delete_account, name="delete"),
    path("privacy/", views.privacy_info, name="privacy"),
]
