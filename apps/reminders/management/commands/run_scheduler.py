import time

from django.core.management.base import BaseCommand

from apps.reminders.services import scheduler


class Command(BaseCommand):
    help = "Run due scheduled jobs once (for cron), or continuously with --loop (for a systemd service / always-on task)."

    def add_arguments(self, parser):
        parser.add_argument("--loop", action="store_true")
        parser.add_argument("--interval", type=int, default=60, help="Seconds between checks in --loop mode.")

    def handle(self, *args, **opts):
        while True:
            results = scheduler.tick()
            if results:
                self.stdout.write(f"Ran jobs: {results}")
            if not opts["loop"]:
                break
            time.sleep(max(15, opts["interval"]))
