import hashlib
import secrets
from django.conf import settings
from django.db import models
from django.utils import timezone


class DeviceToken(models.Model):
    """An authentication token scoped to an iPhone or capture device.

    Only the SHA-256 hash of the token is stored in the database.
    The plain token is shown once when generated.
    """

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="device_tokens",
    )
    name = models.CharField(max_length=64, help_text="e.g. Arun's iPhone 15")
    token_hash = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        status = "revoked" if self.revoked_at else "active"
        return f"{self.name} ({status})"

    @property
    def is_active(self):
        return self.revoked_at is None

    def revoke(self):
        self.revoked_at = timezone.now()
        self.save(update_fields=["revoked_at"])

    @classmethod
    def create_token(cls, owner, name: str) -> tuple["DeviceToken", str]:
        """Generate a cryptographically random token and return (DeviceToken, plain_token)."""
        raw_token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        token_obj = cls.objects.create(
            owner=owner,
            name=name,
            token_hash=token_hash,
        )
        return token_obj, raw_token

    @classmethod
    def verify_token(cls, raw_token: str) -> "DeviceToken | None":
        if not raw_token:
            return None
        token_hash = hashlib.sha256(raw_token.strip().encode("utf-8")).hexdigest()
        token_obj = cls.objects.filter(token_hash=token_hash, revoked_at__isnull=True, owner__is_active=True).select_related("owner").first()
        if token_obj:
            token_obj.last_used_at = timezone.now()
            token_obj.save(update_fields=["last_used_at"])
            return token_obj
        return None


class IngestionEvent(models.Model):
    """An audit log of a capture or parse attempt.

    CRITICAL PRIVACY RULE (Decision 006):
    Raw SMS message bodies are NEVER saved in the database or logs.
    Only extracted metadata, request ID, and outcome are recorded.
    """

    class Outcome(models.TextChoices):
        SUCCESS = "success", "Captured successfully"
        REJECTED_OTP = "rejected_otp", "Rejected OTP / Sensitive"
        REJECTED_UNSUPPORTED = "unsupported", "Unsupported format"
        DUPLICATE = "duplicate", "Duplicate transaction"
        AMBIGUOUS_ACCOUNT = "ambiguous_account", "Ambiguous account"
        ERROR = "error", "Processing error"

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="ingestion_events",
    )
    device_token = models.ForeignKey(
        DeviceToken,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="events",
    )
    request_id = models.CharField(max_length=36)
    parser_version = models.CharField(max_length=16, default="1.0.0")
    outcome = models.CharField(max_length=24, choices=Outcome.choices)
    bank = models.CharField(max_length=16, blank=True)
    account_suffix = models.CharField(max_length=4, blank=True)
    direction = models.CharField(max_length=8, blank=True)
    amount_paise = models.BigIntegerField(null=True, blank=True)
    fingerprint = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"Event {self.request_id[:8]} · {self.outcome}"


class CaptureRequest(models.Model):
    """Durable retry receipt. Never contains SMS bodies or bearer tokens."""
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    request_id = models.UUIDField()
    payload_hash = models.CharField(max_length=64)
    result = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["owner", "request_id"], name="capture_request_owner_id")]
        indexes = [models.Index(fields=["owner", "created_at"])]
