# Expensure

A Django personal finance app for tracking INR balances, spending, savings goals,
and optional Groq-powered guidance. Balances are estimates from manually confirmed
balances and recorded transactions; the app does not connect to or move money at banks.

## Run locally

Use Python 3.14. From the project folder on Windows:

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py runserver
```

Open http://127.0.0.1:8000/. Visitors see the public home page; signup collects
personal details and a first bank account. Login opens the dashboard, where users
can set goals and view their action plans.

## Local configuration

Create a `.env` file at the project root (never commit it):

```dotenv
GROQ_API_KEY=your-key-here
GROQ_MODEL=llama-3.1-8b-instant
AI_DAILY_LIMIT=20
```

Django loads this file automatically; existing environment variables take priority.
Restart the server after changes. The AI limit is per user per local calendar day,
shared across coaching features. Cached responses do not use another call;
provider attempts can count even if they fail. Local guidance remains available.
Groq's organization-wide quotas are separate from this app limit.

## Project map

| Folder | Responsibility |
| --- | --- |
| [config](config/) | Development, production and build settings; root routes |
| [accounts](accounts/) | Signup, bank accounts, balances and data controls |
| [core](core/) | Public home, dashboard, money parsing and formatting |
| [ledger](ledger/) | Transactions, transfers and review queue |
| [planning](planning/) | Goals, income, bills and spending allowance |
| [coach](coach/) | Financial calculations, AI requests and usage limits |
| [ingestion](ingestion/) | Supported-bank SMS parsing and device capture |
| [reports](reports/) | Monthly reports, recurring payments and exports |
| [templates](templates/) | HTML grouped by app; shared layout and partials |
| [static](static/) | CSS, JavaScript and vendored browser libraries |
| [docs](docs/) | Detailed behavior and design decisions |

Each app keeps database definitions in `models.py`, request handlers in `views.py`,
routes in `urls.py`, and business logic in `services.py` where needed.
Tests live in each app's `tests/` folder; schema history stays in `migrations/`.

`.venv/`, `.env`, `db.sqlite3`, Python caches and `staticfiles/` are local or generated
files, excluded from Git. Keep migrations and vendored libraries: they are needed
for database upgrades and the interface.

## Checks

```powershell
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py test
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
```

Run a single app's tests with `manage.py test accounts.tests`.

## Further reading

- [Application guide](docs/application-guide.md): features, calculations and routes.
- [Design decisions](docs/decisions.md): financial rules and data integrity.
- [Production environment example](.env.example) and [Dockerfile](Dockerfile): deployment configuration.
