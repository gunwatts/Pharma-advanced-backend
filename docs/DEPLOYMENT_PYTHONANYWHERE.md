# Test deployment: PythonAnywhere (API) + GitHub Pages (UI)

This setup is for **testing and evaluation**. For production, deploy on your internal company server
(see [OPERATIONS.md](OPERATIONS.md)). PythonAnywhere plans and limits change, so check their help pages for the
current rules on scheduled tasks, outbound internet access and SMTP.

```
Browser ──HTTPS──> https://<github-user>.github.io/Pharma-Advanced/   (static React app)
   │
   └──HTTPS + Bearer token──> https://<pa-user>.pythonanywhere.com/api/  (Django API + SQLite)
                                       ▲
                     PythonAnywhere scheduled task: manage.py run_daily_reminders
```

Login uses expiring bearer tokens, not cookies, so the cross-site setup works without third-party cookies.

## 1. Backend on PythonAnywhere

1. Open a **Bash console** and run:

   ```bash
   git clone -b <branch> https://github.com/gunwatts/Pharma-advanced-backend.git
   cd Pharma-advanced-backend
   mkvirtualenv --python=python3.12 qa-env      # any Python 3.10+ that PythonAnywhere offers
   pip install -r requirements.txt
   mkdir -p ~/qa-data/uploads ~/qa-data/backups ~/qa-sources
   cp .env.example .env && nano .env
   ```

   In `.env`, set at least:

   ```
   DJANGO_SECRET_KEY=<python -c "import secrets; print(secrets.token_urlsafe(50))">
   DJANGO_DEBUG=false
   DJANGO_ALLOWED_HOSTS=<pa-user>.pythonanywhere.com
   CORS_ALLOWED_ORIGINS=https://<github-user>.github.io
   CSRF_TRUSTED_ORIGINS=https://<pa-user>.pythonanywhere.com
   FRONTEND_URL=https://<github-user>.github.io/Pharma-Advanced/
   SQLITE_PATH=/home/<pa-user>/qa-data/db.sqlite3
   SQLITE_WAL=false          # PythonAnywhere storage is network-backed: keep WAL off
   PRIVATE_UPLOAD_ROOT=/home/<pa-user>/qa-data/uploads
   BACKUP_ROOT=/home/<pa-user>/qa-data/backups
   SOURCE_ROOTS=qms=/home/<pa-user>/qa-sources
   ```

2. Initialise the database and admin account:

   ```bash
   python manage.py migrate
   python manage.py createcachetable
   python manage.py collectstatic --noinput
   ADMIN_USERNAME=qaadmin ADMIN_EMAIL=you@example.com ADMIN_PASSWORD='<strong password>' python manage.py bootstrap_admin
   python manage.py seed_demo     # optional: synthetic data + log-only sender, so nothing is delivered
   ```

3. Configure the **Web** tab:
   - Add a new web app with **Manual configuration** and the same Python version.
   - Set **Virtualenv** to `/home/<pa-user>/.virtualenvs/qa-env`.
   - Edit the **WSGI configuration file** and replace its contents with
     [`deploy/pythonanywhere_wsgi.py`](../deploy/pythonanywhere_wsgi.py), with your username filled in.
   - Optional static mapping: URL `/static/` → `/home/<pa-user>/Pharma-advanced-backend/staticfiles`.
   - Turn on **Force HTTPS**, then click **Reload**.
   - Check `https://<pa-user>.pythonanywhere.com/api/health/`. It should return `{"status": "ok", ...}`.

4. **Daily trigger.** PythonAnywhere **free accounts no longer have scheduled tasks**, so use one of these:
   - **Free (recommended): GitHub Actions trigger.** Generate a token
     (`python -c "import secrets; print(secrets.token_urlsafe(32))"`) and set it as `JOB_TRIGGER_TOKEN` in the server
     `.env`, then reload the web app. In the backend repo, add the Actions secret `QA_JOB_TOKEN` with the same value,
     and optionally the variable `QA_API_URL=https://<pa-user>.pythonanywhere.com/api`.
     [`.github/workflows/scheduler.yml`](../.github/workflows/scheduler.yml) calls `POST /api/jobs/tick/` every 30 minutes.
     The server starts each job only when it is due (the time and run days in **Advanced Setup → Schedules**, business
     timezone), at most once per day, and only after activation. GitHub runs scheduled workflows **only from the default
     branch**, sometimes a few minutes late.
   - **Paid account:** add a daily *Scheduled task* instead (times are UTC; 08:00 Asia/Kolkata is 02:30 UTC):
     ```
     cd /home/<pa-user>/Pharma-advanced-backend && /home/<pa-user>/.virtualenvs/qa-env/bin/python manage.py backup_db --keep 14 && /home/<pa-user>/.virtualenvs/qa-env/bin/python manage.py run_daily_reminders
     ```
   Either way, re-running on the same day is safe: groups already sent are skipped.

5. To update after new commits:
   `git pull && pip install -r requirements.txt && python manage.py migrate && python manage.py collectstatic --noinput`,
   then reload the web app.

### PythonAnywhere notes

- **Outbound internet**: free accounts can reach only allow-listed hosts through a proxy, which `requests` uses
  automatically. Check that `login.microsoftonline.com`, `graph.microsoft.com`, `oauth2.googleapis.com` and
  `gmail.googleapis.com` are reachable from your plan.
- **SMTP**: free accounts are restricted (historically to Gmail's SMTP servers only). Prefer the Graph or Gmail API providers.
- **Network folders**: PythonAnywhere cannot reach your company file shares. For testing, use an **uploaded workbook**
  source or files uploaded to `~/qa-sources` (Files tab).
- **SQLite** works well at this scale. Keep `SQLITE_WAL=false` because PythonAnywhere's storage is not a local disk.

## 2. Frontend on GitHub Pages

The `Pharma-Advanced` repository has a workflow (`.github/workflows/deploy-pages.yml`) that builds the app and publishes it.

1. In the repository go to **Settings → Pages → Build and deployment → Source** and choose **GitHub Actions**.
2. Under **Settings → Secrets and variables → Actions → Variables**, add
   `VITE_API_BASE_URL = https://<pa-user>.pythonanywhere.com/api`.
3. Push to the deployment branch or run the workflow manually. The site appears at
   `https://<github-user>.github.io/Pharma-Advanced/`.
4. Make sure the backend's `CORS_ALLOWED_ORIGINS` contains exactly `https://<github-user>.github.io`, with no path or
   trailing slash.

## 3. First login and Quick Start

1. Sign in with the admin account you created and open **Quick Start**.
2. Go through the eight steps: sender → data source → units, departments and recipients → Excel mappings →
   reminder rules → email template → dry run and test email → activate.
3. Until real provider credentials are configured, use the **Log only** sender. It records emails in the server log
   and the UI clearly says nothing was delivered.
