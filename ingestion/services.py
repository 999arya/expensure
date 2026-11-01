"""Deterministic capture with durable retry receipts and conservative review."""
import hashlib
import uuid
from datetime import timedelta
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction as db_transaction
from django.utils import timezone
from django.utils.crypto import salted_hmac
from accounts.models import BankAccount
from ledger.models import Transaction
from .models import DeviceToken, IngestionEvent, CaptureRequest
from .parsers.bank_parsers import parse_sms_message


def find_matching_account(user, bank, suffix):
    accounts = list(BankAccount.objects.filter(owner=user, bank=bank, account_suffix=suffix, is_active=True)[:2])
    if len(accounts) == 1:
        return accounts[0], ""
    return None, "ambiguous_account" if accounts else "not_found"


def commit_parsed_transaction(user, parse_res, account=None, device_token=None, request_id=None):
    request_id = request_id or str(uuid.uuid4())
    def event(outcome):
        IngestionEvent.objects.create(owner=user, device_token=device_token, request_id=request_id,
            outcome=outcome, bank=parse_res.bank if parse_res.is_valid else "",
            account_suffix=parse_res.account_suffix if parse_res.is_valid else "",
            direction=parse_res.direction if parse_res.is_valid else "",
            amount_paise=parse_res.amount_paise if parse_res.is_valid else None,
            fingerprint=parse_res.fingerprint if parse_res.is_valid else "")
    if not parse_res.is_valid:
        outcome = "rejected_otp" if parse_res.is_otp else "unsupported"
        event(outcome)
        return None, outcome, parse_res.error
    if parse_res.direction not in ("debit", "credit") or not 0 < parse_res.amount_paise <= 9_000_000_000_000_000:
        event("error")
        return None, "error", "Invalid amount or direction."
    if account is None:
        account, error = find_matching_account(user, parse_res.bank, parse_res.account_suffix)
        if error:
            event("ambiguous_account" if error == "ambiguous_account" else "error")
            return None, error, "Choose the matching active bank account manually."
    if account.owner_id != user.pk:
        raise ValueError("Cannot record another user's transaction.")
    if not account.is_active or account.bank != parse_res.bank or account.account_suffix != parse_res.account_suffix:
        event("error")
        return None, "account_mismatch", "The selected account must match the parsed bank and last four digits."
    with db_transaction.atomic():
        BankAccount.objects.select_for_update().get(pk=account.pk)
        # A reliable reference is scoped to account and direction. No-reference
        # messages are separate review candidates, not merged by amount/day.
        reference = parse_res.reference[:64]
        if reference:
            existing = Transaction.objects.filter(account=account, reference=reference, direction=parse_res.direction).first()
            if existing:
                if existing.amount_paise != parse_res.amount_paise:
                    event("error")
                    return None, "reference_conflict", "This reference already has a different amount. Review it manually."
                event("duplicate")
                return None, "duplicate", "This transaction was already recorded."
        dedup_key = ("sms:" + hashlib.sha256(f"{parse_res.direction}:{reference}".encode()).hexdigest()) if reference else ""
        txn = Transaction.objects.create(account=account,
            occurred_at=parse_res.occurred_at or timezone.now(), received_at=timezone.now(),
            amount_paise=parse_res.amount_paise, direction=parse_res.direction,
            category=None, description="Bank message: confirm time and classification",
            reference=reference, status=Transaction.Status.NEEDS_REVIEW,
            source=Transaction.Source.SMS, dedup_key=dedup_key)
        event("success")
    return txn, "success", "Captured for review. Confirm the time and category before it affects balances or reports."


def ingest_sms_payload(user, message_text, device_token=None, request_id=None):
    req_id = request_id or str(uuid.uuid4())
    parsed = parse_sms_message(message_text)
    txn, outcome, msg = commit_parsed_transaction(user, parsed, device_token=device_token, request_id=req_id)
    result = {"status": outcome, "request_id": req_id}
    if txn:
        result.update(transaction_id=txn.pk, amount_paise=txn.amount_paise, direction=txn.direction,
            account=txn.account.nickname, review_status=txn.status, message=msg)
    else:
        result.update(error=msg, is_otp=parsed.is_otp)
    return result


def process_device_request(device, message, request_id):
    """Serialize per owner in Postgres; concurrent SQLite writes may retry with 503."""
    payload_hash = salted_hmac("capture-payload-v1", message, algorithm="sha256").hexdigest()
    with db_transaction.atomic():
        get_user_model().objects.select_for_update().get(pk=device.owner_id)
        if not DeviceToken.objects.filter(pk=device.pk, revoked_at__isnull=True, owner__is_active=True).exists():
            return {"status": "unauthorized", "error": "Token is no longer active."}, 401
        receipt = CaptureRequest.objects.filter(owner=device.owner, request_id=request_id).first()
        if receipt:
            if receipt.payload_hash != payload_hash:
                return {"status": "conflict", "error": "Use a new request_id for a different message."}, 409
            return receipt.result, 200 if receipt.result["status"] in ("success", "duplicate") else 422
        recent = CaptureRequest.objects.filter(owner=device.owner, created_at__gte=timezone.now()-timedelta(minutes=1)).count()
        if recent >= settings.INGESTION_RATE_PER_MINUTE:
            return {"status": "rate_limited", "error": "Retry this request after a minute."}, 429
        result = ingest_sms_payload(device.owner, message, device_token=device, request_id=str(request_id))
        CaptureRequest.objects.create(owner=device.owner, request_id=request_id, payload_hash=payload_hash, result=result)
        return result, 200 if result["status"] in ("success", "duplicate") else 422
