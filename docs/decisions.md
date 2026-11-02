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

**Tested by:** `FormatInrTests` in `core/tests/test_features.py`, `ParseInrTests` in
`core/tests/test_parsing.py`, and the database check constraint
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

**Tested by:** `EstimateBalanceTests` in `accounts/tests/test_services.py`, including a
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

**Revisit:** after monthly reports (Phase 5), if a refund from a previous month
distorts month totals in practice.

---

## 004 — Confirmed future income and unpaid bills never double-count

**Status:** accepted (Phase 4)

`R` (remaining confirmed income) excludes anything already posted into `C`.
`U` (expected bills) excludes anything already paid and linked to a transaction.

**Why:** the allowance formula adds `R` and subtracts `U` from the current
balance. If either overlaps with money already in the account, the allowance is
inflated.

**Cost:** every planned cash-flow item needs a link to the transaction that
settles it.

**Tested by:** `SettleBillTests` and `SettleIncomeTests` in `planning/tests/test_features.py`,
including a bill paid after the plan was made: paying ₹1,000 lowers both `C` and
`U` and leaves the allowance unchanged. The same tests cover a backdated
settlement waiting for review and a tampered POST against another user's item.

---

## 005 — Transfers and adjustments never count as income or spending

**Status:** accepted (Phase 3)

Own-account transfers, opening balances, balance corrections and unresolved
credits are excluded from net savings. A transfer records both legs together; a
single unmatched leg stays `unresolved` and is never treated as income.

**Why:** moving money between your own accounts does not make you richer, and a
missing leg is more likely a recording gap than a gift.

**Cost:** the user has to pair transfers, or accept an unresolved item on the
dashboard.

**Tested by:** `ledger/tests/test_transfers.py`, including a one-legged transfer, a
tampered POST with someone else's account, and a transfer where only one leg
falls before that account's checkpoint.

---

## 006 — No raw statement files in logs or long-term storage

**Status:** accepted (Phase 6; revised for CSV import)

CSV statements are parsed in memory. Only normalized fields, a file hash, counts
and batch metadata are retained. The uploaded file is not written to disk.
Preview rows exist only until the import is confirmed or cancelled. Row
descriptions are bank narration text, never credentials or message bodies.

**Why:** a statement file contains a full account history and account number,
and logs are the easiest place for sensitive data to leak.

**Cost:** a parser failure cannot be replayed from the original file; the user
must upload again. Mitigated by a preview that shows which rows failed and why.

**Tested by:** `ingestion/tests/test_statement_import.py`, including staging,
duplicate detection, confirm/cancel/undo and file-type/size rejection.

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
`ledger/tests/test_review.py`, plus the add-transaction flagging tests in
`ledger/tests/test_transactions.py` and the live two-width browser check of the
review flow.

---

## 008 — Imported statement rows are bank-authoritative

**Status:** accepted

Rows from a CSV statement are treated as already having happened at the bank. A
row after the latest checkpoint is posted normally. A row before the checkpoint
is posted for reports but does not move the estimate, because the confirmed
balance already includes it. A date-only row on the same local day as the
checkpoint waits for review, because its exact time is unknown.

**Why:** the statement is more reliable than a guessed time, but counting a
pre-checkpoint row again would double-count it.

**Cost:** importing an old statement adds report history without changing
today's estimate, which can look surprising. The account page explains which
checkpoint the estimate is anchored to.

**Tested by:** `StatementPostingTests` in
`ingestion/tests/test_statement_import.py`.
