# Operations

## Scheduler

The app has no Celery or Redis. Jobs are management commands guarded by a **database run lock** (`core_joblock`, with
stale-lock takeover after the TTL), and the delivery ledger makes reruns safe.

| Environment | How to schedule |
|---|---|
| PythonAnywhere (free) | GitHub Actions cron calls `POST /api/jobs/tick/` with `X-Job-Token` (see `.github/workflows/scheduler.yml`) |
| PythonAnywhere (paid) | A daily *Scheduled task* runs `manage.py run_daily_reminders` (times are UTC) |
| Linux server, cron | `*/10 * * * * cd /opt/qa/backend && ../venv/bin/python manage.py run_scheduler` |
| Linux server, systemd | [`deploy/qa-scheduler.service`](../deploy/qa-scheduler.service) runs `run_scheduler --loop` |
| Windows server | Task Scheduler every 10 minutes runs `python manage.py run_scheduler` |

`run_scheduler` runs each enabled job from **Advanced Setup → Schedules** at most once per business day, at or after
the configured local time (business timezone). `run_daily_reminders` runs the daily job straight away (subject to
run days and activation), so it suits schedulers that already control the time.

**Limitations:** jobs run only when something triggers them. If the server is down at the scheduled time, the next
tick that day catches up, but a day missed entirely is not backfilled. A run that crashes mid-send leaves a ledger row
`in_progress`. After 15 minutes it becomes `uncertain` and needs reconciliation. It is never blindly resent.

## Daily run outcomes

- **sent**: the reminder or "no pending" email was accepted by the provider.
- **held**: a relevant source failed or was incomplete, or records have data issues, so no misleading email was sent.
  Fix the cause, then use **Runs → Run again** (or the per-delivery **Retry**). Already-sent groups are skipped.
- **failed**: the provider rejected the email. It is retried on the next run up to *max send attempts*, then needs a
  manual retry.
- **uncertain**: check the mailbox, then **Mark as sent** or **Authorize retry** (both are audited).

Admin alert emails go to *System settings → alert recipients* whenever a run needs attention.

## SQLite

- SQLite3 is the required database for V1. Foreign keys are enforced, `busy_timeout` is 20 s, and transactions are
  short and use `BEGIN IMMEDIATE` to avoid lock-upgrade deadlocks.
- **WAL mode** (`SQLITE_WAL=true`) gives better read concurrency while a sync writes. Enable it **only on local disks**.
  WAL relies on shared memory and is unsafe on NFS, SMB or other network filesystems (including PythonAnywhere home
  directories). In WAL mode, back up with `backup_db` rather than copying the `.sqlite3` file alone, because `-wal`
  and `-shm` files exist alongside it.
- Keep the database on reliable local server storage, never on a file share.
- Stored data is minimal: configuration, users, dashboards, **current open records only**, the delivery ledger,
  sync/run logs and the audit trail. Workbooks are parsed in memory; the uploaded-workbook source keeps only the latest
  uploaded file.
- **When SQLite may stop fitting**: many concurrent writers, several application servers, or high-frequency syncs of
  very large workbooks (hundreds of thousands of rows). The data access uses the Django ORM, so a later move to
  PostgreSQL would be a configuration change plus data migration. That is out of scope for V1.

## Backup and restore

```bash
python manage.py backup_db --keep 14          # online, consistent copy (sqlite3 backup API) + integrity_check + gzip
python manage.py verify_backup /path/to/qa-reminders-YYYYMMDD-HHMMSS.sqlite3.gz
```

Restore steps:

1. Stop the web app and the scheduler (PythonAnywhere: disable the task and reload, or stop the web app).
2. Keep a copy of the current database: `cp db.sqlite3 db.sqlite3.before-restore`.
3. Run `gunzip -c <backup>.gz > $SQLITE_PATH`, and remove any stale `-wal`/`-shm` files.
4. Run `python manage.py migrate` (needed if restoring an older backup onto newer code), then
   `python manage.py check`.
5. Start the services and confirm `/api/health/`, the login, and the last delivery entries.

Test restores regularly on a non-production copy. Store backups off the server as well (for example, copy
`BACKUP_ROOT` to approved storage).

## Logging and monitoring

- Logs are JSON lines on stdout (PythonAnywhere: the server and error logs). Set `LOG_TO_FILE=true` for rotating
  files in `LOG_DIR`.
- Secrets are redacted by a logging filter. Email bodies are never logged.
- Monitor `GET /api/health/` (no authentication, no sensitive data) and alert on `DailyRun` status
  `failed` / `completed_with_issues` (also e-mailed to alert recipients).

## Internal company server (production)

1. Use Linux with Python 3.10+, a dedicated service account, and the code in `/opt/qa-reminders/backend`.
2. Create a virtualenv and run `pip install -r requirements.txt`. Write `.env` (mode 600, owned by the service account).
3. Run `migrate`, `createcachetable`, `collectstatic` and `bootstrap_admin`.
4. Serve with gunicorn (`pip install gunicorn`; `gunicorn config.wsgi -w 3 -b 127.0.0.1:8001`) behind nginx or IIS
   with **HTTPS**. Set `X-Forwarded-Proto` so that `SECURE_PROXY_SSL_HEADER` works.
5. Build the frontend with `VITE_API_BASE_URL=https://qa.company.local/api` and serve `dist/` from the same host.
   With a single origin, CORS is not needed.
6. Schedule `run_scheduler` (systemd, cron or Task Scheduler) and a daily `backup_db`.
7. Mount network shares read-only for the service account and list them in `SOURCE_ROOTS`.

## Upgrades

1. Run `backup_db`.
2. Run `git pull`, then `pip install -r requirements.txt`.
3. Run `python manage.py migrate`, then `collectstatic --noinput`.
4. Restart or reload the web app and the scheduler, and check `/api/health/`.
5. Run a **dry run** from the UI before the next scheduled run.
