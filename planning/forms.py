from django import forms
from django.utils import timezone

from accounts.models import BankAccount
from core.forms import RupeeField

from .models import AccountGoal, Bill, Goal, IncomePlan, MonthlyGoal


def _rupees_text(paise):
    """Exact decimal text for a form initial value, never a float."""
    sign = "-" if paise < 0 else ""
    rupees, remainder = divmod(abs(paise), 100)
    return f"{sign}{rupees}.{remainder:02d}"


class GoalForm(forms.ModelForm):
    """Create or edit a goal. Both amounts are typed as rupees."""

    target = RupeeField(label="Target amount")
    saved = RupeeField(
        label="Already saved",
        help_text="Type 0 if you have not set anything aside yet.",
    )

    class Meta:
        model = Goal
        fields = ("name", "target_date")
        labels = {
            "name": "What are you saving for",
            "target_date": "Target date (optional)",
        }
        widgets = {
            "name": forms.TextInput(
                attrs={"autocomplete": "off", "placeholder": "Emergency fund"}
            ),
            "target_date": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, owner, **kwargs):
        self.owner = owner
        super().__init__(*args, **kwargs)
        if not self.is_bound:
            if self.instance.pk:
                self.fields["target"].initial = _rupees_text(self.instance.target_paise)
                self.fields["saved"].initial = _rupees_text(self.instance.saved_paise)
            else:
                self.fields["saved"].initial = "0"

    def clean_target(self):
        target = self.cleaned_data["target"]
        if target <= 0:
            raise forms.ValidationError("Enter a target greater than zero.")
        return target

    def clean_saved(self):
        saved = self.cleaned_data["saved"]
        if saved < 0:
            raise forms.ValidationError("Saved cannot be negative.")
        return saved

    def save(self, commit=True):
        goal = super().save(commit=False)
        goal.owner = self.owner
        goal.target_paise = self.cleaned_data["target"]
        goal.saved_paise = self.cleaned_data["saved"]
        if commit:
            goal.save()
        return goal


class ContributeForm(forms.Form):
    """Add money to a goal."""

    amount = RupeeField(label="Add to this goal")

    def clean_amount(self):
        amount = self.cleaned_data["amount"]
        if amount <= 0:
            raise forms.ValidationError("Enter an amount greater than zero.")
        return amount


class PlannedItemForm(forms.ModelForm):
    """Shared fields for expected income and bills."""

    amount = RupeeField(label="Amount")

    def __init__(self, *args, owner, **kwargs):
        self.owner = owner
        super().__init__(*args, **kwargs)
        accounts = BankAccount.objects.filter(owner=owner, is_active=True)
        self.fields["account"].queryset = accounts
        self.fields["account"].empty_label = None
        if not self.is_bound:
            first = accounts.first()
            if first is not None:
                self.fields["account"].initial = first.pk

    def clean_amount(self):
        amount = self.cleaned_data["amount"]
        if amount <= 0:
            raise forms.ValidationError("Enter an amount greater than zero.")
        return amount

    def clean_account(self):
        account = self.cleaned_data["account"]
        if account is not None and account.owner_id != self.owner.pk:
            raise forms.ValidationError("Choose one of your own accounts.")
        return account

    def save(self, commit=True):
        item = super().save(commit=False)
        item.owner = self.owner
        item.amount_paise = self.cleaned_data["amount"]
        if commit:
            item.save()
        return item


class IncomePlanForm(PlannedItemForm):
    class Meta:
        model = IncomePlan
        fields = ("description", "amount", "expected_on", "account")
        labels = {
            "description": "What is coming in",
            "expected_on": "Expected on",
        }
        widgets = {
            "description": forms.TextInput(
                attrs={"autocomplete": "off", "placeholder": "Salary, client payment…"}
            ),
            "expected_on": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.is_bound:
            self.fields["expected_on"].initial = timezone.localdate()


class BillForm(PlannedItemForm):
    class Meta:
        model = Bill
        fields = ("description", "amount", "due_on", "account")
        labels = {
            "description": "What is the bill for",
            "due_on": "Due on",
        }
        widgets = {
            "description": forms.TextInput(
                attrs={"autocomplete": "off", "placeholder": "Electricity, rent…"}
            ),
            "due_on": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.is_bound:
            self.fields["due_on"].initial = timezone.localdate()


class MonthlyGoalForm(forms.ModelForm):
    """Set the monthly savings target G for a month."""

    savings = RupeeField(
        label="Desired savings this month",
        help_text="Target amount to save over and above your opening balance.",
    )

    class Meta:
        model = MonthlyGoal
        fields = ("savings",)

    def __init__(self, *args, owner, month, **kwargs):
        self.owner = owner
        self.month = month
        super().__init__(*args, **kwargs)
        if not self.is_bound:
            if self.instance.pk:
                self.fields["savings"].initial = _rupees_text(self.instance.savings_paise)
            else:
                self.fields["savings"].initial = "0"

    def clean_savings(self):
        savings = self.cleaned_data["savings"]
        if savings < 0:
            raise forms.ValidationError("Savings target cannot be negative.")
        return savings

    def save(self, commit=True):
        goal = super().save(commit=False)
        goal.owner = self.owner
        goal.month = self.month
        goal.savings_paise = self.cleaned_data["savings"]
        if commit:
            goal.save()
        return goal


class AccountGoalForm(forms.ModelForm):
    """Set the always-on minimum floor F and optional end-of-month target T."""

    minimum_floor = RupeeField(
        label="Minimum balance floor",
        help_text="The balance you never want this account to drop below.",
    )
    target = RupeeField(
        label="End-of-month target (optional)",
        required=False,
        help_text="Leave blank to default to your minimum balance floor.",
    )

    class Meta:
        model = AccountGoal
        fields = ("minimum_floor", "target")

    def __init__(self, *args, account, **kwargs):
        self.account = account
        super().__init__(*args, **kwargs)
        if not self.is_bound:
            if self.instance.pk:
                self.fields["minimum_floor"].initial = _rupees_text(
                    self.instance.minimum_floor_paise
                )
                if self.instance.target_paise is not None:
                    self.fields["target"].initial = _rupees_text(
                        self.instance.target_paise
                    )
            else:
                self.fields["minimum_floor"].initial = "0"

    def clean_minimum_floor(self):
        floor = self.cleaned_data["minimum_floor"]
        if floor < 0:
            raise forms.ValidationError("Minimum floor cannot be negative.")
        return floor

    def clean_target(self):
        target = self.cleaned_data.get("target")
        if target is not None and target < 0:
            raise forms.ValidationError("Target balance cannot be negative.")
        return target

    def clean(self):
        cleaned_data = super().clean()
        floor = cleaned_data.get("minimum_floor")
        target = cleaned_data.get("target")
        if floor is not None and target is not None and target < floor:
            self.add_error("target", "Target cannot be less than the minimum balance floor.")
        return cleaned_data

    def save(self, commit=True):
        ag = super().save(commit=False)
        ag.account = self.account
        ag.minimum_floor_paise = self.cleaned_data["minimum_floor"]
        ag.target_paise = self.cleaned_data.get("target")
        if commit:
            ag.save()
        return ag
