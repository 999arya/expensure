from django import forms

from accounts.models import BankAccount
from ingestion.parsers.statement_csv import MAX_STATEMENT_BYTES

from .services import normalize_bank


class StatementUploadForm(forms.Form):
    account = forms.ModelChoiceField(
        label="Account",
        queryset=BankAccount.objects.none(),
    )
    statement = forms.FileField(
        label="CSV statement",
        help_text="Kotak or Bank of Baroda CSV export. The file is read in memory and not stored.",
        widget=forms.ClearableFileInput(attrs={"accept": ".csv,text/csv"}),
    )

    def __init__(self, *args, owner, **kwargs):
        self.owner = owner
        super().__init__(*args, **kwargs)
        accounts = BankAccount.objects.filter(owner=owner, is_active=True)
        self.fields["account"].queryset = accounts
        if accounts.count() == 1:
            self.fields["account"].initial = accounts.first().pk

    def clean_account(self):
        account = self.cleaned_data["account"]
        if not normalize_bank(account.bank):
            raise forms.ValidationError(
                "Statement import currently supports Kotak and Bank of Baroda accounts."
            )
        return account

    def clean_statement(self):
        upload = self.cleaned_data["statement"]
        if not upload.name.lower().endswith(".csv"):
            raise forms.ValidationError("Upload the CSV export, not a PDF or spreadsheet.")
        if upload.size > MAX_STATEMENT_BYTES:
            raise forms.ValidationError("Keep the statement under 2 MB.")
        return upload
