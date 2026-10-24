from django.core.management.base import BaseCommand

from apps.core.timeutils import business_today
from apps.reminders.models import DailyRun, JobSchedule
from apps.reminders.services import workflow


class Command(BaseCommand):
    help = ("Run the daily reminder job once (sync, evaluate, send). Safe to re-run: groups already sent today are skipped. "
            "Use this from a PythonAnywhere scheduled task or cron.")

    def add_arguments(self, parser):
        parser.add_argument("--no-sync", action="store_true", help="Use current records without syncing sources first.")
        parser.add_argument("--ignore-schedule", action="store_true", help="Run even if the schedule is disabled or today is not a run day.")

    def handle(self, *args, **opts):
        schedule = JobSchedule.objects.filter(job=JobSchedule.JOB_DAILY).first()
        today = business_today()
        if schedule and not opts["ignore_schedule"]:
            if not schedule.enabled:
                self.stdout.write("Daily reminder schedule is disabled; nothing to do.")
                return
            if schedule.days_of_week and today.weekday() not in schedule.days_of_week:
                self.stdout.write("Today is not a scheduled run day; nothing to do.")
                return
        if schedule:
            JobSchedule.objects.filter(pk=schedule.pk).update(last_run_date=today)
        run = workflow.run_daily(kind=DailyRun.KIND_SCHEDULED, triggered_by="command", sync=not opts["no_sync"])
        if schedule:
            JobSchedule.objects.filter(pk=schedule.pk).update(last_status=run.status, last_finished_at=run.finished_at)
        counts = ", ".join(f"{k}={run.summary[k]}" for k in ("sent", "failed", "uncertain", "held", "skipped") if k in run.summary)
        reason = run.summary.get("reason") or run.summary.get("error")
        self.stdout.write(f"Run #{run.pk} {run.status}: {counts}" + (f" reason={reason}" if reason else ""))
