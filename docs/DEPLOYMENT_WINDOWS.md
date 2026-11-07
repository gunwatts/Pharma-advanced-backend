# Deployment on a company Windows server

One Windows server runs everything. The web app (setup screens and API) is served by
**Waitress**, the daily mails are triggered by **Windows Task Scheduler**, and data lives in a
**SQLite** database on the server's local disk. No IIS module, Celery, Redis or database server is needed.

```
Users' browsers ──HTTPS──> IIS / reverse proxy (company certificate) ──> Waitress :8000 (Django: app + /api)
                                                                       │
Task Scheduler: every 15 min -> manage.py run_scheduler (sends the daily mails once per day, at 08:00 by default)
Task Scheduler: daily 07:30  -> manage.py backup_db
Microsoft 365 (Graph): reads each plant's Excel in OneDrive/SharePoint, sends from the shared mailbox
```

## 1. What IT needs to prepare

| Item | Details |
|---|---|
| Windows Server 2016 or later | 2 CPU, 4 GB RAM and 10 GB of free disk are plenty |
| Python 3.12 (64-bit) | Install from python.org, tick **"Add python.exe to PATH"** and the **py launcher** |
| Service account | A domain account the scheduled tasks run as, for example `COMPANY\svc-qms-reminders` (no admin rights needed) |
| Outbound HTTPS | The server must reach `login.microsoftonline.com` and `graph.microsoft.com` (and pypi.org during install) |
| Microsoft Entra app registration | See [PROVIDERS.md](PROVIDERS.md) → "Microsoft 365". IT gives you **tenant ID, client ID, client secret** |
| Shared mailbox | For example `qms.reminders@company.com` (Exchange Online) |
| Internal DNS name and certificate | For example `https://qms-reminders.company.local`, via IIS (recommended) or another reverse proxy |

## 2. Install

1. Copy the backend folder to `D:\QMSReminders\backend`. Use a local disk, not a network share.
2. Copy the built frontend into `D:\QMSReminders\backend\frontend_dist`. Download the **"qms-frontend-server"** artifact
   from the frontend repository's GitHub Actions run (built for same-server use). Alternatively, on any PC with Node.js 22:
   ```powershell
   cd Pharma-Advanced
   npm ci
   $env:VITE_BASE="/"; $env:VITE_API_BASE_URL="/api"; npm run build   # output: dist\
   ```
   Then copy the **contents** of `dist\` into `backend\frontend_dist\`.
3. Open PowerShell in `D:\QMSReminders\backend` and run:
   ```powershell
   powershell -ExecutionPolicy Bypass -File deploy\windows\install.ps1
   ```
   This creates the Python environment, installs the packages and creates `.env` with a new secret key. It also
   creates the database, the static files and the `data\` and `logs\` folders.
4. Edit `D:\QMSReminders\backend\.env`. Let only Administrators and the service account read this file:
   ```
   DJANGO_DEBUG=false
   DJANGO_ALLOWED_HOSTS=qms-reminders.company.local,localhost
   CSRF_TRUSTED_ORIGINS=https://qms-reminders.company.local
   FRONTEND_URL=https://qms-reminders.company.local/
   BUSINESS_TIMEZONE=Asia/Kolkata
   SQLITE_WAL=true                      # safe on the server's local NTFS disk
   GRAPH_TENANT_ID=<from IT>
   GRAPH_CLIENT_ID=<from IT>
   GRAPH_CLIENT_SECRET=<from IT>
   M365_REDIRECT_URI=https://qms-reminders.company.local/api/m365/callback/   # only for "Admin signs in"
   SECURE_SSL_REDIRECT=true             # set false ONLY if the site is reached over plain http
   ```
   `CORS_ALLOWED_ORIGINS` is not needed, because the app and the API share one address.
5. Create the first administrator:
   ```powershell
   $env:ADMIN_USERNAME="cqa.admin"; $env:ADMIN_EMAIL="cqa.head@company.com"; $env:ADMIN_PASSWORD="<strong password>"
   .venv\Scripts\python.exe manage.py bootstrap_admin
   Remove-Item Env:ADMIN_PASSWORD
   ```
6. Register the scheduled tasks. Run PowerShell **as Administrator**:
   ```powershell
   powershell -ExecutionPolicy Bypass -File deploy\windows\register-tasks.ps1 -ServiceUser "COMPANY\svc-qms-reminders" -ServicePassword "<password>"
   ```
   This registers three tasks:
   - **QMS Reminders - Web** starts the web server at boot and restarts it if it stops.
   - **QMS Reminders - Scheduler** runs every 15 minutes. It sends the day's mails once, at or after the time set
     in Setup.
   - **QMS Reminders - Backup** runs daily at 07:30.
   Give the service account Modify rights on `D:\QMSReminders\backend\data` and `logs`.
7. HTTPS: in IIS, add a site for `qms-reminders.company.local` with the company certificate. Install
   **URL Rewrite** and **Application Request Routing**, then add a reverse-proxy rule to `http://localhost:8000`.
   Also set the server variable `HTTP_X_FORWARDED_PROTO` = `https` on the rule. Block port 8000 from other
   machines in Windows Firewall.
8. Check `https://qms-reminders.company.local/api/health/`. It should return `{"status": "ok", ...}`. Then open
   `https://qms-reminders.company.local/` and sign in.
9. Run the **smoke test**. It must end with `SMOKE TEST PASSED`:
   ```powershell
   powershell -ExecutionPolicy Bypass -File deploy\windows\smoke-test.ps1 -BaseUrl https://qms-reminders.company.local
   ```
   After Microsoft 365 is set up (step 3 below), run it again with `-Online`. Then work through the protocol in
   [DEPLOYMENT_TEST_CASES.md](DEPLOYMENT_TEST_CASES.md) and keep the completed copy as deployment evidence.

## 3. First-time setup in the app (admin)

Follow **Setup** (5 steps), described in [ADMIN_GUIDE.md](ADMIN_GUIDE.md):
1. Connect Microsoft 365 and the shared mailbox, then send a test email.
2. Check the Excel layout and reminder rules. Download the template and share it with the plant QA heads.
3. Add the plants. Paste each plant's OneDrive/SharePoint link, click **Check**, and add departments with To/Cc.
4. Set the mail wording, the time (08:00), the days (Mon–Sat) and the daily summary recipient.
5. Open **Today**, click **Read Excel files now**, review the result, then **Activate**.

## 4. Operations

| Task | How |
|---|---|
| See what goes out today | **Today's reminders** page |
| See what was sent | **History** page (each plant QA head sees only their own plants) |
| Send now or retry after fixing an Excel | Today → **Send today's mails now**. Departments already mailed today are skipped |
| Logs | `logs\web.log` and `logs\scheduler.log` |
| Backups | `data\backups\qa-reminders-*.sqlite3.gz` (30 kept). Copy them to a backed-up share |
| Verify a backup | `.venv\Scripts\python.exe manage.py verify_backup data\backups\<file>` |
| Restore | Stop the *Web* and *Scheduler* tasks, keep a copy of `data\db.sqlite3`, extract the backup over it (7-Zip), delete `db.sqlite3-wal` and `db.sqlite3-shm`, run `manage.py migrate`, then start the tasks |
| Upgrade | Copy the new backend (keep `.env`, `data\`, `logs\`), copy the new `frontend_dist\`, run `install.ps1`, restart the *Web* task |
| Client secret expiry | Entra client secrets expire (often after 6–24 months). Put a reminder in the calendar, then update `GRAPH_CLIENT_SECRET` in `.env` and restart the *Web* task |

The scheduler runs as a separate process from the web server, so mails are sent even if no one has the app open.
If the server is off at 08:00, the next 15-minute check that day sends the mails. A missed day is not sent
afterwards.
