# Decisions

Short records of rules that are expensive to change later. Each one lists the
decision, why, what it costs, and how it will be tested.

Status values: **accepted**, **provisional** (revisit before launch).

---

## 001 — Money is integer paise

**Status:** accepted

Amounts are stored and computed as `int` paise, never `float` or `Decimal` in
the database. Formatting happens once, in `core/formatting.py`.

**Why:** floats cannot represent 0.10 exactly, and rounding errors accumulate
across thousands of transactions. Integer paise makes every sum exact.

**Cost:** every display path must format explicitly, and division (splitting a
bill, for example) has to define a rounding rule.

**Tested by:** `FormatInrTests` in `core/tests.py`, `ParseInrTests` in
`core/test_parsing.py`, and the database check constraint
`txn_amount_positive` on `ledger.Transaction`.

---

## 002 — A checkpoint only covers transactions after its effective time

**Status:** accepted

For each account:

```
estimate = latest checkpoint amount
         + posted credits after checkpoint.effective_at
         − posted debits  after checkpoint.effective_at
```

A transaction is included only if `occurred_at > checkpoint.effective_at`.
A message that arrives *late* with an earlier `occurred_at` is ambiguous: the
bank may or may not have included it in the confirmed balance. It goes to
`needs_review` instead of silently changing the estimate.

**Why:** reception time cannot tell you what the bank had already posted.
Guessing produces balances that are wrong in a direction the user cannot see.

**Cost:** some transactions sit in review until the user decides. That is the
correct trade: a visible question beats a silent error.

**Tested by:** `EstimateBalanceTests` in `accounts/test_services.py`, including a
checkpoint of ₹10,000 with a ₹500 debit and a ₹2,000 credit giving ₹11,500, and
transactions exactly at or before the checkpoint not being counted twice.

---

## 003 — Refunds reduce expenses in the month received

**Status:** provisional

A refund is recorded with its actual receipt date. For category reports, it
reduces the expense total in the month it was received, and is also shown as a
separate "refunds" line.

**Why:** it keeps cash-flow dates and analytical treatment separate and is
simple to explain. Linking a refund back to the original purchase is more
accurate but requires the original to still exist and be identifiable.

**Cost:** a refund for a purchase made in a previous month distorts both months
slightly. The separate line makes that visible instead of hiding it.

**Revisit:** when a reliable link between refund and original transaction
exists (Phase 3), switch to attributing the refund to the original month.

---

## 004 — Confirmed future income and unpaid bills never double-count

**Status:** accepted (implementation in Phase 4)

`R` (remaining confirmed income) excludes anything already posted into `C`.
`U` (expected bills) excludes anything already paid and linked to a transaction.

**Why:** the allowance formula adds `R` and subtracts `U` from the current
balance. If either overlaps with money already in the account, the allowance is
inflated.

**Cost:** every planned cash-flow item needs a link to the transaction that
settles it.

**Tested by:** a table of cases in Phase 4, including a bill paid after the
plan was made: paying ₹1,000 must reduce both `C` and `U`, leaving the
allowance unchanged.

---

## 005 — Transfers and adjustments never count as income or spending

**Status:** accepted (Phase 3)

Own-account transfers, opening balances, balance corrections and unresolved
credits are excluded from net savings. A transfer records both legs together; a
single unmatched leg stays `unresolved` and is never treated as income.

**Why:** moving money between your own accounts does not make you richer, and a
missing leg is more likely a capture gap than a gift.

**Cost:** the user has to pair transfers, or accept an unresolved item on the
dashboard.

**Tested by:** `ledger/test_transfers.py`, including a one-legged transfer, a
tampered POST with someone else's account, and a transfer where only one leg
falls before that account's checkpoint.

---

## 006 — No raw SMS bodies in logs or long-term storage

**Status:** accepted (implementation in Phase 6)

Only parsed fields, a parser version and a short fingerprint are retained.
Message text is not written to application logs. OTP and non-bank messages are
filtered on the device where possible and rejected server-side regardless.

**Why:** SMS bodies contain account details and OTPs, and logs are the easiest
place for sensitive data to leak.

**Cost:** harder debugging of parser failures. Mitigated by keeping a short,
redacted excerpt only when the user explicitly reports a parsing problem.

**Tested by:** Phase 6 tests asserting that ingestion never writes message text
to the database or the log handler.

---

## 007 — Movements before the last confirmed balance wait for review

**Status:** accepted

A transaction dated at or before the account's latest confirmed checkpoint is
ambiguous: the bank may already have included it in that figure. It is saved as
`needs_review` instead of silently moving the estimate, and the user decides at
`/transactions/review/`:

- **"Already included"** → `posted`. The estimate is unchanged: the checkpoint
  rule already treats the movement as inside the confirmed figure.
- **"Ignore"** → `ignored`. Excluded from estimates and reports, kept visible
  with a muted chip so it can be undone.
- **"Not included"** → no server action. The honest fix is to confirm the
  current balance for that account, which re-anchors the estimate from now.

**Why:** guessing produces balances that are wrong in a direction the user
cannot see. A visible question beats a silent error.

**Cost:** some transactions sit in review until the user decides.

**Tested by:** `StatusRuleTests`, `ReviewPageTests` and `ReviewDecideTests` in
`ledger/test_review.py`, plus the add-transaction flagging tests in
`ledger/test_transactions.py` and the live two-width browser check of the
review flow.
