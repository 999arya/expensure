"""Statement import tests: parsing, dedupe, posting and undo."""

from datetime import datetime

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import BalanceCheckpoint, BankAccount
from ledger.models import Transaction

from ..models import ImportBatch, ImportRow
from ..parsers.statement_csv import StatementParseError, parse_statement
from ..services import (
    cancel_batch,
    commit_batch,
    dedup_key_for,
    stage_statement,
    undo_batch,
)

User = get_user_model()

KOTAK_CSV = """\
Kotak Mahindra Bank - Account Statement
Account No: XXXXXXXX4821
Transaction Date,Narration,Chq/Ref No,Withdrawal Amt.,Deposit Amt.,Closing Balance
01/10/2026,UPI/SWIGGY/123456,REF001,"1,250.00",,"48,750.00"
02/10/2026,SALARY OCTOBER,SAL900,"","75,000.00","1,23,750.00"
02/10/2026,UPI/SWIGGY/123456,REF001,"1,250.00",,"1,22,500.00"
04/10/2026,ATM CASH,,"2,000.00",,"1,20,500.00"
"""

BOB_CSV = """\
Bank of Baroda
Account Number: 1234567890123
Date,Particulars,Ref No,Amount,Dr/Cr,Balance
05-10-2026,NEFT CREDIT,BOB001,25000,CR,125000
06-10-2026,ELECTRICITY BILL,BOB002,1800,DR,123200
"""


def at(year, month, day, hour=12, minute=0):
    return timezone.make_aware(datetime(year, month, day, hour, minute))


def make_user(username="asha"):
    return User.objects.create_user(username=username, password="test-pass-123")


def make_account(owner, bank=BankAccount.Bank.KOTAK, suffix="4821"):
    return BankAccount.objects.create(
        owner=owner,
        bank=bank,
        nickname=f"{bank} account",
        account_suffix=suffix,
    )


class StatementParserTests(TestCase):
    def test_kotak_columns_are_read(self):
        parsed = parse_statement(KOTAK_CSV, "kotak")

        self.assertEqual(len(parsed.rows), 4)
        first = parsed.rows[0]
        self.assertEqual(first.description, "UPI/SWIGGY/123456")
        self.assertEqual(first.reference, "REF001")
        self.assertEqual(first.amount_paise, 125_000)
        self.assertEqual(first.direction, "debit")
        self.assertTrue(first.date_only)
        self.assertEqual(parsed.account_suffix, "4821")
        self.assertEqual(parsed.period_start.isoformat(), "2026-10-01")
        self.assertEqual(parsed.period_end.isoformat(), "2026-10-04")
        self.assertFalse(any(row.error for row in parsed.rows))

    def test_bob_dr_cr_amount_is_read(self):
        parsed = parse_statement(BOB_CSV, "bob")

        self.assertEqual(len(parsed.rows), 2)
        credit, debit = parsed.rows
        self.assertEqual(credit.direction, "credit")
        self.assertEqual(credit.amount_paise, 2_500_000)
        self.assertEqual(debit.direction, "debit")
        self.assertEqual(debit.amount_paise, 180_000)
        self.assertEqual(debit.balance_paise, 12_320_000)

    def test_bad_rows_are_flagged_not_fatal(self):
        text = """\
Date,Description,Debit,Credit
not-a-date,Broken row,100,
01/10/2026,Good row,200,
"""
        parsed = parse_statement(text, "kotak")

        self.assertEqual(len(parsed.rows), 2)
        self.assertIn("date could not be read", parsed.rows[0].error)
        self.assertEqual(parsed.rows[1].error, "")

    def test_unknown_layout_is_rejected(self):
        with self.assertRaises(StatementParseError):
            parse_statement("alpha,beta,gamma\n1,2,3\n", "kotak")

    def test_wrong_bank_is_rejected(self):
        with self.assertRaises(StatementParseError):
            parse_statement(BOB_CSV, "kotak")


class StatementStagingTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.account = make_account(self.user)

    def test_stage_creates_rows_and_does_not_keep_the_file(self):
        batch = stage_statement(
            self.user,
            self.account,
            "october.csv",
            KOTAK_CSV.encode("utf-8"),
        )

        self.assertEqual(batch.status, ImportBatch.Status.PARSED)
        self.assertEqual(batch.row_count, 4)
        self.assertEqual(batch.original_filename, "october.csv")
        self.assertEqual(len(batch.file_sha256), 64)
        self.assertEqual(batch.rows.count(), 4)
        self.assertEqual(
            batch.rows.filter(status=ImportRow.Status.READY).count(), 3
        )
        self.assertEqual(
            batch.rows.filter(status=ImportRow.Status.DUPLICATE).count(), 1
        )
        self.assertFalse(hasattr(batch, "statement_file"))

    def test_stage_skips_a_transaction_already_recorded(self):
        Transaction.objects.create(
            account=self.account,
            occurred_at=at(2026, 10, 1, 9),
            amount_paise=125_000,
            direction="debit",
            description="Already here",
            reference="REF001",
            dedup_key=dedup_key_for(
                self.account.pk,
                parse_statement(KOTAK_CSV, "kotak").rows[0],
            ),
        )

        batch = stage_statement(
            self.user,
            self.account,
            "october.csv",
            KOTAK_CSV.encode("utf-8"),
        )

        self.assertEqual(
            batch.rows.filter(status=ImportRow.Status.DUPLICATE).count(), 2
        )

    def test_same_file_is_flagged_as_a_repeat(self):
        data = KOTAK_CSV.encode("utf-8")
        first = stage_statement(self.user, self.account, "october.csv", data)
        commit_batch(first)

        second = stage_statement(self.user, self.account, "october.csv", data)

        self.assertIn("same file", second.warning_text)
        self.assertEqual(second.rows.count(), 4)

    def test_account_suffix_mismatch_warns(self):
        account = make_account(self.user, suffix="9999")

        batch = stage_statement(
            self.user,
            account,
            "october.csv",
            KOTAK_CSV.encode("utf-8"),
        )

        self.assertIn("ending 4821", batch.warning_text)


class StatementPostingTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.account = make_account(self.user)

    def test_commit_creates_imported_transactions_and_clears_rows(self):
        batch = stage_statement(
            self.user, self.account, "october.csv", KOTAK_CSV.encode("utf-8")
        )

        commit_batch(batch)
        batch.refresh_from_db()

        self.assertEqual(batch.status, ImportBatch.Status.IMPORTED)
        self.assertEqual(batch.imported_count, 3)
        self.assertEqual(batch.duplicate_count, 1)
        self.assertEqual(batch.rows.count(), 0)
        self.assertEqual(self.account.transactions.count(), 3)
        self.assertTrue(
            all(
                txn.source == Transaction.Source.IMPORT
                and txn.import_batch_id == batch.pk
                and txn.status == Transaction.Status.POSTED
                for txn in self.account.transactions.all()
            )
        )

    def test_no_checkpoint_posts_everything(self):
        batch = stage_statement(
            self.user, self.account, "october.csv", KOTAK_CSV.encode("utf-8")
        )

        commit_batch(batch)

        self.assertFalse(
            self.account.transactions.filter(
                status=Transaction.Status.NEEDS_REVIEW
            ).exists()
        )

    def test_same_day_date_only_row_waits_for_review(self):
        BalanceCheckpoint.objects.create(
            account=self.account,
            amount_paise=10_000_000,
            effective_at=at(2026, 10, 2, 10),
        )
        batch = stage_statement(
            self.user, self.account, "october.csv", KOTAK_CSV.encode("utf-8")
        )

        commit_batch(batch)

        same_day = self.account.transactions.get(reference="SAL900")
        self.assertEqual(same_day.status, Transaction.Status.NEEDS_REVIEW)
        earlier = self.account.transactions.get(reference="REF001")
        self.assertEqual(earlier.status, Transaction.Status.POSTED)

    def test_earlier_rows_are_posted_but_do_not_move_the_estimate(self):
        from accounts.services import estimate_balance, latest_checkpoint

        BalanceCheckpoint.objects.create(
            account=self.account,
            amount_paise=10_000_000,
            effective_at=at(2026, 10, 5, 10),
        )
        batch = stage_statement(
            self.user, self.account, "october.csv", KOTAK_CSV.encode("utf-8")
        )

        commit_batch(batch)

        self.assertEqual(
            self.account.transactions.filter(
                status=Transaction.Status.POSTED
            ).count(),
            3,
        )
        self.assertEqual(
            estimate_balance(self.account, latest_checkpoint(self.account)),
            10_000_000,
        )

    def test_row_after_checkpoint_moves_the_estimate(self):
        from accounts.services import estimate_balance, latest_checkpoint

        BalanceCheckpoint.objects.create(
            account=self.account,
            amount_paise=10_000_000,
            effective_at=at(2026, 10, 1, 8),
        )
        batch = stage_statement(
            self.user, self.account, "october.csv", KOTAK_CSV.encode("utf-8")
        )

        commit_batch(batch)

        # 10,000.00 + 75,000.00 salary - 2,000.00 ATM. The 1 October debit
        # falls at or before the checkpoint, so it is already inside 10,000.00.
        self.assertEqual(
            estimate_balance(self.account, latest_checkpoint(self.account)),
            10_000_000 + 7_500_000 - 200_000,
        )

    def test_cancel_discards_staging_rows(self):
        batch = stage_statement(
            self.user, self.account, "october.csv", KOTAK_CSV.encode("utf-8")
        )

        cancel_batch(batch)
        batch.refresh_from_db()

        self.assertEqual(batch.status, ImportBatch.Status.CANCELLED)
        self.assertEqual(batch.rows.count(), 0)
        self.assertEqual(self.account.transactions.count(), 0)

    def test_undo_removes_only_this_import(self):
        manual = Transaction.objects.create(
            account=self.account,
            occurred_at=at(2026, 9, 30, 9),
            amount_paise=50_000,
            direction="debit",
            description="Manual",
        )
        batch = stage_statement(
            self.user, self.account, "october.csv", KOTAK_CSV.encode("utf-8")
        )
        commit_batch(batch)

        undo_batch(batch)
        batch.refresh_from_db()

        self.assertEqual(batch.status, ImportBatch.Status.UNDONE)
        self.assertEqual(self.account.transactions.count(), 1)
        self.assertTrue(Transaction.objects.filter(pk=manual.pk).exists())

    def test_committed_import_cannot_be_committed_twice(self):
        batch = stage_statement(
            self.user, self.account, "october.csv", KOTAK_CSV.encode("utf-8")
        )
        commit_batch(batch)

        with self.assertRaises(ValueError):
            commit_batch(batch)

    def test_dedup_key_changes_per_account(self):
        row = parse_statement(KOTAK_CSV, "kotak").rows[0]
        other = make_account(self.user, suffix="7777")

        self.assertNotEqual(
            dedup_key_for(self.account.pk, row),
            dedup_key_for(other.pk, row),
        )


class StatementViewTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.client.force_login(self.user)
        self.account = make_account(self.user)

    def upload(self, owner=None, data=KOTAK_CSV, name="october.csv"):
        return self.client.post(
            reverse("ingestion:upload"),
            {
                "account": self.account.pk,
                "statement": SimpleUploadedFile(
                    name,
                    data.encode("utf-8") if isinstance(data, str) else data,
                    content_type="text/csv",
                ),
            },
        )

    def test_upload_then_preview_then_confirm(self):
        response = self.upload()
        batch = ImportBatch.objects.get()
        self.assertRedirects(
            response, reverse("ingestion:preview", kwargs={"pk": batch.pk})
        )

        preview = self.client.get(reverse("ingestion:preview", kwargs={"pk": batch.pk}))
        self.assertContains(preview, "Statement preview")
        self.assertContains(preview, "UPI/SWIGGY/123456")

        confirmed = self.client.post(
            reverse("ingestion:confirm", kwargs={"pk": batch.pk})
        )
        self.assertRedirects(
            confirmed, reverse("ingestion:batch_detail", kwargs={"pk": batch.pk})
        )
        self.assertEqual(self.account.transactions.count(), 3)

    def test_cancel_from_preview(self):
        self.upload()
        batch = ImportBatch.objects.get()

        response = self.client.post(
            reverse("ingestion:cancel", kwargs={"pk": batch.pk})
        )

        self.assertRedirects(response, reverse("ingestion:history"))
        batch.refresh_from_db()
        self.assertEqual(batch.status, ImportBatch.Status.CANCELLED)

    def test_undo_from_detail(self):
        self.upload()
        batch = ImportBatch.objects.get()
        self.client.post(reverse("ingestion:confirm", kwargs={"pk": batch.pk}))

        response = self.client.post(
            reverse("ingestion:undo", kwargs={"pk": batch.pk})
        )

        self.assertRedirects(
            response, reverse("ingestion:batch_detail", kwargs={"pk": batch.pk})
        )
        self.assertEqual(self.account.transactions.count(), 0)

    def test_non_csv_is_rejected(self):
        response = self.upload(name="statement.pdf")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Upload the CSV export")
        self.assertEqual(ImportBatch.objects.count(), 0)

    def test_oversized_file_is_rejected(self):
        from ..parsers.statement_csv import MAX_STATEMENT_BYTES

        response = self.upload(data=b"x" * (MAX_STATEMENT_BYTES + 1))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "under 2 MB")
        self.assertEqual(ImportBatch.objects.count(), 0)

    def test_unsupported_account_bank_is_rejected(self):
        other = BankAccount.objects.create(
            owner=self.user,
            bank=BankAccount.Bank.OTHER,
            nickname="Other bank",
            account_suffix="1111",
        )

        response = self.client.post(
            reverse("ingestion:upload"),
            {
                "account": other.pk,
                "statement": SimpleUploadedFile(
                    "october.csv", KOTAK_CSV.encode("utf-8"), content_type="text/csv"
                ),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "supports Kotak and Bank of Baroda")

    def test_another_owner_cannot_see_the_batch(self):
        self.upload()
        batch = ImportBatch.objects.get()
        self.client.force_login(make_user("ravi"))

        self.assertEqual(
            self.client.get(
                reverse("ingestion:preview", kwargs={"pk": batch.pk})
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.get(
                reverse("ingestion:batch_detail", kwargs={"pk": batch.pk})
            ).status_code,
            404,
        )

    def test_history_lists_only_own_batches(self):
        self.upload()
        other = make_user("ravi")
        other_account = make_account(other, bank=BankAccount.Bank.BANK_OF_BARODA)
        stage_statement(other, other_account, "bob.csv", BOB_CSV.encode("utf-8"))

        response = self.client.get(reverse("ingestion:history"))

        self.assertContains(response, "october.csv")
        self.assertNotContains(response, "bob.csv")
