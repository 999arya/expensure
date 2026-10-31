# Finance

A mobile-first personal finance app for INR savings and current accounts.

Balances are **estimates**. They are built from a balance you confirm, plus the
transactions recorded since then. There is no bank connection, no money moves,
and transaction capture is not guaranteed to be complete.

## Status

Phase 1 complete: responsive dashboard, fictional data, motion layer.
No database writes and no authentication yet.

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

## Layout

```
config/            settings, root URLs, WSGI/ASGI entry points
core/              dashboard view, demo data, formatting, tests
templates/         base.html, dashboard.html, partials/
static/            css/, js/, vendor/gsap/
docs/              decisions.md
```

Apps live at the repository root rather than under `apps/`.

## How a request becomes a page

1. `manage.py runserver` sets `DJANGO_SETTINGS_MODULE=config.settings`.
2. `config/settings.py` defines installed apps, database, template and static
   directories, and the timezone (`Asia/Kolkata`, stored in UTC).
3. `config/urls.py` matches `/` and delegates to `core.urls`.
4. `core/urls.py` maps `""` to `core.views.dashboard`.
5. `core/views.py` builds a context dictionary and calls `render()`.
6. `templates/dashboard.html` extends `templates/base.html` and pulls styles
   from `static/css/app.css`.

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
| 2 | Accounts, auth, manual transactions | Next |
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
