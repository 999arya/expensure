import json
from datetime import datetime
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import BalanceCheckpoint, BankAccount
from ledger.defaults import seed_default_categories
from ledger.models import Transaction
from ingestion.models import DeviceToken, IngestionEvent
from ingestion.parsers.bank_parsers import (
    parse_sms_message,
)
from ingestion.services import ingest_sms_payload

User = get_user_model()


def at(y, m, d, h=12, minute=0):
    return timezone.make_aware(datetime(y, m, d, h, minute))


class IngestionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="arun",
            password="testpassword123",
        )
        seed_default_categories(self.user)
        self.kotak_acc = BankAccount.objects.create(
            owner=self.user,
            bank=BankAccount.Bank.KOTAK,
            nickname="Kotak Salary",
            account_suffix="1234",
        )
        BalanceCheckpoint.objects.create(
            account=self.kotak_acc,
            amount_paise=1_000_000,
            effective_at=at(2026, 10, 1),
        )
        self.bob_acc = BankAccount.objects.create(
            owner=self.user,
            bank=BankAccount.Bank.BANK_OF_BARODA,
            nickname="BoB Savings",
            account_suffix="5678",
        )
        BalanceCheckpoint.objects.create(
            account=self.bob_acc,
            amount_paise=2_000_000,
            effective_at=at(2026, 10, 1),
        )

        self.device_token, self.raw_token = DeviceToken.create_token(
            self.user, "Arun's iPhone"
        )

    def test_kotak_debit_and_credit_parsing(self):
        debit_sms = "Sent Rs.500.00 from Kotak Bank AC X1234 to payee@upi on 15-10-26 Ref 429182910291. Bal Rs.14,500.00."
        res = parse_sms_message(debit_sms)
        self.assertTrue(res.is_valid)
        self.assertEqual(res.bank, "kotak")
        self.assertEqual(res.account_suffix, "1234")
        self.assertEqual(res.direction, "debit")
        self.assertEqual(res.amount_paise, 50_000)
        self.assertEqual(res.reference, "429182910291")
        self.assertEqual(res.reported_balance_paise, 1_450_000)

        credit_sms = "Your Kotak Bank A/C 1234 has been credited by Rs. 50,000.00 on 30-Oct-26 by NEFT. Bal: Rs. 63,250.00."
        res_cr = parse_sms_message(credit_sms)
        self.assertTrue(res_cr.is_valid)
        self.assertEqual(res_cr.bank, "kotak")
        self.assertEqual(res_cr.account_suffix, "1234")
        self.assertEqual(res_cr.direction, "credit")
        self.assertEqual(res_cr.amount_paise, 5_000_000)
        self.assertEqual(res_cr.reported_balance_paise, 6_325_000)

    def test_bank_of_baroda_debit_and_credit_parsing(self):
        bob_debit = "A/C 5678 debited by Rs.2,000.00 on 12-10-2026 by UPI. Avl Bal Rs.28,000.00. Ref 1029384756."
        res = parse_sms_message(bob_debit)
        self.assertTrue(res.is_valid)
        self.assertEqual(res.bank, "bob")
        self.assertEqual(res.account_suffix, "5678")
        self.assertEqual(res.direction, "debit")
        self.assertEqual(res.amount_paise, 200_000)
        self.assertEqual(res.reference, "1029384756")
        self.assertEqual(res.reported_balance_paise, 2_800_000)

        bob_credit = "A/C 5678 credited for Rs.10,000.00 on 01-10-2026. Avl Bal Rs.30,000.00. Ref 9876543210."
        res_cr = parse_sms_message(bob_credit)
        self.assertTrue(res_cr.is_valid)
        self.assertEqual(res_cr.bank, "bob")
        self.assertEqual(res_cr.account_suffix, "5678")
        self.assertEqual(res_cr.direction, "credit")
        self.assertEqual(res_cr.amount_paise, 1_000_000)

    def test_otp_rejection(self):
        otp_sms = "Your Kotak Bank net banking OTP is 839201. Valid for 10 minutes. Do not share this OTP with anyone."
        res = parse_sms_message(otp_sms)
        self.assertFalse(res.is_valid)
        self.assertTrue(res.is_otp)
        self.assertIn("rejected", res.error.lower())

    def test_duplicate_rejection(self):
        msg = "Sent Rs.500.00 from Kotak Bank AC X1234 to tea@upi on 15-10-26 Ref UTR998877. Bal Rs.14,500.00."
        res1 = ingest_sms_payload(self.user, msg)
        self.assertEqual(res1["status"], "success")

        # Ingesting the same payload again must be flagged duplicate
        res2 = ingest_sms_payload(self.user, msg)
        self.assertEqual(res2["status"], "duplicate")
        self.assertIn("already recorded", res2["error"])

    def test_api_ingest_endpoint_with_device_token(self):
        url = reverse("ingestion:api_ingest")
        payload = {
            "message": "Sent Rs.750.00 from Kotak Bank AC X1234 to grocer@upi on 18-10-26 Ref REF112233."
        }

        # 1. Without token -> 401
        res_no_auth = self.client.post(url, data=json.dumps(payload), content_type="application/json")
        self.assertEqual(res_no_auth.status_code, 401)

        # 2. With valid Bearer token -> 200
        headers = {"HTTP_AUTHORIZATION": f"Bearer {self.raw_token}"}
        res_auth = self.client.post(url, data=json.dumps(payload), content_type="application/json", **headers)
        self.assertEqual(res_auth.status_code, 200)
        data = res_auth.json()
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["amount_paise"], 75_000)

        # Verify transaction in database
        txn = Transaction.objects.get(pk=data["transaction_id"])
        self.assertEqual(txn.amount_paise, 75_000)
        self.assertEqual(txn.source, Transaction.Source.SMS)

        # Verify device token last used updated
        self.device_token.refresh_from_db()
        self.assertIsNotNone(self.device_token.last_used_at)

        # 3. With revoked token -> 401
        self.device_token.revoke()
        res_revoked = self.client.post(url, data=json.dumps(payload), content_type="application/json", **headers)
        self.assertEqual(res_revoked.status_code, 401)

    def test_privacy_decision_006_no_raw_sms_bodies_persisted(self):
        """Decision 006: Never record message bodies in ordinary application logs or DB."""
        secret_text = "Sent Rs.300.00 from Kotak Bank AC X1234 to secret_person on 19-10-26 Ref SECR9988."
        ingest_sms_payload(self.user, secret_text)

        event = IngestionEvent.objects.latest("created_at")
        self.assertEqual(event.outcome, IngestionEvent.Outcome.SUCCESS)
        self.assertFalse(hasattr(event, "raw_message"))
        self.assertFalse(hasattr(event, "body"))

        # Verify secret_person or full raw string is not stored in event fields
        event_dict = {f.name: getattr(event, f.name) for f in event._meta.fields}
        for k, val in event_dict.items():
            self.assertNotEqual(val, secret_text)
            self.assertNotIn("secret_person", str(val))
