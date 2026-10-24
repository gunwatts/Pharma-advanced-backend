import gzip
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


def verify(path: Path):
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        result = con.execute("PRAGMA integrity_check").fetchone()[0]
        tables = con.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
    finally:
        con.close()
    return result, tables


class Command(BaseCommand):
    help = "Create a consistent online backup of the SQLite database (sqlite3 backup API), verify it, and prune old backups."

    def add_arguments(self, parser):
        parser.add_argument("--keep", type=int, default=14, help="Number of backups to keep.")
        parser.add_argument("--no-gzip", action="store_true")

    def handle(self, *args, **opts):
        root = Path(settings.BACKUP_ROOT)
        root.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        target = root / f"qa-reminders-{stamp}.sqlite3"
        src = sqlite3.connect(settings.DATABASES["default"]["NAME"])
        dst = sqlite3.connect(target)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        result, tables = verify(target)
        if result != "ok":
            target.unlink(missing_ok=True)
            raise CommandError(f"Backup failed integrity check: {result}")
        if not opts["no_gzip"]:
            with open(target, "rb") as fin, gzip.open(f"{target}.gz", "wb") as fout:
                shutil.copyfileobj(fin, fout)
            target.unlink()
            target = Path(f"{target}.gz")
        backups = sorted(root.glob("qa-reminders-*.sqlite3*"))
        for old in backups[: max(0, len(backups) - opts["keep"])]:
            old.unlink()
        self.stdout.write(f"Backup OK ({tables} tables, integrity ok): {target}")
