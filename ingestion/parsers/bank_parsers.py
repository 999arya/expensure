"""Deterministic regex-based SMS parsers for Kotak and Bank of Baroda.

Privacy guarantees (Decision 006):
- Rejects OTPs and non-financial messages.
- Does not log raw message text.
- Parses deterministically without LLM guesswork.
"""

import hashlib
import re
from dataclasses import dataclass, replace
from datetime import datetime
from django.utils import timezone
from core.parsing import parse_inr

OTP_PATTERNS = [
    r"\botp\b",
    r"one[\s-]?time[\s-]?password",
    r"verification\s*code",
    r"security\s*code",
    r"secret\s*code",
    r"do\s*not\s*share",
    r"login\s*code",
]


@dataclass(frozen=True)
class ParseResult:
    is_valid: bool
    is_otp: bool
    bank: str  # "kotak", "bob", "unknown"
    account_suffix: str
    direction: str  # "debit", "credit"
    amount_paise: int
    occurred_at: datetime | None
    reference: str
    reported_balance_paise: int | None
    merchant_or_info: str
    fingerprint: str
    error: str = ""
    time_precision: str = "date"


def check_is_otp(text: str) -> bool:
    lower = text.lower()
    return any(re.search(pat, lower) for pat in OTP_PATTERNS)


def parse_rupees_to_paise(amt_str: str) -> int:
    """Safely convert strings like '1,234.50' or '500' to integer paise."""
    amount = parse_inr(amt_str)
    if not 0 <= amount <= 9_000_000_000_000_000:
        raise ValueError("Amount is outside the supported range")
    return amount


def parse_date_str(date_str: str) -> datetime | None:
    """Parse common Indian SMS date formats: '15-10-26', '20-Oct-26', '12-10-2026'."""
    formats = [
        "%d-%m-%y",
        "%d-%m-%Y",
        "%d/%m/%y",
        "%d/%m/%Y",
        "%d-%b-%y",
        "%d-%b-%Y",
        "%d %b %y",
        "%d %b %Y",
    ]
    for fmt in formats:
        try:
            dt = datetime.strptime(date_str.strip(), fmt)
            # Default to midday local time when no time is given
            dt = dt.replace(hour=12, minute=0, second=0)
            return timezone.make_aware(dt)
        except (ValueError, TypeError):
            continue
    return None


def calculate_fingerprint(bank: str, suffix: str, direction: str, amount_paise: int, ref: str, date_str: str) -> str:
    seed = f"{bank}:{suffix}:{direction}:{amount_paise}:{ref}:{date_str}"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:32]


def parse_kotak(text: str) -> ParseResult | None:
    """Parser for Kotak Mahindra Bank transactional messages."""
    lower = text.lower()
    if "kotak" not in lower:
        return None

    # Determine direction
    is_credit = bool(re.search(r"\b(credited|received)\b", lower))
    is_debit = bool(re.search(r"\b(debited|sent|spent)\b", lower))
    if not (is_credit or is_debit) or (is_credit and is_debit):
        return None
    direction = "credit" if is_credit else "debit"

    # Extract Account suffix (e.g. 'AC X1234', 'A/C 1234', 'AC 1234')
    acc_match = re.search(r"(?:a/c|ac)\s*(?:x+|xx+)?(\d{4})(?!\d)", text, re.IGNORECASE)
    suffix = acc_match.group(1) if acc_match else ""
    if not suffix:
        return None

    # Extract Amount (e.g. 'Rs.500.00', 'Rs 1,250.00', 'INR 50,000.00')
    amt_match = re.search(r"(?:rs\.?|inr)\s*([\d,]+(?:\.\d{1,2})?)", text, re.IGNORECASE)
    if not amt_match:
        return None
    amount_paise = parse_rupees_to_paise(amt_match.group(1))

    # Extract Balance if reported (e.g. 'Bal Rs.14,500.00', 'Avail Bal: Rs 13,250.00')
    bal_match = re.search(r"(?:avail(?:able)?\s*bal(?:ance)?|bal)\s*:?\s*(?:rs\.?|inr)\s*([\d,]+(?:\.\d{1,2})?)", text, re.IGNORECASE)
    reported_bal = parse_rupees_to_paise(bal_match.group(1)) if bal_match else None

    # Extract Date
    date_match = re.search(r"\bon\s+(\d{1,2}[-/ ](?:[a-zA-Z]{3}|\d{1,2})[-/ ]\d{2,4})", text, re.IGNORECASE)
    date_str = date_match.group(1) if date_match else ""
    occurred_at = parse_date_str(date_str) if date_str else None

    # Extract Reference / UTR
    ref_match = re.search(r"(?:ref(?:erence)?|utr|upi\s*ref)\s*:?\s*([a-zA-Z0-9]+)", text, re.IGNORECASE)
    ref = ref_match.group(1) if ref_match else ""

    # Info / Merchant
    info_match = re.search(r"(?:info|to|at)\s*:?\s*([^.\n;]+)", text, re.IGNORECASE)
    info = info_match.group(1).strip() if info_match else ""

    fp = calculate_fingerprint("kotak", suffix, direction, amount_paise, ref, date_str)

    return ParseResult(
        is_valid=True,
        is_otp=False,
        bank="kotak",
        account_suffix=suffix,
        direction=direction,
        amount_paise=amount_paise,
        occurred_at=occurred_at,
        reference=ref,
        reported_balance_paise=reported_bal,
        merchant_or_info=info,
        fingerprint=fp,
    )


def parse_baroda(text: str) -> ParseResult | None:
    """Parser for Bank of Baroda transactional messages."""
    lower = text.lower()
    # Check indicators: 'baroda', 'bob', or pattern 'A/C ... debited/credited'
    is_bob = "baroda" in lower or "bob" in lower or re.search(r"\ba/c\s*(?:x+)?\d{4}\s+(debited|credited)", lower)
    if not is_bob:
        return None

    is_credit = bool(re.search(r"\b(credited|received)\b", lower))
    is_debit = bool(re.search(r"\b(debited|sent)\b", lower))
    if not (is_credit or is_debit) or (is_credit and is_debit):
        return None
    direction = "credit" if is_credit else "debit"

    # Account suffix
    acc_match = re.search(r"(?:a/c|ac)\s*(?:x+|xx+)?(\d{4})(?!\d)", text, re.IGNORECASE)
    suffix = acc_match.group(1) if acc_match else ""
    if not suffix:
        return None

    # Amount
    amt_match = re.search(r"(?:rs\.?|inr)\s*([\d,]+(?:\.\d{1,2})?)", text, re.IGNORECASE)
    if not amt_match:
        return None
    amount_paise = parse_rupees_to_paise(amt_match.group(1))

    # Available Balance (e.g. 'Avl Bal Rs.28,000.00', 'Avl Bal: Rs 27,149.50')
    bal_match = re.search(r"(?:avl\s*bal(?:ance)?|avail(?:able)?\s*bal)\s*:?\s*(?:rs\.?|inr)\s*([\d,]+(?:\.\d{1,2})?)", text, re.IGNORECASE)
    reported_bal = parse_rupees_to_paise(bal_match.group(1)) if bal_match else None

    # Date
    date_match = re.search(r"\bon\s+(\d{1,2}[-/ ](?:[a-zA-Z]{3}|\d{1,2})[-/ ]\d{2,4})", text, re.IGNORECASE)
    date_str = date_match.group(1) if date_match else ""
    occurred_at = parse_date_str(date_str) if date_str else None

    # Reference
    ref_match = re.search(r"(?:ref(?:erence)?|utr)\s*:?\s*([a-zA-Z0-9]+)", text, re.IGNORECASE)
    ref = ref_match.group(1) if ref_match else ""

    # Merchant or info
    info_match = re.search(r"(?:at|to|info)\s*:?\s*([^.\n;]+)", text, re.IGNORECASE)
    info = info_match.group(1).strip() if info_match else ""

    fp = calculate_fingerprint("bob", suffix, direction, amount_paise, ref, date_str)

    return ParseResult(
        is_valid=True,
        is_otp=False,
        bank="bob",
        account_suffix=suffix,
        direction=direction,
        amount_paise=amount_paise,
        occurred_at=occurred_at,
        reference=ref,
        reported_balance_paise=reported_bal,
        merchant_or_info=info,
        fingerprint=fp,
    )


def parse_sms_message(text: str) -> ParseResult:
    """Master parse function: checks OTP first, then runs bank parsers."""
    if not text or not text.strip():
        return ParseResult(
            is_valid=False, is_otp=False, bank="unknown", account_suffix="",
            direction="", amount_paise=0, occurred_at=None, reference="",
            reported_balance_paise=None, merchant_or_info="", fingerprint="",
            error="Empty message",
        )

    if check_is_otp(text):
        return ParseResult(
            is_valid=False, is_otp=True, bank="unknown", account_suffix="",
            direction="", amount_paise=0, occurred_at=None, reference="",
            reported_balance_paise=None, merchant_or_info="", fingerprint="",
            error="OTP / Security code messages are rejected for your safety.",
        )

    if len(text) <= 4096:
        try:
            res = parse_kotak(text)
            if res is None and "kotak" not in text.lower():
                res = parse_baroda(text)
            if res and res.is_valid and 0 < res.amount_paise <= 9_000_000_000_000_000:
                # Date-only messages must be reviewed; never invent precise capture times.
                precision = "date" if res.occurred_at else "unknown"
                time_match = re.search(r"\bat\s+(\d{1,2}):(\d{2})(?::(\d{2}))?(?!\d)", text, re.I)
                if time_match and res.occurred_at:
                    hour, minute, second = (int(v or 0) for v in time_match.groups())
                    res = replace(res, occurred_at=res.occurred_at.replace(hour=hour, minute=minute, second=second))
                    precision = "exact"
                return replace(res, time_precision=precision)
        except (ValueError, OverflowError):
            pass

    return ParseResult(
        is_valid=False, is_otp=False, bank="unknown", account_suffix="",
        direction="", amount_paise=0, occurred_at=None, reference="",
        reported_balance_paise=None, merchant_or_info="", fingerprint="",
        error="Unsupported or invalid bank message. Paste a supported Kotak or Bank of Baroda format, or enter it manually.",
    )
