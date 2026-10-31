from django import forms
from django.utils import timezone

from accounts.models import BankAccount
from core.forms import RupeeField

from .models import Category, Transaction

DATETIME_FORMAT = "%Y-%m-%dT%H:%M"


class TransactionForm(forms.ModelForm):
    """Record one movement of money by hand.

    The account and category dropdowns are limited to the signed-in user's own
    rows, so a tampered POST cannot attach a transaction to someone else's
    account.
    """

    amount = RupeeField(label="Amount")
    direction = forms.ChoiceField(
        label="Direction",
        choices=Transaction.Direction.choices,
        widget=forms.RadioSelect(attrs={"class": "choice-list"}),
    )
    occurred_at = forms.DateTimeField(
        label="When it happened",
        input_formats=[
            "%Y-%m-%dT%H:%M",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d",
        ],
        widget=forms.DateTimeInput(
            attrs={"type": "datetime-local"},
            format=DATETIME_FORMAT,
        ),
    )

    class Meta:
        model = Transaction
        fields = (
            "account",
            "occurred_at",
            "direction",
            "category",
            "description",
            "reference",
        )
        labels = {
            "description": "What was it for",
            "reference": "Reference (optional)",
        }
        widgets = {
            "description": forms.TextInput(
                attrs={"autocomplete": "off", "placeholder": "Swiggy, salary, rent…"}
            ),
            "reference": forms.TextInput(
                attrs={"autocomplete": "off", "placeholder": "UPI reference, if any"}
            ),
        }

    def __init__(self, *args, owner, **kwargs):
        self.owner = owner
        super().__init__(*args, **kwargs)

        accounts = list(
            BankAccount.objects.filter(owner=owner, is_active=True)[:2]
        )
        self.fields["account"].queryset = BankAccount.objects.filter(
            owner=owner, is_active=True
        )
        # With one account there is nothing to choose, so do not make the
        # person open a dropdown that has a single real answer.
        if len(accounts) == 1:
            self.fields["account"].initial = accounts[0].pk
            self.fields["account"].empty_label = None

        self.fields["category"].queryset = Category.objects.filter(owner=owner)
        self.fields["category"].empty_label = "No category"
        self.fields["category"].required = False

        self.order_fields(
            [
                "account",
                "occurred_at",
                "amount",
                "direction",
                "category",
                "description",
                "reference",
            ]
        )

        if not self.is_bound:
            self.fields["occurred_at"].initial = timezone.localtime().replace(
                second=0, microsecond=0
            )
            self.fields["direction"].initial = Transaction.Direction.DEBIT

    def clean_amount(self):
        amount = self.cleaned_data["amount"]
        if amount <= 0:
            raise forms.ValidationError("Enter an amount greater than zero.")
        return amount

    def clean(self):
        data = super().clean()
        account = data.get("account")
        category = data.get("category")

        if account and account.owner_id != self.owner.pk:
            raise forms.ValidationError("Choose one of your own accounts.")
        if category and category.owner_id != self.owner.pk:
            raise forms.ValidationError("Choose one of your own categories.")

        return data

    def save(self, commit=True):
        transaction = super().save(commit=False)
        transaction.amount_paise = self.cleaned_data["amount"]
        transaction.source = Transaction.Source.MANUAL
        transaction.status = Transaction.Status.POSTED

        if commit:
            transaction.save()

        return transaction
