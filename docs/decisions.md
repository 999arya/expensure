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

**Tested by:** `FormatInrTests` in `core/tests.py`; database check constraint
`txn_amount_positive` in Phase 2.

---

## 002 — A checkpoint only covers transactions after its effective time

**Status:** accepted (implementation in Phase 3)

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

**Tested by:** the Phase 3 cases — ₹10,000 checkpoint, ₹500 debit, ₹2,000 credit
gives ₹11,500; replaying a transaction changes nothing; an older transaction
already covered by the checkpoint does not move the estimate.

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

**Status:** accepted (implementation in Phase 3)

Own-account transfers, opening balances, balance corrections and unresolved
credits are excluded from net savings. A transfer needs both legs confirmed; a
single unmatched leg stays `unresolved` and is never treated as income.

**Why:** moving money between your own accounts does not make you richer, and a
missing leg is more likely a capture gap than a gift.

**Cost:** the user has to pair transfers, or accept an unresolved item on the
dashboard.

**Tested by:** Phase 3 transfer tests, including a one-legged transfer.

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
