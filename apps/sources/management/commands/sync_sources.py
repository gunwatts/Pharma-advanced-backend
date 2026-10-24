from django.core.management.base import BaseCommand

from apps.core import locks
from apps.sources.sync import sync_all


class Command(BaseCommand):
    help = "Sync all active source mappings now (no emails are sent)."

    def handle(self, *args, **opts):
        try:
            with locks.run_lock("daily_reminders", ttl_minutes=30):
                results = sync_all(triggered_by="command")
        except locks.LockNotAcquired:
            self.stderr.write("A reminder run is in progress; try again later.")
            return
        for run in results.values():
            self.stdout.write(f"{run.mapping}: {run.status} - {run.message}")
