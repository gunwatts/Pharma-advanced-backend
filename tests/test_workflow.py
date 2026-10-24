from datetime import timedelta
from unittest import mock

from django.utils import timezone

from apps.core import locks
from apps.core.models import SystemSettings
from apps.reminders.models import DailyRun, Delivery, JobSchedule
from apps.reminders.services import delivery, providers, workflow
from apps.reminders.services.scheduler import is_due, next_run_at, run_job

from .helpers import HEADERS, QATestCase, workbook


def outcomes(run):
    return {(g["unit"], g["department"]): g["outcome"] for g in run.summary["groups"]}


class WorkflowTests(QATestCase):
    def setUp(self):
        super().setUp()
        self.m_cc = self.mapping(self.cc, "CC")
        self.rule(self.cc)

    def set_rows(self, cc_rows, extra_sheets=None):
        self.upload(workbook({"CC": [HEADERS, *cc_rows], **(extra_sheets or {})}))

    def run_daily(self, **kw):
        return workflow.run_daily(kind=DailyRun.KIND_MANUAL, triggered_by="test", **kw)

    def test_one_consolidated_email_per_unit_department(self):
        self.set_rows([self.row("CC-1", offset=0), self.row("CC-2", offset=3), self.row("CC-3", dept="QC", offset=1),
                       self.row("CC-4", offset=-1), self.row("CC-5", offset=8)])
        with mock.patch.object(providers.ConsoleProvider, "_send", wraps=providers.ConsoleProvider(self.sender)._send) as send:
            run = self.run_daily()
        self.assertEqual(run.status, DailyRun.STATUS_COMPLETED)
        p1qa = Delivery.objects.get(unit=self.u1, department=self.qa)
        self.assertEqual(p1qa.kind, "reminder")
        self.assertEqual(p1qa.item_counts["total"], 2)  # CC-1 today + CC-2 in window; overdue/beyond excluded
        self.assertEqual(Delivery.objects.get(unit=self.u1, department=self.qc).item_counts["total"], 1)
        self.assertEqual(send.call_count, 4)  # 4 groups -> 4 emails (2 reminders + 2 no-pending)
        email = send.call_args_list[0].args[0]
        self.assertEqual(email.to, self.groups[("U1", "QA")].to_emails if "Assurance" in email.subject else email.to)

    def test_past_dates_never_emailed(self):
        self.set_rows([self.row("CC-1", offset=-1), self.row("CC-2", offset=-30)])
        plan = workflow.build_plan(sync=True)
        gp = next(g for g in plan.groups if g.group.unit == self.u1 and g.group.department == self.qa)
        self.assertEqual(gp.items, [])
        self.assertEqual({e.reason for e in gp.excluded}, {"overdue"})
        self.assertEqual(gp.action, "no_pending")

    def test_dry_run_explains_inclusions_and_exclusions_without_sending(self):
        self.set_rows([self.row("CC-1", offset=0), self.row("CC-2", offset=-2), self.row("CC-3", offset=20)])
        preview = workflow.dry_run(sync=True)
        g = next(g for g in preview["groups"] if g["unit"] == "Plant 1" and g["department"] == "Quality Assurance")
        self.assertEqual([i["source_id"] for i in g["included"]], ["CC-1"])
        self.assertEqual({e["source_id"]: e["reason"] for e in g["excluded"]}, {"CC-2": "overdue", "CC-3": "beyond_window"})
        self.assertIn("CC-1", g["email"]["html"])
        self.assertFalse(Delivery.objects.exists())

    def test_parent_context_and_only_department_child_actions(self):
        self.mapping(self.audit, "AUD")
        self.mapping(self.obs, "OBS", {"audit_ref": "Audit No"})
        self.mapping(self.act, "ACT", {"observation_ref": "Obs No"})
        self.rule(self.act)
        self.set_rows([], {
            "AUD": [HEADERS, self.row("AUD-1", offset=30)],
            "OBS": [HEADERS + ["Audit No"], self.row("OBS-1", offset=30, title="Line clearance gap") + ["AUD-1"]],
            "ACT": [HEADERS + ["Obs No"], self.row("ACT-QA", offset=2, title="QA retraining") + ["OBS-1"],
                    self.row("ACT-QC", dept="QC", offset=2, title="QC secret detail") + ["OBS-1"]],
        })
        plan = workflow.build_plan(sync=True)
        by_group = {(g.group.unit.code, g.group.department.code): g for g in plan.groups}
        qa_html, qc_html = by_group[("U1", "QA")].email.html, by_group[("U1", "QC")].email.html
        self.assertIn("ACT-QA", qa_html)
        self.assertNotIn("ACT-QC", qa_html)
        self.assertNotIn("QC secret detail", qa_html)
        self.assertIn("ACT-QC", qc_html)
        self.assertNotIn("ACT-QA", qc_html)
        for html in (qa_html, qc_html):  # parent context in each relevant department email
            self.assertIn("OBS-1", html)
            self.assertIn("Line clearance gap", html)
        self.assertIn("Action items", qa_html)

    def test_email_sections_distinguish_qms_audit_and_actions(self):
        self.mapping(self.obs, "OBS", {"audit_ref": "Audit No"})
        self.rule(self.obs)
        self.set_rows([self.row("CC-1")], {"OBS": [HEADERS + ["Audit No"], self.row("OBS-1", offset=1) + ["AUD-X"]]})
        plan = workflow.build_plan(sync=True)
        html = next(g for g in plan.groups if g.group.unit == self.u1 and g.group.department == self.qa).email.html
        self.assertIn("QMS records (1)", html)
        self.assertIn("Audits and observations (1)", html)

    def test_dynamic_data_is_html_escaped(self):
        self.set_rows([self.row("CC-1", title="<img src=x onerror=alert(1)>")])
        plan = workflow.build_plan(sync=True)
        html = next(g for g in plan.groups if g.group.unit == self.u1 and g.group.department == self.qa).email.html
        self.assertNotIn("<img src=x", html)
        self.assertIn("&lt;img", html)

    def test_no_pending_sent_only_after_successful_sync(self):
        self.set_rows([self.row("CC-1", offset=-5)])
        run = self.run_daily()
        self.assertEqual(Delivery.objects.get(unit=self.u1, department=self.qa).kind, "no_pending")
        self.assertEqual(outcomes(run)[("Plant 1", "Quality Assurance")], "sent")

    def test_no_pending_suppressed_when_source_fails(self):
        # Nothing uploaded: the source fails -> every group affected is held, nothing sent.
        run = self.run_daily()
        self.assertEqual(run.status, DailyRun.STATUS_ATTENTION)
        self.assertEqual(set(outcomes(run).values()), {"held"})
        self.assertFalse(Delivery.objects.filter(state="sent", kind__in=["reminder", "no_pending"]).exists())

    def test_no_pending_suppressed_when_sync_incomplete(self):
        self.set_rows([self.row("CC-1", offset=-5), self.row("CC-1", offset=-5)])  # duplicate -> incomplete
        run = self.run_daily()
        self.assertEqual(set(outcomes(run).values()), {"held"})

    def test_failed_fixed_unit_source_only_holds_that_unit(self):
        self.m_cc.unit_column, self.m_cc.fixed_unit = "", self.u1
        self.m_cc.save()
        from apps.sources.models import DataSource
        other = DataSource.objects.create(name="Plant 2 file", provider="upload")
        self.mapping(self.cc, "CC", data_source=other, unit_column="", fixed_unit=self.u2)
        self.upload(workbook({"CC": [[h for h in HEADERS if h != "Plant"], ["CC-9", "QA", "x", "Open", "01-01-2001", "o"]]}), source=other)
        # Unit 1 source has no upload -> fails.
        run = self.run_daily()
        oc = outcomes(run)
        self.assertEqual(oc[("Plant 1", "Quality Assurance")], "held")
        self.assertEqual(oc[("Plant 2", "Quality Assurance")], "sent")

    def test_records_with_data_issues_suppress_no_pending(self):
        self.set_rows([self.row("CC-1", offset=None)])
        run = self.run_daily()
        self.assertEqual(outcomes(run)[("Plant 1", "Quality Assurance")], "held")
        self.assertEqual(outcomes(run)[("Plant 1", "Quality Control")], "sent")

    def test_requires_activation(self):
        SystemSettings.objects.filter(pk=1).update(reminders_active=False)
        run = self.run_daily()
        self.assertEqual(run.status, DailyRun.STATUS_SKIPPED)
        self.assertFalse(Delivery.objects.exists())

    def test_rerun_does_not_resend_successful_groups(self):
        self.set_rows([self.row("CC-1")])
        self.run_daily()
        with mock.patch.object(providers.ConsoleProvider, "_send") as send:
            run = self.run_daily()
        send.assert_not_called()
        self.assertTrue(all(v == "skipped_sent" for v in outcomes(run).values()))

    def test_group_failure_does_not_resend_successful_group_and_retries_only_failed(self):
        self.set_rows([self.row("CC-1"), self.row("CC-2", dept="QC")])
        qc_suffix = f":d{self.qc.pk}"

        def qc_fails(self_, email, key):
            if key.endswith(qc_suffix):
                return providers.SendResult(providers.FAILED, error="boom")
            return providers.SendResult(providers.ACCEPTED, message_id="m")

        with mock.patch.object(providers.ConsoleProvider, "_send", qc_fails):
            first = self.run_daily()
        self.assertEqual(first.status, DailyRun.STATUS_ATTENTION)
        failed = Delivery.objects.filter(state="failed")
        self.assertEqual(set(failed.values_list("department", flat=True)), {self.qc.pk})
        sent_before = set(Delivery.objects.filter(state="sent").values_list("idempotency_key", flat=True))
        with mock.patch.object(providers.ConsoleProvider, "_send", return_value=providers.SendResult(providers.ACCEPTED, "m2")) as send:
            workflow.run_daily(kind=DailyRun.KIND_RETRY, triggered_by="test")
        retried = {c.args[1] for c in send.call_args_list}
        self.assertTrue(retried.isdisjoint(sent_before))
        self.assertEqual(len(retried), 2)  # the two QC groups only
        self.assertFalse(Delivery.objects.filter(state="failed").exists())

    def test_uncertain_delivery_is_not_resent_until_reconciled(self):
        self.set_rows([self.row("CC-1")])
        with mock.patch.object(providers.ConsoleProvider, "_send", return_value=providers.SendResult(providers.UNCERTAIN, error="timeout")):
            self.run_daily()
        entry = Delivery.objects.get(unit=self.u1, department=self.qa)
        self.assertEqual(entry.state, "uncertain")
        with mock.patch.object(providers.ConsoleProvider, "_send") as send:
            self.run_daily()
        send.assert_not_called()
        delivery.reconcile(entry, "authorize_retry", None, "Checked mailbox; not received")
        with mock.patch.object(providers.ConsoleProvider, "_send", return_value=providers.SendResult(providers.ACCEPTED, "ok")) as send:
            self.run_daily()
        self.assertEqual(send.call_count, 1)  # only the reconciled group; the other 3 remain uncertain
        entry.refresh_from_db()
        self.assertEqual(entry.state, "sent")

    def test_stale_in_progress_becomes_uncertain(self):
        self.set_rows([self.row("CC-1")])
        entry = delivery.ledger_entry(delivery.group_key(workflow.business_today(), self.u1.pk, self.qa.pk), workflow.business_today(), "reminder")
        Delivery.objects.filter(pk=entry.pk).update(state="in_progress", last_attempt_at=timezone.now() - timedelta(hours=1))
        with mock.patch.object(providers.ConsoleProvider, "_send", return_value=providers.SendResult(providers.ACCEPTED, "m")):
            self.run_daily()
        entry.refresh_from_db()
        self.assertEqual(entry.state, "uncertain")

    def test_max_attempts_stops_automatic_retries(self):
        SystemSettings.objects.filter(pk=1).update(max_send_attempts=2)
        self.set_rows([self.row("CC-1")])
        with mock.patch.object(providers.ConsoleProvider, "_send", return_value=providers.SendResult(providers.FAILED, error="x")):
            for _ in range(3):
                self.run_daily()
        entry = Delivery.objects.get(unit=self.u1, department=self.qa)
        self.assertEqual(entry.attempts, 2)

    def test_concurrent_run_is_skipped_by_lock(self):
        self.set_rows([self.row("CC-1")])
        holder = locks.acquire(workflow.LOCK_NAME)
        try:
            run = self.run_daily()
        finally:
            locks.release(workflow.LOCK_NAME, holder)
        self.assertEqual(run.status, DailyRun.STATUS_SKIPPED)
        self.assertFalse(Delivery.objects.exists())

    def test_stale_lock_is_taken_over(self):
        holder = locks.acquire("x", ttl_minutes=60)
        from apps.core.models import JobLock
        JobLock.objects.filter(name="x").update(expires_at=timezone.now() - timedelta(minutes=1))
        self.assertNotEqual(locks.acquire("x"), holder)

    def test_test_email_only_goes_to_test_recipient(self):
        self.set_rows([self.row("CC-1")])
        workflow.build_plan(sync=True)
        group = self.groups[("U1", "QA")]
        with mock.patch.object(providers.ConsoleProvider, "_send", return_value=providers.SendResult(providers.ACCEPTED, "t")) as send:
            outcome, delivers = workflow.send_test_email("tester@example.com", group_id=group.pk, profile=self.sender)
        email = send.call_args.args[0]
        self.assertEqual(email.to, ["tester@example.com"])
        self.assertEqual(email.cc, [])
        self.assertTrue(email.subject.startswith("[TEST]"))
        self.assertFalse(delivers)  # console provider never claims delivery
        self.assertFalse(Delivery.objects.filter(kind="reminder").exists())

    def test_template_with_unknown_placeholder_is_rejected_by_api_serializer(self):
        from apps.reminders.serializers import EmailTemplateSerializer
        s = EmailTemplateSerializer(data={"name": "x", "subject": "{{ password }}", "no_pending_subject": "ok"})
        self.assertFalse(s.is_valid())
        self.assertIn("subject", s.errors)


class SchedulerTests(QATestCase):
    def test_due_once_per_day(self):
        from datetime import time
        sched = JobSchedule.objects.get(job="daily_reminders")
        sched.run_time, sched.days_of_week = time(0, 0), []
        sched.save()
        self.assertTrue(is_due(sched))
        self.assertIsNotNone(next_run_at(sched))
        with mock.patch("apps.reminders.services.workflow.run_daily", return_value=DailyRun(status="completed")) as rd:
            run_job(sched)
            sched.refresh_from_db()
            self.assertFalse(is_due(sched))
            self.assertIsNone(run_job(sched))  # already claimed today
        self.assertEqual(rd.call_count, 1)
