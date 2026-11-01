from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.services import estimate_balance
from ledger.models import Transaction
from ledger.services import (
    apply_review_decision,
    conflict_checkpoint,
    status_for_transaction,
)

from .test_transactions import make_user, make_account, at

User = get_user_model()


def add_review_txn(account, when, description="Late debit"):
    return Transaction.objects.create(
        account=account,
        occurred_at=when,
        amount_paise=50_000,
        direction=Transaction.Direction.DEBIT,
        description=description,
        status=Transaction.Status.NEEDS_REVIEW,
    )


class StatusRuleTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.account = make_account(self.user)
        self.checkpoint_time = at(2026, 10, 15, 12, 0)
        self.account.checkpoints.update(effective_at=self.checkpoint_time)

    def test_after_checkpoint_is_posted(self):
        self.assertEqual(
            status_for_transaction(self.account, self.checkpoint_time + timedelta(hours=1)),
            Transaction.Status.POSTED,
        )

    def test_at_checkpoint_waits_for_review(self):
        self.assertEqual(
            status_for_transaction(self.account, self.checkpoint_time),
            Transaction.Status.NEEDS_REVIEW,
        )

    def test_before_checkpoint_waits_for_review(self):
        self.assertEqual(
            status_for_transaction(self.account, self.checkpoint_time - timedelta(days=1)),
            Transaction.Status.NEEDS_REVIEW,
        )

    def test_no_checkpoint_posts_at_once(self):
        self.account.checkpoints.all().delete()

        self.assertEqual(
            status_for_transaction(self.account, timezone.now()),
            Transaction.Status.POSTED,
        )

    def test_conflict_checkpoint_is_the_latest_one(self):
        latest = self.account.checkpoints.create(
            amount_paise=2_000_000,
            effective_at=at(2026, 10, 20),
        )

        txn = add_review_txn(self.account, at(2026, 10, 18))

        self.assertEqual(conflict_checkpoint(txn), latest)

    def test_conflict_checkpoint_is_none_without_checkpoints(self):
        self.account.checkpoints.all().delete()

        txn = add_review_txn(self.account, at(2026, 10, 18))

        self.assertIsNone(conflict_checkpoint(txn))


class ReviewPageTests(TestCase):
    def setUp(self):
        self.asha = make_user("asha")
        self.ravi = make_user("ravi")
        self.asha_account = make_account(self.asha, suffix="1111")
        self.ravi_account = make_account(self.ravi, suffix="9999")
        self.asha_account.checkpoints.update(effective_at=at(2026, 10, 20))
        self.ravi_account.checkpoints.update(effective_at=at(2026, 10, 20))
        self.asha_txn = add_review_txn(self.asha_account, at(2026, 10, 14), "Asha late lunch")
        self.ravi_txn = add_review_txn(self.ravi_account, at(2026, 10, 14), "Ravi late lunch")
        self.client.force_login(self.asha)

    def test_requires_login(self):
        self.client.logout()

        response = self.client.get(reverse("ledger:review"))

        self.assertRedirects(
            response, f"/accounts/login/?next={reverse('ledger:review')}"
        )

    def test_lists_only_own_rows(self):
        response = self.client.get(reverse("ledger:review"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Asha late lunch")
        self.assertNotContains(response, "Ravi late lunch")

    def test_shows_the_conflicting_checkpoint(self):
        response = self.client.get(reverse("ledger:review"))

        self.assertContains(response, "20 Oct")
        self.assertContains(response, "₹10,000.00")
        self.assertContains(response, "Kotak salary")

    def test_ignored_rows_do_not_appear(self):
        self.asha_txn.status = Transaction.Status.IGNORED
        self.asha_txn.save()

        response = self.client.get(reverse("ledger:review"))

        self.assertNotContains(response, "Asha late lunch")
        self.assertContains(response, "Nothing waiting for your decision.")

    def test_posted_rows_do_not_appear(self):
        self.asha_txn.status = Transaction.Status.POSTED
        self.asha_txn.save()

        response = self.client.get(reverse("ledger:review"))

        self.assertNotContains(response, "Asha late lunch")


class ReviewDecideTests(TestCase):
    def setUp(self):
        self.asha = make_user("asha")
        self.ravi = make_user("ravi")
        self.asha_account = make_account(self.asha, suffix="1111")
        self.ravi_account = make_account(self.ravi, suffix="9999")
        self.asha_account.checkpoints.update(effective_at=at(2026, 10, 20))
        self.ravi_account.checkpoints.update(effective_at=at(2026, 10, 20))
        self.txn = add_review_txn(self.asha_account, at(2026, 10, 14), "Asha late lunch")
        self.client.force_login(self.asha)
        self.url = reverse("ledger:review_decide", args=[self.txn.pk])

    def test_decide_requires_post(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 405)

    def test_approve_marks_posted_and_leaves_estimate_unchanged(self):
        before = estimate_balance(self.asha_account, self.asha_account.checkpoints.first())

        response = self.client.post(self.url, {"decision": "approve"})

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("ledger:review"))
        self.txn.refresh_from_db()
        self.assertEqual(self.txn.status, Transaction.Status.POSTED)
        self.assertEqual(
            estimate_balance(self.asha_account, self.asha_account.checkpoints.first()),
            before,
        )
        followed = self.client.get(reverse("ledger:review"))
        self.assertContains(followed, "already included")

    def test_ignore_marks_ignored_and_leaves_estimate_unchanged(self):
        before = estimate_balance(self.asha_account, self.asha_account.checkpoints.first())

        response = self.client.post(self.url, {"decision": "ignore"})

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("ledger:review"))
        self.txn.refresh_from_db()
        self.assertEqual(self.txn.status, Transaction.Status.IGNORED)
        self.assertEqual(
            estimate_balance(self.asha_account, self.asha_account.checkpoints.first()),
            before,
        )
        followed = self.client.get(reverse("ledger:review"))
        self.assertContains(followed, "Ignored")

    def test_ignored_row_is_excluded_from_the_queue(self):
        self.client.post(self.url, {"decision": "ignore"})

        response = self.client.get(reverse("ledger:review"))

        self.assertNotContains(response, "Asha late lunch")

    def test_another_users_row_is_not_found(self):
        ravi_txn = add_review_txn(self.ravi_account, at(2026, 10, 14), "Ravi late lunch")
        url = reverse("ledger:review_decide", args=[ravi_txn.pk])

        response = self.client.post(url, {"decision": "approve"})

        self.assertEqual(response.status_code, 404)

    def test_invalid_decision_shows_error(self):
        response = self.client.post(self.url, {"decision": "maybe"})

        self.assertEqual(response.status_code, 302)
        self.txn.refresh_from_db()
        self.assertEqual(self.txn.status, Transaction.Status.NEEDS_REVIEW)

        from django.contrib.messages import get_messages

        texts = [str(message) for message in get_messages(response.wsgi_request)]
        self.assertIn("Choose 'Already included' or 'Ignore'.", texts)

    def test_cannot_decide_an_already_posted_row(self):
        self.txn.status = Transaction.Status.POSTED
        self.txn.save()

        response = self.client.post(self.url, {"decision": "approve"})

        self.assertEqual(response.status_code, 404)

    def test_review_count_drops_after_deciding_all(self):
        response = self.client.get("/dashboard/")

        self.assertContains(response, "1 transaction needs review")

        self.client.post(self.url, {"decision": "approve"})

        response = self.client.get("/dashboard/")

        self.assertNotContains(response, "needs review")


class ApplyDecisionTests(TestCase):
    def test_approve_and_ignore_return_expected_statuses(self):
        self.assertEqual(apply_review_decision(None, "approve"), Transaction.Status.POSTED)  # noqa: ARG005
        self.assertEqual(apply_review_decision(None, "ignore"), Transaction.Status.IGNORED)  # noqa: ARG005

    def test_unknown_decision_raises(self):
        with self.assertRaises(ValueError):
            apply_review_decision(None, "delete")  # noqa: ARG005
