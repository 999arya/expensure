from django.conf import settings
from django.core.validators import RegexValidator
from django.db import models


class BankAccount(models.Model):
    """An account the user owns, identified by bank and last four digits.

    Only the masked suffix is stored. Never a full account number.
    """

    class Bank(models.TextChoices):
        KOTAK = "kotak", "Kotak Mahindra Bank"
        BANK_OF_BARODA = "bob", "Bank of Baroda"
        OTHER = "other", "Other bank"

    class AccountType(models.TextChoices):
        SAVINGS = "savings", "Savings"
        CURRENT = "current", "Current"

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="bank_accounts",
    )
    bank = models.CharField(max_length=100)
    nickname = models.CharField(max_length=64)
    account_suffix = models.CharField(
        max_length=4,
        validators=[
            RegexValidator(r"^\d{4}$", "Enter the last four digits of the account.")
        ],
    )
    account_type = models.CharField(
        max_length=8,
        choices=AccountType.choices,
        default=AccountType.SAVINGS,
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["bank", "nickname"]
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "bank", "account_type", "account_suffix"],
                name="unique_account_per_owner",
            )
        ]

    def get_bank_display(self):
        # Preserve labels for existing accounts stored with legacy bank codes.
        return dict(self.Bank.choices).get(self.bank, self.bank)

    def __str__(self):
        return f"{self.nickname} ····{self.account_suffix}"


class BalanceCheckpoint(models.Model):
    """A balance the user confirmed at a specific time.

    `effective_at` is when the balance was true at the bank. `recorded_at` is
    when we learned about it. Keeping both lets a late message be recognised as
    ambiguous instead of silently moving the estimate.
    """

    class Source(models.TextChoices):
        MANUAL = "manual", "Entered by you"
        SMS = "sms", "Bank SMS"
        IMPORT = "import", "Imported"

    account = models.ForeignKey(
        BankAccount,
        on_delete=models.CASCADE,
        related_name="checkpoints",
    )
    amount_paise = models.BigIntegerField()
    effective_at = models.DateTimeField()
    recorded_at = models.DateTimeField(auto_now_add=True)
    source = models.CharField(
        max_length=8,
        choices=Source.choices,
        default=Source.MANUAL,
    )
    note = models.CharField(
        max_length=200,
        blank=True,
        help_text="What this balance is known to include, if anything.",
    )

    class Meta:
        ordering = ["-effective_at", "-id"]
        indexes = [models.Index(fields=["account", "-effective_at"])]

    def __str__(self):
        return f"{self.account} · {self.effective_at:%d %b %Y, %H:%M}"
