"""Simple, dependency-free scheduler.

``tick()`` is safe to call as often as you like (cron every 5-15 minutes, a
PythonAnywhere scheduled task, a systemd timer, or ``run_scheduler --loop``):
each job runs at most once per business day, guarded by a DB run lock.
"""

import logging
from datetime import datetime, timedelta

from django.utils import timezone

from apps.core import locks
from apps.core.timeutils import business_now, business_tz
from apps.reminders.models import DailyRun, JobSchedule
from apps.reminders.services import workflow
from apps.sources.sync import sync_all

logger = logging.getLogger("qa.scheduler")


def _runs_on(schedule, day):
    return not schedule.days_of_week or day.weekday() in schedule.days_of_week


def next_run_at(schedule, now=None):
    """Next time the job will run (``now`` if it is already due)."""
    if not schedule.enabled:
        return None
    now = now or business_now()
    for offset in range(8):
        day = (now + timedelta(days=offset)).date()
        if not _runs_on(schedule, day) or schedule.last_run_date == day:
            continue
        candidate = datetime.combine(day, schedule.run_time, tzinfo=business_tz())
        return max(candidate, now) if offset == 0 else candidate
    return None


def is_due(schedule, now=None):
    now = now or business_now()
    today = now.date()
    return (schedule.enabled and _runs_on(schedule, today) and schedule.last_run_date != today
            and now.time() >= schedule.run_time)


def run_job(schedule, triggered_by="scheduler"):
    today = business_now().date()
    # Claim today's slot first so a crash cannot cause a tight re-run loop.
    claimed = JobSchedule.objects.filter(pk=schedule.pk).exclude(last_run_date=today).update(
        last_run_date=today, last_started_at=timezone.now(), last_status="running")
    if not claimed:
        return None
    status = "failed"
    try:
        if schedule.job == JobSchedule.JOB_DAILY:
            run = workflow.run_daily(kind=DailyRun.KIND_SCHEDULED, triggered_by=triggered_by)
            status = run.status
        elif schedule.job == JobSchedule.JOB_SYNC:
            with locks.run_lock("sync_sources", ttl_minutes=30):
                daily = DailyRun.objects.create(run_date=today, kind=DailyRun.KIND_SYNC, triggered_by=triggered_by)
                results = sync_all(daily_run=daily, triggered_by=triggered_by)
                ok = all(r.trusted for r in results.values())
                daily.status = DailyRun.STATUS_COMPLETED if ok else DailyRun.STATUS_ATTENTION
                daily.finished_at = timezone.now()
                daily.summary = {"sync": workflow._sync_summary(results)}
                daily.save()
                status = daily.status
    except locks.LockNotAcquired:
        status = "skipped"
    except Exception:
        logger.exception("Scheduled job failed", extra={"job": schedule.job})
    finally:
        JobSchedule.objects.filter(pk=schedule.pk).update(last_finished_at=timezone.now(), last_status=status)
    return status


def tick(triggered_by="scheduler"):
    results = {}
    for schedule in JobSchedule.objects.filter(enabled=True):
        if is_due(schedule):
            results[schedule.job] = run_job(schedule, triggered_by)
    return results
