from datetime import time
from unittest import mock

from django.test import override_settings
from rest_framework.test import APIClient

from apps.reminders.models import DailyRun, JobSchedule

from .helpers import QATestCase


class JobTriggerTests(QATestCase):
    url = "/api/jobs/tick/"

    @override_settings(JOB_TRIGGER_TOKEN="")
    def test_disabled_without_configured_token(self):
        self.assertEqual(APIClient().post(self.url, HTTP_X_JOB_TOKEN="anything").status_code, 404)

    @override_settings(JOB_TRIGGER_TOKEN="s3cret-token")
    def test_rejects_wrong_or_missing_token(self):
        self.assertEqual(APIClient().post(self.url).status_code, 403)
        self.assertEqual(APIClient().post(self.url, HTTP_X_JOB_TOKEN="wrong").status_code, 403)

    @override_settings(JOB_TRIGGER_TOKEN="s3cret-token")
    def test_runs_due_job_once_per_day(self):
        sched = JobSchedule.objects.get(job="daily_reminders")
        sched.run_time, sched.days_of_week = time(0, 0), []
        sched.save()
        with mock.patch("apps.reminders.services.workflow.run_daily", return_value=DailyRun(status="completed")) as run:
            first = APIClient().post(self.url, HTTP_X_JOB_TOKEN="s3cret-token")
            second = APIClient().post(self.url, HTTP_X_JOB_TOKEN="s3cret-token")
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.data["ran"], {"daily_reminders": "completed"})
        self.assertEqual(second.data["ran"], {})
        self.assertEqual(run.call_count, 1)

    @override_settings(JOB_TRIGGER_TOKEN="s3cret-token")
    def test_get_not_allowed(self):
        self.assertEqual(APIClient().get(self.url, HTTP_X_JOB_TOKEN="s3cret-token").status_code, 405)
