# Application guide

See the [project README](../README.md) for setup, configuration and the folder map.

## How a request becomes a page

1. `manage.py runserver` sets `DJANGO_SETTINGS_MODULE=config.settings`.
2. `config/settings.py` defines installed apps, database, template and static
   directories, and the timezone (`Asia/Kolkata`, stored in UTC).
3. `config/urls.py` matches `/`, `/accounts/`, `/transactions/` and `/plan/`,
   and delegates to the apps.
4. `core/urls.py` maps `/` to the public home page and `/dashboard/` to the
   authenticated dashboard. Anonymous dashboard visitors are sent to login.
5. `accounts/views.py` handles signup: it creates the user, first bank account, starting balance and starter
   categories in one transaction, then logs them in.
6. `core/views.py` asks `accounts/services.py` for the account cards, then
   builds a context dictionary and calls `render()`.
7. `templates/dashboard.html` extends `templates/base.html` and pulls styles
   from `static/css/app.css`.

## Accounts and login

| URL | What it does |
| --- | --- |
| `/accounts/signup/` | Create a user, first bank account and starter categories, then log in |
| `/accounts/login/` | Log in; honours a safe `?next=` |
| `/accounts/logout/` | Log out (POST only, as Django 5 requires) |
| `/accounts/add/` | Add an account and the balance it shows today |
| `/accounts/<id>/confirm/` | Record the balance the bank shows now |

Passwords go through Django's four validators (similarity, length, common
password, all-numeric). The starter categories are seeded once per user and are
safe to re-run.

Adding an account stores the bank, a nickname, the account type and the last
four digits. The full account number is never asked for. The balance you enter
becomes the account's first checkpoint, so every later estimate has an anchor.

## How the estimate is calculated

```
estimate = latest checkpoint amount
         + posted credits after checkpoint.effective_at
         − posted debits  after checkpoint.effective_at
```

A transaction exactly at the checkpoint time is treated as already included.
Transactions marked `needs_review` are excluded until someone decides. The rule
lives in `accounts/services.py` so the dashboard and later reports cannot drift
apart.

### Confirming a balance again

Every account card has **Confirm balance**. Type the figure your bank app shows
and save. That becomes the newest checkpoint, so the estimate becomes exactly
what you typed, and only movements recorded after that moment adjust it.

Backdating is allowed and is stored as history: if you confirm a time older than
your newest checkpoint, the estimate keeps using the newer one. A time in the
future is rejected, because a balance cannot be known before it exists.

## Transactions

| URL | What it does |
| --- | --- |
| `/transactions/` | Your transactions, newest first, 50 per page |
| `/transactions/add/` | Record one movement by hand |
| `/transactions/review/` | Movements waiting for your review decision |
| `/transactions/transfers/` | Transfers between your own accounts |
| `/transactions/transfers/add/` | Move money between two accounts you own |

Amounts are typed as text, so `1,23,456.78` and `1234.5` both work and become
exact integer paise. A transaction is posted the moment you save it, because you
are the one asserting it happened. With a single account it is preselected;
with more, the dropdown lists only your own accounts.

The list filters on `?from=`, `?to=` (both `YYYY-MM-DD`), `?category=` and
`?account=`. The `to` day is included in full. A filter that cannot be read — a
bad date, someone else's category id — shows a message and returns no rows
rather than quietly showing everything, so an empty page always means what it
says. Paging keeps the current filters.

### Review queue

A transaction dated at or before your last confirmed balance for that account
is ambiguous: the bank may already have counted it in that figure. It is saved
as `needs_review` and waits for your decision at `/transactions/review/`:

- **Already included** → marked `posted`. Your balance is unchanged: the time
  rule already treats it as inside the checkpoint.
- **Ignore** → marked `ignored`. It stays out of your balance and reports,
  kept visible with a muted chip so it can be undone later.
- **Not included** → no server action. Confirm the current balance for that
  account instead; that re-anchors the estimate from now.

The dashboard shows how many transactions are waiting and links straight to
the queue.

### Transfers

A transfer records a debit on one account and a credit on another account you
own, in one atomic step. Both legs are linked, so reports never treat the
movement as income or spending.

Each leg gets the review status its own account implies: the sending bank and
the receiving bank can learn about the movement at different times, so one leg
can be posted while the other waits.

If a leg is ever removed, the transfer is shown as unresolved. A credit marked
with the **Transfer** category but with no matching leg is listed under
**Unpaired transfer credits** and is never counted as income. Moving money
between your own accounts cannot make you richer, and a missing leg is far more
likely a capture gap than a gift.

## Goals and the spending allowance

| URL | What it does |
| --- | --- |
| `/plan/` | This month's allowance, goals, expected income and bills |
| `/plan/goals/` | All goals, with a quick "add to this goal" form |
| `/plan/goals/add/` | Create a goal |
| `/plan/goals/<id>/edit/` | Change a goal's name, target or saved amount |
| `/plan/income/add/` | Add income you are still waiting for |
| `/plan/bills/add/` | Add a bill you still have to pay |

The allowance answers one question: how much can I spend for the rest of this
month?

```
allowance = estimated balances
          + income still expected
          − bills still unpaid
```

Only items dated on or before the last day of this month are counted. Overdue
items stay in, because they are still expected. A settled item is left out of
its side of the formula, so nothing is counted twice:

- **Mark received** records a credit and marks the income received. The balance
  goes up and expected income goes down by the same amount.
- **Mark paid** records a debit and marks the bill paid. The balance goes down
  and unpaid bills go down by the same amount.

The allowance therefore does not jump when you pay a bill. It is still an
estimate: a settlement dated before the account's confirmed balance waits in the
review queue like any other movement.

A goal is tracked by hand. The app cannot see which rupees are earmarked, so you
enter how much you have set aside and add to it when you move money on purpose.
A goal never changes an account balance.

## Development states

`?state=empty`, `?state=loading` and `?state=error` preview the three dashboard
states. They only work while `DEBUG=True`; the view ignores the parameter in
production, and a test enforces that.

The loading state is a static preview. Nothing is fetched yet.

## Motion

GSAP 3.15.0 is vendored in `static/vendor/gsap/` (see the README there for the
version, hash and license). It loads only on the dashboard.

- CSS owns layout, colour and instant feedback.
- GSAP owns timing: entrance stagger and the progress-bar fill.
- Without JavaScript, or with GSAP blocked, the page renders fully.
- With `prefers-reduced-motion: reduce`, nothing animates.
- Rupee amounts never count up; only containers and bars move.

## Decisions

See `docs/decisions.md` for the money, checkpoint, refund, transfer and
allowance rules.

## Roadmap

| Phase | Scope | Status |
| --- | --- | --- |
| 1 | Dashboard, layout, states, motion | Done |
| 2 | Accounts, auth, manual transactions | Done |
| 3 | Estimated balances, corrections, transfers | Done |
| 4 | Goals and spending allowance | Done |
| 5 | Monthly reports and CSV | Done |
| 6 | SMS parsing, then iPhone capture | Done — experimental capture |
| 7 | AI coach | Done — opt-in and bounded |
| 8 | Private pilot, Postgres, deployment | In progress |

## Phone checklist

Run through this after any layout change:

- [ ] No horizontal scrolling at 320–430px wide
- [ ] Bottom navigation reachable one-handed
- [ ] Text readable without pinch-zooming
- [ ] Tap targets at least 44px tall
- [ ] Dark mode follows the system setting
- [ ] With reduced motion on, content appears without animating
- [ ] The estimate disclaimer is visible, not buried


### First-visit journey

Visitors land on `/`, with login and registration links. Registration collects
first name, optional last name, email, login credentials, and the first bank
account (bank, nickname, type, last four digits, current balance). User, account,
balance checkpoint and starter categories are created in one transaction.
Successful signup and login open `/dashboard/`; logout returns to the public home.

The dashboard prompts users to set a goal. Saving a new goal opens its action plan
at `/coach/goals/<id>/`. Savings pace is calculated in integer paise. Without a
future deadline, the plan asks for one; completed goals are recognized. Long-term
goals do not reserve funds automatically: the plan links to monthly savings.

Optional AI prioritization requires explicit consent, `GROQ_API_KEY` and the
existing daily AI budget. Groq receives verified steps with calculated amounts,
never names or bank identifiers. Responses can only reorder those steps; invalid
responses and provider failures fall back to clearly labeled local guidance.
