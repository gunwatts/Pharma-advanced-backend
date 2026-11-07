"""Post-deployment verification: `python manage.py deploy_check [--online]`.

Prints one line per check (PASS / WARN / FAIL) and exits with status 1 if any check FAILs, so it
can be used in the Windows smoke test and in change-control evidence. Read-only: it never sends
mail or changes data (``--online`` only requests a Microsoft token and reads the plant workbooks).
"""

import os
import tempfile
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

PASS, WARN, FAIL = "PASS", "WARN", "FAIL"
DEV_KEY = "insecure-dev-only-key-do-not-use-in-production"


class Command(BaseCommand):
    help = "Verify a deployed installation (configuration, database, files, Microsoft 365, scheduling)."

    def add_arguments(self, parser):
        parser.add_argument("--online", action="store_true", help="Also test Microsoft 365 sign-in and read every plant workbook.")

    def handle(self, *args, **opts):
        self.results = []
        for check in (self.check_settings, self.check_database, self.check_files, self.check_timezone,
                      self.check_frontend, self.check_users, self.check_configuration, self.check_m365):
            try:
                check()
            except Exception as exc:  # a crashing check is itself a failure
                self.add(FAIL, check.__name__.replace("check_", ""), f"check crashed: {exc.__class__.__name__}: {exc}")
        if opts["online"]:
            self.check_online()

        width = max(len(name) for _, name, _ in self.results)
        for status, name, detail in self.results:
            style = {PASS: self.style.SUCCESS, WARN: self.style.WARNING, FAIL: self.style.ERROR}[status]
            self.stdout.write(f"{style(status)}  {name.ljust(width)}  {detail}")
        fails = sum(1 for s, _, _ in self.results if s == FAIL)
        warns = sum(1 for s, _, _ in self.results if s == WARN)
        self.stdout.write(f"\n{len(self.results)} checks: {len(self.results) - fails - warns} passed, {warns} warnings, {fails} failed.")
        if fails:
            raise SystemExit(1)

    def add(self, status, name, detail=""):
        self.results.append((status, name, detail))

    # --- individual checks -------------------------------------------------------------------

    def check_settings(self):
        self.add(FAIL if settings.DEBUG else PASS, "DEBUG off", "DJANGO_DEBUG must be false in production" if settings.DEBUG else "")
        weak = settings.SECRET_KEY == DEV_KEY or len(settings.SECRET_KEY) < 40
        self.add(FAIL if weak else PASS, "Secret key", "set a long random DJANGO_SECRET_KEY" if weak else "")
        hosts = [h for h in settings.ALLOWED_HOSTS if h not in ("*",)]
        self.add(PASS if hosts and "*" not in settings.ALLOWED_HOSTS else FAIL, "Allowed hosts", ", ".join(settings.ALLOWED_HOSTS) or "empty")
        redirect = getattr(settings, "SECURE_SSL_REDIRECT", False)
        self.add(PASS if redirect else WARN, "HTTPS redirect", "" if redirect else "SECURE_SSL_REDIRECT is off - only acceptable on a trusted internal network")
        admin_on = getattr(settings, "ENABLE_DJANGO_ADMIN", False)
        self.add(WARN if admin_on else PASS, "Django admin disabled", "ENABLE_DJANGO_ADMIN is on" if admin_on else "")

    def check_database(self):
        with connection.cursor() as cur:
            cur.execute("SELECT 1")
            cur.execute("PRAGMA journal_mode")
            journal = cur.fetchone()[0]
            cur.execute("PRAGMA foreign_keys")
            fk = cur.fetchone()[0]
        self.add(PASS, "Database reachable", settings.DATABASES["default"]["NAME"])
        path = str(settings.DATABASES["default"]["NAME"])
        on_share = path.startswith("\\\\") or path.startswith("//")
        self.add(FAIL if on_share else PASS, "Database on local disk", "SQLite must not be on a network share" if on_share else "")
        self.add(PASS if fk else FAIL, "Foreign keys enforced", "")
        self.add(PASS, "Journal mode", journal)
        executor = MigrationExecutor(connection)
        pending = executor.migration_plan(executor.loader.graph.leaf_nodes())
        self.add(FAIL if pending else PASS, "Migrations applied", f"{len(pending)} pending - run manage.py migrate" if pending else "")
        tables = connection.introspection.table_names()
        cache_ok = "django_cache" in tables or "DatabaseCache" not in settings.CACHES["default"]["BACKEND"]
        self.add(PASS if cache_ok else FAIL, "Cache table", "" if cache_ok else "run manage.py createcachetable")

    def check_files(self):
        for label, folder in (("Upload folder writable", settings.PRIVATE_UPLOAD_ROOT), ("Backup folder writable", settings.BACKUP_ROOT)):
            folder = Path(folder)
            try:
                folder.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(dir=folder, delete=True):
                    pass
                self.add(PASS, label, str(folder))
            except OSError as exc:
                self.add(FAIL, label, f"{folder}: {exc.strerror}")
        env_file = Path(settings.BASE_DIR) / ".env"
        self.add(PASS if env_file.exists() else WARN, ".env file present", str(env_file))
        static_ok = (Path(settings.STATIC_ROOT) / "rest_framework").exists() or (Path(settings.STATIC_ROOT) / "admin").exists()
        self.add(PASS if static_ok else WARN, "Static files collected", "" if static_ok else "run manage.py collectstatic")

    def check_timezone(self):
        from apps.core.models import SystemSettings
        name = SystemSettings.load().business_timezone or settings.BUSINESS_TIMEZONE
        try:
            ZoneInfo(name)
            self.add(PASS, "Business timezone", name)
        except ZoneInfoNotFoundError:
            self.add(FAIL, "Business timezone", f"'{name}' not found - install the tzdata package (required on Windows)")

    def check_frontend(self):
        index = Path(settings.FRONTEND_DIST) / "index.html"
        if not index.exists():
            self.add(WARN, "Frontend bundle", f"{index} missing - copy the built frontend there (skip if hosted elsewhere)")
            return
        html = index.read_text(encoding="utf-8", errors="ignore")
        same_origin = "connect-src 'self'" in html and "/assets/" in html
        self.add(PASS if same_origin else WARN, "Frontend bundle", "built for same-server use" if same_origin
                 else "index.html was not built with VITE_BASE=/ and VITE_API_BASE_URL=/api")

    def check_users(self):
        admins = [u for u in get_user_model().objects.filter(is_active=True).select_related("profile__role")
                  if u.is_superuser or getattr(getattr(getattr(u, "profile", None), "role", None), "key", "") == "admin"]
        self.add(PASS if admins else FAIL, "Administrator account", f"{len(admins)} active" if admins else "run manage.py bootstrap_admin")
        if os.environ.get("ADMIN_PASSWORD"):
            self.add(WARN, "ADMIN_PASSWORD cleared", "remove ADMIN_PASSWORD from the environment/.env after bootstrap")

    def check_configuration(self):
        from apps.catalog.models import RecipientGroup, Unit
        from apps.core.models import SystemSettings
        from apps.reminders.models import EmailTemplate, JobSchedule, SenderProfile
        plants = Unit.objects.filter(active=True).count()
        groups = RecipientGroup.objects.filter(active=True).count()
        self.add(PASS if plants else WARN, "Plants configured", f"{plants} active plant(s), {groups} department mail(s)")
        self.add(PASS if EmailTemplate.objects.filter(is_active=True).exists() else FAIL, "Active mail template", "")
        sender = SenderProfile.objects.filter(is_default=True, active=True).first()
        if sender is None:
            self.add(WARN, "Shared mailbox", "not configured yet (Setup -> Microsoft 365)")
        elif sender.provider == SenderProfile.PROVIDER_CONSOLE:
            self.add(WARN, "Shared mailbox", "log-only sender - no email will be delivered")
        else:
            self.add(PASS, "Shared mailbox", f"{sender.from_address} via {sender.get_provider_display()}")
        schedule = JobSchedule.objects.filter(job=JobSchedule.JOB_DAILY).first()
        detail = f"{schedule.run_time:%H:%M}, days {schedule.days_of_week or 'every day'}" if schedule else "missing"
        self.add(PASS if schedule and schedule.enabled else WARN, "Daily schedule", detail)
        active = SystemSettings.load().reminders_active
        self.add(PASS if active else WARN, "Reminders activated", "" if active else "activate in Setup -> Check & activate")
        recipients = SystemSettings.load().alert_recipients
        self.add(PASS if recipients else WARN, "Daily summary recipient", ", ".join(recipients) or "none - set in Setup -> Mail & schedule")

    def check_m365(self):
        from apps.core import m365
        missing = m365.missing_env()
        self.add(PASS if not missing else WARN, "Microsoft 365 app details", "" if not missing else "missing " + ", ".join(missing))
        status = m365.status()
        if status["mode"] == "delegated":
            self.add(PASS if status["signed_in"] else WARN, "Microsoft sign-in", status["account"] or "nobody signed in")
            if not os.environ.get("M365_REDIRECT_URI"):
                self.add(WARN, "M365 redirect URI", "set M365_REDIRECT_URI to https://<server>/api/m365/callback/")

    def check_online(self):
        from apps.core import m365
        from apps.sources import connectors
        from apps.sources.excel import WorkbookError, list_sheets
        from apps.sources.models import DataSource
        try:
            m365.clear_cache()
            m365.access_token()
            self.add(PASS, "Microsoft 365 token", f"mode: {m365.status()['mode']}")
        except m365.M365Error as exc:
            self.add(FAIL, "Microsoft 365 token", str(exc))
        for source in DataSource.objects.filter(active=True, plant__isnull=False, plant__active=True).select_related("plant"):
            try:
                sheets = list_sheets(connectors.fetch_workbook(source))
                self.add(PASS, f"Workbook {source.plant.code}", f"{len(sheets)} sheets")
            except (connectors.SourceError, WorkbookError) as exc:
                self.add(FAIL, f"Workbook {source.plant.code}", str(exc))
