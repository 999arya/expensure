from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import UserCreationForm

User = get_user_model()


class SignupForm(UserCreationForm):
    """Username, optional email, and a password entered twice.

    Django's password validators run here, so a weak password is rejected
    before the user row is ever written.
    """

    email = forms.EmailField(
        required=False,
        label="Email (optional)",
        help_text="Only used to recover your account later.",
    )

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "email")

    def clean_email(self):
        email = (self.cleaned_data.get("email") or "").strip().lower()
        return email
