import gzip
import shutil
import sqlite3
import tempfile
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.core.management.commands.backup_db import verify


class Command(BaseCommand):
    help = "Verify a backup file (integrity check and key table counts) without touching the live database."

    def add_arguments(self, parser):
        parser.add_argument("path")

    def handle(self, *args, **opts):
        path = Path(opts["path"])
        if not path.exists():
            raise CommandError("Backup file not found.")
        with tempfile.TemporaryDirectory() as tmp:
            check = path
            if path.suffix == ".gz":
                check = Path(tmp) / "restore-check.sqlite3"
                with gzip.open(path, "rb") as fin, open(check, "wb") as fout:
                    shutil.copyfileobj(fin, fout)
            result, tables = verify(check)
            con = sqlite3.connect(check)
            counts = {t: con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                      for t in ("auth_user", "catalog_category", "reminders_delivery", "core_auditevent", "records_record")}
            con.close()
        if result != "ok":
            raise CommandError(f"Integrity check failed: {result}")
        self.stdout.write(f"Backup OK: {tables} tables. Row counts: {counts}")
