# QMS Reminder System: Backend (V1)

The Django + Django REST Framework API (SQLite3) behind the daily QMS reminder mails for a multi-plant pharma
company. The corporate QA head sets it up once. After that, every send day, each department of each plant gets
**one mail** with the QMS events and action plans it must attend to.

- **Plant workbooks:** one identical Excel workbook per plant in OneDrive/SharePoint. It has one sheet per QMS
  event type (Change Control, Deviation, Incident, CAPA, OOS, OOT, plus any you add) and one **Action Plans** sheet
  linked by QMS number.
- **Department mail, two sections:**
  - **Action required:** due within N days, sent daily.
  - **Periodic reminders:** sent every N days before that. The cycle can be counted back from the target date or
    from when the item first appeared, with an optional horizon for long-dated items.
- **Routing:** each action plan goes only to its own department, with the parent event shown for context. Overdue
  items are never mailed. A department with nothing due gets a "nothing pending" mail, but only when its plant's
  Excel was read correctly.
- **Safety:** if a plant's Excel can't be read, that plant's mails are **held** and the admin's **daily summary**
  says why. Mails are idempotent: never more than one per department per day, and retries only resend what failed.
- **Microsoft 365:** an IT-registered app (app-only), or the admin's *Sign in with Microsoft* (encrypted refresh
  token). Sends from a shared mailbox, and resolves pasted OneDrive/SharePoint links.
- **Simple setup:** plants, departments, To/Cc, event types and rules are all data, so adding a plant or department
  later needs no code. Plant QA heads can log in and see only their own plants.

## Documentation
- [docs/ADMIN_GUIDE.md](docs/ADMIN_GUIDE.md): how the admin sets up and uses the system.
- [docs/DEPLOYMENT_WINDOWS.md](docs/DEPLOYMENT_WINDOWS.md): **production install on the company Windows server**.
- [docs/DEPLOYMENT_TEST_CASES.md](docs/DEPLOYMENT_TEST_CASES.md): **deployment test protocol** (IQ/OQ/PQ style). Use it
  with `deploy\windows\smoke-test.ps1`, `manage.py deploy_check` and `manage.py make_test_workbook`.
- [docs/PROVIDERS.md](docs/PROVIDERS.md): Microsoft 365 app registration (both modes), Gmail and SMTP.
- [docs/OPERATIONS.md](docs/OPERATIONS.md): scheduler, SQLite, backup/restore and logging.
- [docs/SECURITY.md](docs/SECURITY.md): security model and the regulatory note.
- [docs/DEPLOYMENT_PYTHONANYWHERE.md](docs/DEPLOYMENT_PYTHONANYWHERE.md): the cloud test deployment.

## Local development
```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env              # set DJANGO_DEBUG=true for local development
python manage.py migrate && python manage.py createcachetable
ADMIN_USERNAME=admin ADMIN_PASSWORD='choose-a-strong-one' python manage.py bootstrap_admin
python manage.py seed_demo        # 2 demo plants, synthetic workbooks, log-only sender (nothing is delivered)
python manage.py runserver
```

| Purpose | Command |
|---|---|
| Tests / lint | `python manage.py test tests` / `ruff check .` |
| Scheduler tick (cron / Task Scheduler) | `python manage.py run_scheduler` |
| Send today's mails now | `python manage.py run_daily_reminders` |
| Read all Excel files only | `python manage.py sync_sources` |
| Backup / verify | `python manage.py backup_db --keep 30` / `python manage.py verify_backup <file>` |
| Verify an installation | `python manage.py deploy_check [--online]` (Windows: `deploy\windows\smoke-test.ps1`) |
| Functional test workbook | `python manage.py make_test_workbook --plant TEST --out TEST.xlsx` |

V2 features (dashboards, record explorer, advanced configuration) remain in the code and API, but V1's interface
shows only the reminder-mail screens.
