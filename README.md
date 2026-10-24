# Pharma QA Reminder System — Backend (V1)

This is the Django + Django REST Framework API for an internal pharma QA reminder system. It syncs current QMS and audit
records from Excel workbooks you configure, works out which records are due, and sends **one consolidated reminder
email per unit/department per day**. It also serves role-based dashboards.

The frontend (React + TypeScript) lives in the separate `Pharma-Advanced` repository and is deployed to GitHub Pages.

> **Status:** This is a near-production V1. Before production use, you still need your organisation's provider credentials,
> access permissions, backups, monitoring and validation (including any GxP/CSV assessment). See
> [docs/SECURITY.md](docs/SECURITY.md).

## Features

- **Configurable data model**: units, departments, categories (Change Control, CAPA, Deviation, Incident, OOS, OOT, Audit,
  Audit Observation and Audit Action Item are seeded as examples), fields, statuses, parent/child relationships and
  recipients. Nothing is hard-coded.
- **Excel sources**: an uploaded workbook, a server/network folder (restricted to `SOURCE_ROOTS`), or OneDrive/SharePoint
  via Microsoft Graph. You map columns per worksheet, then validate and preview before saving.
- **Safe sync**: a failed or incomplete sync changes nothing. Closed or missing records are removed only after a
  successful, validated sync. Only mapped fields of open records are stored.
- **Reminder rules**: a rule builder with target date, window (default 7 days), status filters, unit/department scope and
  exclusion conditions. It accepts no SQL or Python.
- **Date eligibility**: `today <= target_date <= today + window`. Past dates are **never** emailed. Missing or invalid
  dates are flagged for review.
- **Daily workflow**: run lock → sync → trust check → group by unit/department → evaluate rules → one consolidated
  email per group. A "no pending" email is sent only when the group's data is complete. Groups affected by a failed
  source are held, and admins are alerted.
- **Delivery ledger**: idempotent per run date and unit/department, with states pending, in progress, sent, failed,
  uncertain, held and retry authorized. Uncertain sends are never resent until an admin reconciles them. Retries cover
  only the failed groups.
- **Email providers**: Microsoft 365 (Graph `sendMail`), Gmail API, SMTP, and a log-only console provider for testing.
  Credentials are read only from environment variables.
- **Dashboards**: built in the UI with KPI, pie, donut, bar, line, area, table, upcoming and overdue widgets.
  Dashboards can be published, cloned and assigned to roles or users, and support click-through to records.
- **Security**: expiring hashed bearer tokens, login throttling and lockout, role permissions, and server-side
  unit/department scoping on every endpoint (list, detail, search, children, export, widgets, drill-down). Every
  configuration change is recorded in an audit trail.

## Quick start (local development)

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env            # then set DJANGO_DEBUG=true for local dev
python manage.py migrate
python manage.py createcachetable
ADMIN_USERNAME=admin ADMIN_PASSWORD='choose-a-strong-one' python manage.py bootstrap_admin
python manage.py seed_demo      # optional: synthetic demo data + log-only sender (nothing is delivered)
python manage.py runserver
```

API base: `http://localhost:8000/api/` (health: `/api/health/`).

## Commands

| Purpose | Command |
|---|---|
| Run tests | `python manage.py test tests` |
| Lint | `ruff check .` |
| Daily run (cron / PythonAnywhere task) | `python manage.py run_daily_reminders` |
| Scheduler tick / loop | `python manage.py run_scheduler` / `python manage.py run_scheduler --loop` |
| Sync sources only | `python manage.py sync_sources` |
| Backup database (+verify, prune) | `python manage.py backup_db --keep 14` |
| Verify a backup file | `python manage.py verify_backup <file>` |
| Create/update admin from env | `python manage.py bootstrap_admin` |
| Demo data (non-production) | `python manage.py seed_demo` |

## Documentation

- [docs/DEPLOYMENT_PYTHONANYWHERE.md](docs/DEPLOYMENT_PYTHONANYWHERE.md): test deployment on PythonAnywhere and GitHub Pages.
- [docs/PROVIDERS.md](docs/PROVIDERS.md): Microsoft 365, Gmail, SMTP and Graph data source setup.
- [docs/OPERATIONS.md](docs/OPERATIONS.md): scheduler, SQLite, backup/restore, logging, upgrades and internal server deployment.
- [docs/SECURITY.md](docs/SECURITY.md): security model, hardening checklist and regulatory notes.
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): modules, data model and key decisions.
