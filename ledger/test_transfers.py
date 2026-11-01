from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.services import estimate_balance, latest_checkpoint
from ledger.defaults import seed_default_categories
from ledger.models import Transaction, Transfer

from .test_transactions import add_txn, at, make_account, make_user

User = get_user_model()


def balance(account):
    return estimate_balance(account, latest_checkpoint(account))


class CreateTransferTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.kotak = make_account(self.user, nickname="Kotak salary", suffix="4821")
        self.bob = make_account(self.user, nickname="BoB savings", suffix="1111")

    def test_creates_two_linked_legs(self):
        from ledger.services import create_transfer

        transfer = create_transfer(
            owner=self.user,
            from_account=self.kotak,
            to_account=self.bob,
            amount_paise=50_000,
            occurred_at=at(2026, 10, 20),
            description="Move to savings",
        )

        legs = transfer.legs.order_by("direction")
        self.assertEqual(legs.count(), 2)
        debit = legs.get(direction=Transaction.Direction.DEBIT)
        credit = legs.get(direction=Transaction.Direction.CREDIT)
        self.assertEqual(debit.account, self.kotak)
        self.assertEqual(credit.account, self.bob)
        self.assertEqual(debit.amount_paise, 50_000)
        self.assertEqual(credit.amount_paise, 50_000)
        self.assertEqual(debit.transfer, transfer)
        self.assertEqual(credit.transfer, transfer)
        self.assertIn("Kotak salary", credit.description)
        self.assertIn("BoB savings", debit.description)

    def test_estimates_move_in_opposite_directions(self):
        from ledger.services import create_transfer

        before_kotak = balance(self.kotak)
        before_bob = balance(self.bob)

        create_transfer(
            owner=self.user,
            from_account=self.kotak,
            to_account=self.bob,
            amount_paise=50_000,
            occurred_at=at(2026, 10, 20),
        )

        self.assertEqual(balance(self.kotak), before_kotak - 50_000)
        self.assertEqual(balance(self.bob), before_bob + 50_000)

    def test_a_late_leg_waits_for_review_on_its_own_account(self):
        from ledger.services import create_transfer

        self.bob.checkpoints.update(effective_at=at(2026, 10, 25))

        transfer = create_transfer(
            owner=self.user,
            from_account=self.kotak,
            to_account=self.bob,
            amount_paise=50_000,
            occurred_at=at(2026, 10, 20),
        )

        debit = transfer.legs.get(direction=Transaction.Direction.DEBIT)
        credit = transfer.legs.get(direction=Transaction.Direction.CREDIT)
        self.assertEqual(debit.status, Transaction.Status.POSTED)
        self.assertEqual(credit.status, Transaction.Status.NEEDS_REVIEW)

    def test_same_account_is_rejected(self):
        from ledger.services import create_transfer

        with self.assertRaises(ValueError):
            create_transfer(
                owner=self.user,
                from_account=self.kotak,
                to_account=self.kotak,
                amount_paise=50_000,
                occurred_at=at(2026, 10, 20),
            )

    def test_another_users_account_is_rejected(self):
        from ledger.services import create_transfer

        other = make_user("ravi")
        ravi_account = make_account(other, nickname="Ravi", suffix="9999")

        with self.assertRaises(ValueError):
            create_transfer(
                owner=self.user,
                from_account=self.kotak,
                to_account=ravi_account,
                amount_paise=50_000,
                occurred_at=at(2026, 10, 20),
            )

    def test_zero_amount_is_rejected(self):
        from ledger.services import create_transfer

        with self.assertRaises(ValueError):
            create_transfer(
                owner=self.user,
                from_account=self.kotak,
                to_account=self.bob,
                amount_paise=0,
                occurred_at=at(2026, 10, 20),
            )


class TransferFormTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.kotak = make_account(self.user, nickname="Kotak salary", suffix="4821")
        self.bob = make_account(self.user, nickname="BoB savings", suffix="1111")
        self.client.force_login(self.user)

    def form_data(self, **overrides):
        data = {
            "from_account": self.kotak.pk,
            "to_account": self.bob.pk,
            "amount": "1,500",
            "occurred_at": "2026-10-20T09:30",
            "description": "Monthly move",
        }
        data.update(overrides)
        return data

    def test_valid_form_creates_transfer_and_redirects(self):
        response = self.client.post(reverse("ledger:transfer_add"), self.form_data())

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("ledger:transfers"))
        self.assertEqual(Transfer.objects.count(), 1)
        transfer = Transfer.objects.get()
        self.assertEqual(transfer.amount_paise, 150_000)
        self.assertEqual(transfer.legs.count(), 2)

    def test_same_account_shows_an_error(self):
        response = self.client.post(
            reverse("ledger:transfer_add"),
            self.form_data(to_account=self.kotak.pk),
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("two different accounts", str(response.context["form"].errors))
        self.assertEqual(Transfer.objects.count(), 0)

    def test_another_users_account_is_rejected(self):
        other = make_user("ravi")
        ravi_account = make_account(other, nickname="Ravi", suffix="9999")

        response = self.client.post(
            reverse("ledger:transfer_add"),
            self.form_data(to_account=ravi_account.pk),
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["form"].is_valid())
        self.assertEqual(Transfer.objects.count(), 0)

    def test_zero_amount_is_rejected(self):
        response = self.client.post(
            reverse("ledger:transfer_add"), self.form_data(amount="0")
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("amount", response.context["form"].errors)
        self.assertEqual(Transfer.objects.count(), 0)

    def test_needs_two_accounts(self):
        self.bob.delete()

        response = self.client.get(reverse("ledger:transfer_add"))

        self.assertRedirects(response, reverse("accounts:add"))

    def test_requires_login(self):
        self.client.logout()

        response = self.client.get(reverse("ledger:transfer_add"))

        self.assertRedirects(
            response,
            f"/accounts/login/?next={reverse('ledger:transfer_add')}",
        )


class TransferListTests(TestCase):
    def setUp(self):
        self.asha = make_user("asha")
        self.ravi = make_user("ravi")
        seed_default_categories(self.asha)
        seed_default_categories(self.ravi)
        self.asha_kotak = make_account(self.asha, nickname="Asha Kotak", suffix="1111")
        self.asha_bob = make_account(self.asha, nickname="Asha BoB", suffix="2222")
        self.ravi_kotak = make_account(self.ravi, nickname="Ravi Kotak", suffix="9999")
        self.ravi_bob = make_account(self.ravi, nickname="Ravi BoB", suffix="8888")

        from ledger.services import create_transfer

        self.transfer = create_transfer(
            owner=self.asha,
            from_account=self.asha_kotak,
            to_account=self.asha_bob,
            amount_paise=25_000,
            occurred_at=at(2026, 10, 20),
            description="Asha move",
        )
        create_transfer(
            owner=self.ravi,
            from_account=self.ravi_kotak,
            to_account=self.ravi_bob,
            amount_paise=99_000,
            occurred_at=at(2026, 10, 20),
            description="Ravi move",
        )
        self.client.force_login(self.asha)

    def test_lists_only_own_transfers(self):
        response = self.client.get(reverse("ledger:transfers"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Asha move")
        self.assertNotContains(response, "Ravi move")

    def test_requires_login(self):
        self.client.logout()

        response = self.client.get(reverse("ledger:transfers"))

        self.assertRedirects(
            response, f"/accounts/login/?next={reverse('ledger:transfers')}"
        )

    def test_broken_transfer_is_flagged(self):
        self.transfer.legs.filter(direction=Transaction.Direction.CREDIT).delete()

        response = self.client.get(reverse("ledger:transfers"))

        self.assertContains(response, "Unresolved")
        self.assertContains(response, "1 transfer unresolved")

    def test_unpaired_credit_is_listed_and_counted(self):
        Transaction.objects.create(
            account=self.asha_kotak,
            occurred_at=at(2026, 10, 21),
            amount_paise=70_000,
            direction=Transaction.Direction.CREDIT,
            category=self.asha.categories.get(kind="transfer"),
            description="Mystery credit",
            status=Transaction.Status.POSTED,
        )

        response = self.client.get(reverse("ledger:transfers"))

        self.assertContains(response, "Mystery credit")
        self.assertContains(response, "Unpaired")
        self.assertContains(response, "1 transfer unresolved")

    def test_ignored_unpaired_credit_is_not_counted(self):
        Transaction.objects.create(
            account=self.asha_kotak,
            occurred_at=at(2026, 10, 21),
            amount_paise=70_000,
            direction=Transaction.Direction.CREDIT,
            category=self.asha.categories.get(kind="transfer"),
            description="Mystery credit",
            status=Transaction.Status.IGNORED,
        )

        response = self.client.get(reverse("ledger:transfers"))

        self.assertNotContains(response, "Mystery credit")
        self.assertEqual(response.context["unresolved_count"], 0)

    def test_dashboard_shows_the_unresolved_notice(self):
        Transaction.objects.create(
            account=self.asha_kotak,
            occurred_at=at(2026, 10, 21),
            amount_paise=70_000,
            direction=Transaction.Direction.CREDIT,
            category=self.asha.categories.get(kind="transfer"),
            description="Mystery credit",
            status=Transaction.Status.POSTED,
        )

        response = self.client.get("/")

        self.assertContains(response, "1 transfer unresolved")
        self.assertContains(response, "Review transfers")

    def test_dashboard_stays_quiet_when_everything_is_paired(self):
        response = self.client.get("/")

        self.assertNotContains(response, "unresolved")
