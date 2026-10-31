# Finance

A mobile-first personal finance app for INR savings and current accounts.

Balances are **estimates**. They are built from a balance you confirm, plus the
transactions recorded since then. There is no bank connection, no money moves,
and transaction capture is not guaranteed to be complete.

## Status

Phase 3 in progress. Accounts, authentication and manual transactions are done
(Phase 2). You can now confirm a balance whenever you check your bank app, which
re-anchors the estimate from that moment. The review queue is next.

## Requirements

- Python 3.14 (`py -3.14` on Windows)
- Git

## Setup

```powershell
cd C:\Users\Lenovo\Desktop\finance
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py runserver
```

Open http://127.0.0.1:8000/.

On this machine `python` resolves to MSYS2's build, which has no pip. Always call
`.\.venv\Scripts\python.exe` directly, or activate the venv first.

### Testing on a phone over Wi-Fi

```powershell
ipconfig                     # note the IPv4 address
$env:DJANGO_ALLOWED_HOSTS="192.168.1.23"
.\.venv\Scripts\python.exe manage.py runserver 0.0.0.0:8000
```

Then open `http://192.168.1.23:8000/` on the phone. Both devices must be on the
same network. Set the environment variable fresh in each new terminal.

## Tests

```powershell
.\.venv\Scripts\python.exe manage.py test
```

`core/test_security.py` checks the multi-user boundary: anonymous access,
another user's ids in filters and form posts, CSRF, and password storage.

## Layout

```
config/            settings, root URLs, WSGI/ASGI entry points
core/              dashboard view, money parsing and formatting, tests
accounts/          BankAccount, BalanceCheckpoint, signup/login/logout
ledger/            Category, Transaction, filters, starter categories
templates/         base.html, dashboard.html, accounts/, ledger/, partials/
static/            css/, js/, vendor/gsap/
docs/              decisions.md
```

Apps live at the repository root rather than under `apps/`.

## How a request becomes a page

1. `manage.py runserver` sets `DJANGO_SETTINGS_MODULE=config.settings`.
2. `config/settings.py` defines installed apps, database, template and static
   directories, and the timezone (`Asia/Kolkata`, stored in UTC).
3. `config/urls.py` matches `/`, `/accounts/` and `/transactions/`, and
   delegates to the apps.
4. `core/urls.py` maps `""` to `core.views.dashboard`, which requires login.
   Anonymous visitors are sent to `/accounts/login/?next=/`.
5. `accounts/views.py` handles signup: it creates the user and their starter
   categories in one transaction, then logs them in.
6. `core/views.py` asks `accounts/services.py` for the account cards, then
   builds a context dictionary and calls `render()`.
7. `templates/dashboard.html` extends `templates/base.html` and pulls styles
   from `static/css/app.css`.

## Accounts and login

| URL | What it does |
| --- | --- |
| `/accounts/signup/` | Create a user and 14 starter categories, then log in |
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

Amounts are typed as text, so `1,23,456.78` and `1234.5` both work and become
exact integer paise. A transaction is posted the moment you save it, because you
are the one asserting it happened. With a single account it is preselected;
with more, the dropdown lists only your own accounts.

The list filters on `?from=`, `?to=` (both `YYYY-MM-DD`), `?category=` and
`?account=`. The `to` day is included in full. A filter that cannot be read — a
bad date, someone else's category id — shows a message and returns no rows
rather than quietly showing everything, so an empty page always means what it
says. Paging keeps the current filters.

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

See `docs/decisions.md` for the money, checkpoint, refund and transfer rules.

## Roadmap

| Phase | Scope | Status |
| --- | --- | --- |
| 1 | Dashboard, layout, states, motion | Done |
| 2 | Accounts, auth, manual transactions | In progress |
| 3 | Estimated balances, corrections, transfers | Planned |
| 4 | Goals and spending allowance | Planned |
| 5 | Monthly reports and CSV | Planned |
| 6 | SMS parsing, then iPhone capture | Planned |
| 7 | AI coach | Planned |
| 8 | Private pilot, Postgres, deployment | Planned |

## Phone checklist

Run through this after any layout change:

- [ ] No horizontal scrolling at 320–430px wide
- [ ] Bottom navigation reachable one-handed
- [ ] Text readable without pinch-zooming
- [ ] Tap targets at least 44px tall
- [ ] Dark mode follows the system setting
- [ ] With reduced motion on, content appears without animating
- [ ] The estimate disclaimer is visible, not buried
