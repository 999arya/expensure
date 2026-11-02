"""Statement import orchestration.

The CSV is parsed in memory. Staging rows exist only until the user confirms or
cancels the batch.
"""

import hashlib
import os
import re

from django.db import IntegrityError
from django.db import transaction as db_transaction
from django.utils import timezone

from ledger.models import Transaction
from ledger.services import status_for_import

from .models import ImportBatch, ImportRow
from .parsers.statement_csv import (
    StatementParseError,
    StatementRow,
    decode_statement,
    parse_statement,
)


def normalize_bank(value: str) -> str:
    text = str(value or "").casefold()
    if "kotak" in text:
        return "kotak"
    if "baroda" in text or re.search(r"\bbob\b", text):
        return "bob"
    return ""


def _normalize_reference(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


def _normalize_description(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").casefold()).strip()


def dedup_key_for(account_id: int, row: StatementRow) -> str:
    reference = _normalize_reference(row.reference)
    if reference:
        seed = f"ref|{account_id}|{reference}"
    else:
        occurred = row.occurred_at.date().isoformat() if row.occurred_at else ""
        seed = (
            f"fp|{account_id}|{occurred}|{row.amount_paise}|{row.direction}|"
            f"{_normalize_description(row.description)}"
        )
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:48]


def _safe_filename(value: str) -> str:
    name = os.path.basename(str(value or "")).strip()
    return name[:160]


def stage_statement(owner, account, filename: str, data: bytes) -> ImportBatch:
    """Parse an upload and create a preview batch. Raises StatementParseError."""
    bank = normalize_bank(account.bank)
    if not bank:
        raise StatementParseError(
            "CSV import currently supports Kotak and Bank of Baroda accounts. "
            "Choose one of those accounts, or add it first."
        )

    text = decode_statement(data)
    parsed = parse_statement(text, bank)

    warnings = list(parsed.warnings)
    if parsed.account_suffix and parsed.account_suffix != account.account_suffix:
        warnings.append(
            f"The statement shows an account ending {parsed.account_suffix}, but "
            f"{account.nickname} ends {account.account_suffix}. Check that you chose "
            "the right account."
        )

    file_hash = hashlib.sha256(data).hexdigest()
    duplicate_file = (
        ImportBatch.objects.filter(
            owner=owner,
            account=account,
            file_sha256=file_hash,
            status=ImportBatch.Status.IMPORTED,
        )
        .order_by("-created_at")
        .first()
    )
    if duplicate_file:
        warnings.append(
            "This looks like the same file you already imported on "
            f"{timezone.localtime(duplicate_file.created_at):%d %b %Y}. "
            "Matching rows will be skipped."
        )

    valid_rows = [row for row in parsed.rows if not row.error]
    keys = {dedup_key_for(account.pk, row) for row in valid_rows}
    existing_keys = set(
        Transaction.objects.filter(
            account=account,
            dedup_key__in=keys,
        ).values_list("dedup_key", flat=True)
    )

    seen: set[str] = set()
    staged: list[ImportRow] = []
    duplicate_count = 0
    invalid_count = 0

    for row in parsed.rows:
        if row.error:
            status = ImportRow.Status.INVALID
            note = row.error.capitalize()
            invalid_count += 1
            key = ""
        else:
            key = dedup_key_for(account.pk, row)
            if key in existing_keys or key in seen:
                status = ImportRow.Status.DUPLICATE
                note = "Already recorded on this account."
                duplicate_count += 1
            else:
                status = ImportRow.Status.READY
                note = ""
                seen.add(key)

        staged.append(
            ImportRow(
                batch=None,
                line_number=row.line_number,
                occurred_at=row.occurred_at,
                date_only=row.date_only,
                description=row.description,
                reference=row.reference,
                amount_paise=row.amount_paise,
                direction=row.direction,
                balance_paise=row.balance_paise,
                dedup_key=key,
                status=status,
                note=note,
            )
        )

    with db_transaction.atomic():
        batch = ImportBatch.objects.create(
            owner=owner,
            account=account,
            bank=bank,
            original_filename=_safe_filename(filename),
            file_sha256=file_hash,
            period_start=parsed.period_start,
            period_end=parsed.period_end,
            row_count=len(staged),
            duplicate_count=duplicate_count,
            invalid_count=invalid_count,
            warning_text="\n".join(warnings),
        )
        for row in staged:
            row.batch = batch
        ImportRow.objects.bulk_create(staged)

    return batch


def commit_batch(batch: ImportBatch) -> ImportBatch:
    """Create transactions for every ready row, then discard staging rows."""
    if batch.status != ImportBatch.Status.PARSED:
        raise ValueError("This import is no longer waiting to be confirmed.")

    created = 0
    duplicates = 0
    invalid = 0

    with db_transaction.atomic():
        rows = list(batch.rows.all())
        for row in rows:
            if row.status != ImportRow.Status.READY:
                if row.status == ImportRow.Status.DUPLICATE:
                    duplicates += 1
                elif row.status == ImportRow.Status.INVALID:
                    invalid += 1
                continue

            if not row.occurred_at or not row.amount_paise or not row.direction:
                invalid += 1
                continue

            try:
                with db_transaction.atomic():
                    Transaction.objects.create(
                        account=batch.account,
                        occurred_at=row.occurred_at,
                        received_at=timezone.now(),
                        amount_paise=row.amount_paise,
                        direction=row.direction,
                        description=row.description or "Imported bank transaction",
                        reference=row.reference,
                        status=status_for_import(
                            batch.account, row.occurred_at, row.date_only
                        ),
                        source=Transaction.Source.IMPORT,
                        dedup_key=row.dedup_key,
                        import_batch=batch,
                    )
            except IntegrityError:
                duplicates += 1
                continue
            created += 1

        batch.imported_count = created
        batch.duplicate_count = duplicates
        batch.invalid_count = invalid
        batch.status = ImportBatch.Status.IMPORTED
        batch.completed_at = timezone.now()
        batch.save(
            update_fields=[
                "imported_count",
                "duplicate_count",
                "invalid_count",
                "status",
                "completed_at",
            ]
        )
        batch.rows.all().delete()

    return batch


def cancel_batch(batch: ImportBatch) -> ImportBatch:
    if batch.status != ImportBatch.Status.PARSED:
        raise ValueError("This import is no longer waiting to be confirmed.")
    batch.rows.all().delete()
    batch.status = ImportBatch.Status.CANCELLED
    batch.completed_at = timezone.now()
    batch.save(update_fields=["status", "completed_at"])
    return batch


def undo_batch(batch: ImportBatch) -> ImportBatch:
    """Remove every transaction that this batch created."""
    if batch.status != ImportBatch.Status.IMPORTED:
        raise ValueError("Only a completed import can be undone.")
    batch.transactions.all().delete()
    batch.status = ImportBatch.Status.UNDONE
    batch.undone_at = timezone.now()
    batch.save(update_fields=["status", "undone_at"])
    return batch
