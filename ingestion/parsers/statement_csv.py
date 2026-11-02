"""Read a Kotak or Bank of Baroda CSV statement in memory.

The file is never written to disk. Only normalized rows leave this module.
"""

import codecs
import csv
import io
import re
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation

from django.utils import timezone

SUPPORTED_BANKS = {
    "kotak": "Kotak Mahindra Bank",
    "bob": "Bank of Baroda",
}

MAX_STATEMENT_ROWS = 2000
MAX_STATEMENT_BYTES = 2 * 1024 * 1024

DATE_FORMATS = (
    "%d/%m/%Y",
    "%d-%m-%Y",
    "%d.%m.%Y",
    "%d/%m/%y",
    "%d-%m-%y",
    "%d %b %Y",
    "%d %b %y",
    "%d-%b-%Y",
    "%d-%b-%y",
    "%Y-%m-%d",
    "%Y/%m/%d",
)

DATETIME_FORMATS = (
    "%d/%m/%Y %H:%M:%S",
    "%d/%m/%Y %H:%M",
    "%d-%m-%Y %H:%M:%S",
    "%d-%m-%Y %H:%M",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M",
    "%d/%m/%Y %I:%M:%S %p",
    "%d/%m/%Y %I:%M %p",
)

# Header names seen across net-banking exports are matched by tokens below.
DATE_HEADER_PRIORITY = ("transaction date", "txn date", "tran date", "date", "value date", "posting date")


class StatementParseError(ValueError):
    """The upload cannot be read as a supported statement."""


@dataclass(frozen=True)
class StatementRow:
    line_number: int
    occurred_at: datetime | None
    date_only: bool
    description: str
    reference: str
    amount_paise: int | None
    direction: str
    balance_paise: int | None
    error: str = ""


@dataclass(frozen=True)
class ParsedStatement:
    bank: str
    rows: list[StatementRow]
    account_suffix: str
    period_start: date | None
    period_end: date | None
    warnings: list[str]


def normalize_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).strip()


def decode_statement(data: bytes) -> str:
    """Decode common Indian bank CSV encodings without keeping the bytes."""
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return data.decode("utf-16")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            return data.decode("cp1252")
        except UnicodeDecodeError:
            return data.decode("latin-1")


def detect_bank(text: str, fallback: str = "") -> str:
    lower = text.casefold()
    if "kotak" in lower:
        return "kotak"
    if "baroda" in lower or re.search(r"\bbob\b", lower):
        return "bob"
    return fallback if fallback in SUPPORTED_BANKS else ""


def detect_account_suffix(text: str) -> str:
    match = re.search(
        r"(?:a/c|account)\s*(?:no\.?|number)?\s*[:\-]?\s*([x*\d]{6,})",
        text,
        re.IGNORECASE,
    )
    if not match:
        return ""
    return match.group(1)[-4:]


def _classify_header(value: str) -> str | None:
    normalized = normalize_text(value)
    if not normalized:
        return None

    tokens = set(normalized.split())
    if normalized in DATE_HEADER_PRIORITY or normalized.endswith(" date"):
        return "date"
    if {"dr", "cr"} <= tokens:
        return "direction"
    if normalized in {"type", "transaction type", "txn type"}:
        return "direction"
    if tokens & {"withdrawal", "debit"} or normalized in {"dr amount", "dr amt"}:
        return "debit"
    if tokens & {"deposit", "credit"} or normalized in {"cr amount", "cr amt"}:
        return "credit"
    if tokens & {
        "reference",
        "ref",
        "utr",
        "cheque",
        "chq",
        "instrument",
    }:
        return "reference"
    if tokens & {
        "description",
        "narration",
        "particular",
        "particulars",
        "remark",
        "remarks",
        "detail",
        "details",
    }:
        return "description"
    if "balance" in tokens:
        return "balance"
    if tokens & {"amount", "amt"}:
        return "amount"
    return None


def _header_map(row: list[str]) -> dict[str, int]:
    mapping: dict[str, int] = {}
    for index, value in enumerate(row[:30]):
        key = _classify_header(value)
        if not key:
            continue
        if key == "date" and "date" in mapping:
            current = normalize_text(row[mapping["date"]])
            candidate = normalize_text(value)
            # Prefer the transaction date over the value/posting date.
            if any(current.startswith(prefix) for prefix in ("transaction", "txn", "tran")):
                continue
            if any(candidate.startswith(prefix) for prefix in ("transaction", "txn", "tran")):
                mapping[key] = index
            continue
        mapping.setdefault(key, index)
    return mapping


def _find_header(rows: list[list[str]]) -> tuple[int, dict[str, int]]:
    best: tuple[int, dict[str, int]] | None = None
    for index, row in enumerate(rows[:40]):
        mapping = _header_map(row)
        if "date" not in mapping:
            continue
        if not {"debit", "credit", "amount"} & set(mapping):
            continue
        if "description" not in mapping and "reference" not in mapping:
            continue
        score = len(mapping)
        if best is None or score > len(best[1]):
            best = (index, mapping)
    if best is None:
        raise StatementParseError(
            "This does not look like a supported Kotak or Bank of Baroda CSV. "
            "Send the header row and I will add that export format."
        )
    return best


def parse_statement_date(value: str) -> tuple[datetime | None, bool]:
    text = " ".join(str(value or "").strip().split())
    if not text:
        return None, True
    for fmt in DATETIME_FORMATS:
        try:
            parsed = datetime.strptime(text, fmt)
            return timezone.make_aware(parsed), False
        except ValueError:
            continue
    for fmt in DATE_FORMATS:
        try:
            parsed = datetime.strptime(text, fmt).date()
            return timezone.make_aware(datetime.combine(parsed, time.min)), True
        except ValueError:
            continue
    return None, True


def parse_statement_amount(value: str) -> tuple[int | None, str]:
    """Return (paise, marker), where marker may be 'debit' or 'credit'."""
    text = str(value or "").strip()
    if not text or text in {"-", "--", "—"}:
        return None, ""

    lower = text.casefold()
    marker = ""
    if re.search(r"\bdr\b", lower):
        marker = "debit"
    elif re.search(r"\bcr\b", lower):
        marker = "credit"
    text = re.sub(r"\b(?:dr|cr)\b", "", text, flags=re.IGNORECASE)

    negative = text.strip().startswith("(") and text.strip().endswith(")")
    text = text.replace("(", "").replace(")", "")
    text = re.sub(r"(?i)(?:inr|rs\.?|₹)", "", text)
    text = text.replace(",", "").replace(" ", "").strip()
    if not text:
        return None, marker
    text = text.removeprefix("+")
    try:
        amount = Decimal(text)
    except InvalidOperation:
        return None, marker
    if negative:
        amount = -amount
    if amount == 0:
        return None, marker
    paise = int((amount * 100).to_integral_value())
    return paise, marker


def _cell(row: list[str], index: int | None) -> str:
    if index is None or index >= len(row):
        return ""
    return str(row[index]).strip()


def _looks_like_repeated_header(row: list[str]) -> bool:
    mapping = _header_map(row)
    return "date" in mapping and bool({"debit", "credit", "amount"} & set(mapping))


def _looks_like_footer(row: list[str]) -> bool:
    text = normalize_text(" ".join(row))
    return any(
        phrase in text
        for phrase in (
            "statement summary",
            "closing balance",
            "opening balance",
            "total debit",
            "total credit",
            "end of statement",
        )
    )


def parse_statement(text: str, bank: str) -> ParsedStatement:
    """Parse decoded CSV text into normalized rows."""
    if bank not in SUPPORTED_BANKS:
        raise StatementParseError("CSV import currently supports Kotak and Bank of Baroda accounts.")

    try:
        reader = csv.reader(io.StringIO(text))
        rows = list(reader)
    except csv.Error as exc:
        raise StatementParseError("The CSV could not be read. Check that it is a valid bank export.") from exc

    if not rows:
        raise StatementParseError("The file is empty.")

    header_index, mapping = _find_header(rows)
    parsed: list[StatementRow] = []

    for offset, raw_row in enumerate(rows[header_index + 1 :], start=header_index + 2):
        if not any(str(cell).strip() for cell in raw_row):
            continue
        if _looks_like_repeated_header(raw_row):
            continue
        if _looks_like_footer(raw_row):
            continue

        date_text = _cell(raw_row, mapping.get("date"))
        description = _cell(raw_row, mapping.get("description"))
        reference = _cell(raw_row, mapping.get("reference"))
        balance_text = _cell(raw_row, mapping.get("balance"))
        direction_text = normalize_text(_cell(raw_row, mapping.get("direction")))

        debit_text = _cell(raw_row, mapping.get("debit"))
        credit_text = _cell(raw_row, mapping.get("credit"))
        amount_text = _cell(raw_row, mapping.get("amount"))

        if not date_text and not (debit_text or credit_text or amount_text or description):
            continue

        occurred_at, date_only = parse_statement_date(date_text)
        errors: list[str] = []
        if occurred_at is None:
            errors.append("date could not be read")

        debit_paise, debit_marker = parse_statement_amount(debit_text)
        credit_paise, credit_marker = parse_statement_amount(credit_text)
        amount_paise, amount_marker = parse_statement_amount(amount_text)
        balance_paise, _ = parse_statement_amount(balance_text)

        direction = ""
        amount: int | None = None

        if debit_paise is not None and credit_paise is not None:
            errors.append("both debit and credit are filled")
        elif debit_paise is not None:
            direction = "debit"
            amount = abs(debit_paise)
        elif credit_paise is not None:
            direction = "credit"
            amount = abs(credit_paise)
        elif amount_paise is not None:
            direction_tokens = set(re.findall(r"[a-z]+", direction_text))
            if direction_tokens & {"debit", "withdrawal", "dr"}:
                direction = "debit"
            elif direction_tokens & {"credit", "deposit", "cr"}:
                direction = "credit"
            elif amount_marker:
                direction = amount_marker
            elif debit_marker:
                direction = "debit"
            elif credit_marker:
                direction = "credit"
            elif amount_paise < 0:
                direction = "debit"
                amount_paise = abs(amount_paise)
            else:
                direction = "credit"
            amount = abs(amount_paise)
        else:
            errors.append("amount could not be read")

        if amount is not None and amount <= 0:
            errors.append("amount is zero")

        if not direction and amount is not None:
            errors.append("debit or credit could not be decided")

        if not description and not reference:
            description = "Imported bank transaction"

        parsed.append(
            StatementRow(
                line_number=offset,
                occurred_at=occurred_at,
                date_only=date_only,
                description=description[:200],
                reference=reference[:64],
                amount_paise=amount,
                direction=direction,
                balance_paise=balance_paise,
                error="; ".join(errors),
            )
        )
        if len(parsed) > MAX_STATEMENT_ROWS:
            raise StatementParseError(
                f"That statement has more than {MAX_STATEMENT_ROWS} rows. "
                "Split it into smaller date ranges."
            )

    if not parsed:
        raise StatementParseError("No transaction rows were found below the header.")

    valid_dates = [row.occurred_at.date() for row in parsed if row.occurred_at]
    warnings: list[str] = []
    if any("date could not be read" in row.error for row in parsed):
        warnings.append("Some rows have a date the app could not read. They are marked for attention.")
    if any(row.date_only for row in parsed):
        warnings.append("This export has dates but no transaction times. Same-day rows may need review.")

    detected = detect_bank(text, bank)
    if detected and detected != bank:
        raise StatementParseError(
            f"This looks like a {SUPPORTED_BANKS[detected]} statement, but the chosen account is "
            f"{SUPPORTED_BANKS[bank]}. Choose the matching account."
        )

    return ParsedStatement(
        bank=bank,
        rows=parsed,
        account_suffix=detect_account_suffix(text),
        period_start=min(valid_dates) if valid_dates else None,
        period_end=max(valid_dates) if valid_dates else None,
        warnings=warnings,
    )
