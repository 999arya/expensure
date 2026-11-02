from datetime import timedelta

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import UserCreationForm
from django.db import transaction
from django.utils import timezone

from core.forms import RupeeField

from .models import BalanceCheckpoint, BankAccount

User = get_user_model()

DATETIME_FORMAT = "%Y-%m-%dT%H:%M"


class SignupForm(UserCreationForm):
    """Personal details and a password entered twice.

    Django's password validators run here, so a weak password is rejected
    before the user row is ever written.
    """

    first_name = forms.CharField(max_length=150, label="First name")
    last_name = forms.CharField(max_length=150, required=False, label="Last name (optional)")
    email = forms.EmailField(
        required=True,
        label="Email",
        help_text="Only used to recover your account later.",
    )

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("first_name", "last_name", "email", "username")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["username"].widget.attrs.pop("autofocus", None)
        self.fields["first_name"].widget.attrs.update(autofocus=True, autocomplete="given-name")
        self.fields["last_name"].widget.attrs["autocomplete"] = "family-name"
        self.fields["email"].widget.attrs["autocomplete"] = "email"

    def clean_email(self):
        email = (self.cleaned_data.get("email") or "").strip().lower()
        return email


class BankAccountForm(forms.ModelForm):
    """Add an account together with the balance the user sees right now.

    The balance becomes a BalanceCheckpoint, which is the anchor every later
    estimate is built from.
    """

    opening_balance = RupeeField(
        label="Balance right now",
        help_text=(
            "What the bank shows today. This is your starting point; "
            "everything you record after it adjusts the estimate."
        ),
        allow_negative=True,
    )

    class Meta:
        model = BankAccount
        fields = ("bank", "nickname", "account_type", "account_suffix")
        labels = {
            "bank": "Bank name",
            "account_suffix": "Last 4 digits",
            "account_type": "Account type",
        }
        help_texts = {
            "bank": "Enter your bank's name, for example HDFC Bank or State Bank of India.",
            "account_suffix": "Only the last four digits. Never the full number.",
            "nickname": "Something you will recognise, like “Kotak salary”.",
        }
        widgets = {
            "bank": forms.TextInput(attrs={"placeholder": "Enter your bank name", "autocomplete": "off"}),
            "account_suffix": forms.TextInput(
                attrs={
                    "inputmode": "numeric",
                    "maxlength": 4,
                    "autocomplete": "off",
                    "placeholder": "1234",
                }
            ),
            "nickname": forms.TextInput(attrs={"autocomplete": "off"}),
        }

    def __init__(self, *args, owner, **kwargs):
        self.owner = owner
        super().__init__(*args, **kwargs)

    def clean_bank(self):
        name = " ".join(self.cleaned_data["bank"].split())
        if not name:
            raise forms.ValidationError("Enter your bank's name.")
        # Keep supported CSV import adapters compatible with both old and new accounts.
        aliases = {
            "kotak": "kotak", "kotak bank": "kotak", "kotak mahindra": "kotak",
            "kotak mahindra bank": "kotak", "bob": "bob", "bank of baroda": "bob",
        }
        return aliases.get(name.casefold(), name)

    def clean(self):
        data = super().clean()
        bank = data.get("bank")
        account_type = data.get("account_type")
        suffix = data.get("account_suffix")

        if bank and account_type and suffix:
            already_added = BankAccount.objects.filter(
                owner=self.owner,
                bank__iexact=bank,
                account_type=account_type,
                account_suffix=suffix,
            ).exists()
            if already_added:
                raise forms.ValidationError(
                    "You have already added this account."
                )

        return data

    def save(self, commit=True):
        self.instance.owner = self.owner

        if not commit:
            return super().save(commit=False)

        with transaction.atomic():
            account = super().save(commit=True)
            BalanceCheckpoint.objects.create(
                account=account,
                amount_paise=self.cleaned_data["opening_balance"],
                effective_at=timezone.now(),
                source=BalanceCheckpoint.Source.MANUAL,
                note="Balance entered when the account was added.",
            )

        return account


class ConfirmBalanceForm(forms.Form):
    """The balance the bank shows right now.

    Saving this becomes a new checkpoint. Everything recorded after it adjusts
    the estimate, and everything recorded before it is treated as already
    included in the figure you just typed.
    """

    amount = RupeeField(
        label="Balance the bank shows",
        help_text="Copy it from your bank app. Negative is allowed if the account is overdrawn.",
        allow_negative=True,
    )
    effective_at = forms.DateTimeField(
        label="As of",
        input_formats=[
            "%Y-%m-%dT%H:%M",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y-%m-%d %H:%M:%S",
        ],
        widget=forms.DateTimeInput(
            attrs={"type": "datetime-local"},
            format=DATETIME_FORMAT,
        ),
        help_text="When this balance was true at the bank. Usually now.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.is_bound:
            self.fields["effective_at"].initial = timezone.localtime().replace(
                second=0, microsecond=0
            )

    def clean_effective_at(self):
        value = self.cleaned_data["effective_at"]
        # A balance cannot be known before it happens. A few minutes of grace
        # absorbs small clock differences between the phone and the server.
        if value > timezone.now() + timedelta(minutes=5):
            raise forms.ValidationError(
                "Choose a time in the past or the present."
            )
        return value


class DeleteBankAccountForm(forms.Form):
    """Delete one bank account after typing its last four digits."""

    confirm_suffix = forms.CharField(
        label="Type the last four digits to confirm",
        max_length=4,
        widget=forms.TextInput(
            attrs={
                "inputmode": "numeric",
                "autocomplete": "off",
                "placeholder": "1234",
            }
        ),
    )

    def __init__(self, *args, account, **kwargs):
        self.account = account
        super().__init__(*args, **kwargs)

    def clean_confirm_suffix(self):
        value = (self.cleaned_data.get("confirm_suffix") or "").strip()
        if value != self.account.account_suffix:
            raise forms.ValidationError(
                "That does not match the last four digits of this account."
            )
        return value

