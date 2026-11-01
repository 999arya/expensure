import json
import uuid
from dataclasses import asdict
from datetime import datetime
from django.core import signing
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.cache import never_cache
from django.db import OperationalError
from .services import process_device_request
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from accounts.models import BankAccount
from .models import DeviceToken, IngestionEvent
from .parsers.bank_parsers import parse_sms_message
from .services import commit_parsed_transaction, find_matching_account

SAMPLE_MESSAGES = {
    "kotak_debit": "Sent Rs.500.00 from Kotak Bank AC X1234 to payee@upi on 15-10-26 Ref 429182910291. Bal Rs.14,500.00.",
    "kotak_credit": "Your Kotak Bank A/C 1234 has been credited by Rs. 50,000.00 on 30-Oct-26 by NEFT. Bal: Rs. 63,250.00.",
    "bob_debit": "A/C 5678 debited by Rs.2,000.00 on 12-10-2026 by UPI. Avl Bal Rs.28,000.00. Ref 1029384756.",
    "bob_credit": "A/C 5678 credited for Rs.10,000.00 on 01-10-2026. Avl Bal Rs.30,000.00. Ref 9876543210.",
    "otp_sample": "Your Kotak Bank net banking OTP is 839201. Valid for 10 minutes. Do not share this OTP with anyone.",
}


@sensitive_post_parameters("sms_text")
@login_required
def paste_view(request):
    """Paste SMS screen for manual import or preview."""
    user = request.user
    accounts = BankAccount.objects.filter(owner=user, is_active=True)

    parsed_result = None
    selected_account = None
    error_message = None

    if request.method == "POST":
        sms_text = request.POST.get("sms_text", "").strip()
        parsed = parse_sms_message(sms_text)

        if not parsed.is_valid:
            error_message = parsed.error
        else:
            parsed_result = parsed
            matching_acc, _ = find_matching_account(user, parsed.bank, parsed.account_suffix)
            selected_account = matching_acc

    context = {
        "accounts": accounts,
        "parsed": parsed_result,
        "selected_account": selected_account,
        "error_message": error_message,
        "samples": SAMPLE_MESSAGES,
    }
    if parsed_result:
        payload = asdict(parsed_result)
        payload["occurred_at"] = parsed_result.occurred_at.isoformat() if parsed_result.occurred_at else None
        # Only parsed, minimized metadata is signed. No raw SMS is embedded.
        payload["merchant_or_info"] = ""
        context["review_token"] = signing.dumps({"owner": user.pk, "parsed": payload}, salt="sms-review")
    return render(request, "ingestion/paste.html", context)


@require_POST
@login_required
def commit_paste_view(request):
    """Review screen commit before saving to ledger."""
    user = request.user
    account_id = request.POST.get("account_id")
    account = get_object_or_404(BankAccount, pk=account_id, owner=user, is_active=True)

    from .parsers.bank_parsers import ParseResult
    try:
        payload = signing.loads(request.POST.get("review_token", ""), salt="sms-review", max_age=900)
        if payload["owner"] != user.pk:
            raise signing.BadSignature("Wrong owner")
        fields = payload["parsed"]
        fields["occurred_at"] = datetime.fromisoformat(fields["occurred_at"]) if fields["occurred_at"] else None
        parse_res = ParseResult(**fields)
    except (signing.BadSignature, ValueError, KeyError, TypeError):
        messages.error(request, "This review expired or was changed. Paste the message again.")
        return redirect("ingestion:paste")

    txn, status, msg = commit_parsed_transaction(user, parse_res, account=account)
    if txn:
        messages.success(request, msg)
        return redirect("ledger:list")
    else:
        messages.error(request, msg)
        return redirect("ingestion:paste")


@never_cache
@login_required
def shortcuts_guide(request):
    """Apple Shortcuts iOS capture guide and device token management."""
    tokens = DeviceToken.objects.filter(owner=request.user)
    recent_events = IngestionEvent.objects.filter(owner=request.user)[:10]

    context = {
        "tokens": tokens,
        "events": recent_events,
        "raw_token_display": request.session.pop("new_device_token", None),
    }
    return render(request, "ingestion/shortcuts.html", context)


@require_POST
@login_required
def generate_token(request):
    name = request.POST.get("name", "").strip() or f"{request.user.username}'s Device"
    name = name[:64]
    _, raw_token = DeviceToken.create_token(request.user, name)
    request.session["new_device_token"] = raw_token
    messages.success(request, f"Device token '{name}' created. Copy it now: it won't be shown again.")
    return redirect("ingestion:shortcuts")


@require_POST
@login_required
def revoke_token(request, pk):
    token = get_object_or_404(DeviceToken, pk=pk, owner=request.user)
    token.revoke()
    messages.info(request, f"Device token '{token.name}' has been revoked.")
    return redirect("ingestion:shortcuts")


@csrf_exempt
def api_ingest(request):
    """Device-token-protected HTTPS ingestion endpoint for Apple Shortcuts."""
    if request.method != "POST":
        return JsonResponse({"error": "POST method required"}, status=405)

    # Extract auth token from Authorization header or X-Device-Token
    auth_header = request.headers.get("Authorization", "")
    token_str = ""
    if auth_header.startswith("Bearer "):
        token_str = auth_header[7:].strip()
    elif "X-Device-Token" in request.headers:
        token_str = request.headers["X-Device-Token"].strip()

    if not token_str:
        return JsonResponse({"error": "Missing Authorization header or X-Device-Token"}, status=401)

    device_token = DeviceToken.verify_token(token_str)
    if not device_token or not device_token.is_active:
        return JsonResponse({"error": "Invalid or revoked device token"}, status=401)

    if request.content_type != "application/json":
        return JsonResponse({"error": "Use application/json"}, status=415)
    if len(request.body) > 8192:
        return JsonResponse({"error": "Payload is too large"}, status=413)
    try:
        data = json.loads(request.body)
        if not isinstance(data, dict) or not isinstance(data.get("message"), str):
            raise ValueError
        message_text = data["message"].strip()
        if not 1 <= len(message_text) <= 4096:
            raise ValueError
        request_id = uuid.UUID(str(data.get("request_id") or uuid.uuid4()))
    except (ValueError, TypeError, UnicodeDecodeError):
        return JsonResponse({"error": "Provide a message string (1-4096 characters) and a valid UUID request_id."}, status=400)
    try:
        result, status_code = process_device_request(device_token, message_text, request_id)
    except OperationalError:
        return JsonResponse({"error": "Temporarily busy. Retry with the same request_id."}, status=503)
    response = JsonResponse(result, status=status_code)
    if status_code == 429:
        response["Retry-After"] = "60"
    response["Cache-Control"] = "no-store"
    return response
