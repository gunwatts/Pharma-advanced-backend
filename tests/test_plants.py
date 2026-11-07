from unittest import mock

from apps.catalog.models import Category
from apps.core.models import SystemSettings
from apps.records.models import Record
from apps.reminders.models import DailyRun, Delivery, ReminderRule
from apps.reminders.services import providers, workflow
from apps.sources.models import DataSource

from .helpers import QATestCase, d, workbook

EVENT_HEADERS = ["QMS Number", "Department", "Description", "Target Date", "Status"]
ACTION_HEADERS = ["Action ID", "QMS Number", "Department", "Description", "Target Date", "Status"]


class PlantWorkbookTests(QATestCase):
    """One workbook per plant: event sheets + one Action Plans sheet linked by QMS number."""

    def setUp(self):
        super().setUp()
        self.capa = Category.objects.get(key="capa")
        self.action = Category.objects.get(key="action_plan")
        self.src2 = DataSource.objects.create(name="Plant 2 workbook", provider="upload")
        self.maps = {}
        for plant, source in ((self.u1, self.source), (self.u2, self.src2)):
            for cat, sheet, extra in ((self.cc, "Change Control", {}), (self.capa, "CAPA", {}), (self.action, "Action Plans", {"qms_ref": "QMS Number"})):
                id_col = "Action ID" if cat == self.action else "QMS Number"
                self.maps[(plant.code, cat.key)] = self.mapping(cat, sheet, extra, data_source=source, id_column=id_col, unit_column="",
                                                                fixed_unit=plant, column_map={"title": "Description", "target_date": "Target Date", **extra})
        for cat in (self.cc, self.capa, self.action):
            ReminderRule.objects.create(name=cat.name, category=cat, target_date_field="target_date", window_days=7, repeat_every_days=15,
                                        start_days_before=90, include_statuses=["Open"])

    def sync_plant(self, plant, sheets):
        source = self.source if plant == self.u1 else self.src2
        self.upload(workbook(sheets), source=source)

    def test_same_qms_number_in_two_plants_and_actions_route_to_their_departments(self):
        cc_rows = [EVENT_HEADERS, ["CC-001", "Production", "New product XYZ", d(60), "Open"]]
        actions = [ACTION_HEADERS, ["CC-001-A1", "CC-001", "QC", "Prepare specification", d(3), "Open"],
                   ["CC-001-A2", "CC-001", "QA", "Create material codes", d(15), "Open"],
                   ["CC-001-A3", "CC-001", "QA", "Already done", d(2), "Completed"]]
        from apps.catalog.models import Department
        Department.objects.create(code="PROD", name="Production")
        self.sync_plant(self.u1, {"Change Control": cc_rows, "CAPA": [EVENT_HEADERS], "Action Plans": actions})
        self.sync_plant(self.u2, {"Change Control": cc_rows, "CAPA": [EVENT_HEADERS], "Action Plans": [ACTION_HEADERS]})
        plan = workflow.build_plan(sync=True)
        self.assertEqual(Record.objects.filter(source_id="CC-001").count(), 2)  # same number, two plants
        a1 = Record.objects.get(source_id="CC-001-A1")
        self.assertEqual((a1.parent.source_id, a1.parent.unit), ("CC-001", self.u1))  # parent resolved within the plant
        groups = {(g.group.unit.code, g.group.department.code): g for g in plan.groups}
        qc = groups[("U1", "QC")]
        self.assertEqual([(e.record.source_id, e.section) for e in qc.items], [("CC-001-A1", "action_required")])
        self.assertIn("New product XYZ", qc.email.html)  # parent event context
        qa = groups[("U1", "QA")]
        self.assertEqual([(e.record.source_id, e.section) for e in qa.items], [("CC-001-A2", "periodic")])
        self.assertIn("Periodic reminders", qa.email.html)
        self.assertNotIn("CC-001-A1", qa.email.html)
        self.assertFalse(Record.objects.filter(source_id="CC-001-A3").exists())  # completed -> not stored
        self.assertEqual(groups[("U2", "QA")].action, "no_pending")

    def test_one_plant_workbook_failing_holds_only_that_plant(self):
        self.sync_plant(self.u2, {"Change Control": [EVENT_HEADERS], "CAPA": [EVENT_HEADERS], "Action Plans": [ACTION_HEADERS]})
        run = workflow.run_daily(kind=DailyRun.KIND_MANUAL, triggered_by="t")  # plant 1 has no workbook uploaded
        outcomes = {(g["unit"], g["department"]): g["outcome"] for g in run.summary["groups"]}
        self.assertEqual(outcomes[("Plant 1", "Quality Assurance")], "held")
        self.assertEqual(outcomes[("Plant 2", "Quality Assurance")], "sent")

    def test_admin_daily_summary_sent_every_run(self):
        SystemSettings.objects.filter(pk=1).update(alert_recipients=["cqa.head@example.com"])
        for plant in (self.u1, self.u2):
            self.sync_plant(plant, {"Change Control": [EVENT_HEADERS], "CAPA": [EVENT_HEADERS], "Action Plans": [ACTION_HEADERS]})
        with mock.patch.object(providers.ConsoleProvider, "_send", return_value=providers.SendResult(providers.ACCEPTED, "m")) as send:
            run = workflow.run_daily(kind=DailyRun.KIND_MANUAL, triggered_by="t")
        self.assertEqual(run.status, DailyRun.STATUS_COMPLETED)
        summary = Delivery.objects.get(kind="alert")
        self.assertEqual(summary.recipients_to, ["cqa.head@example.com"])
        self.assertIn("all OK", summary.subject)
        self.assertEqual(send.call_count, 5)  # 4 department mails + 1 summary

    def test_unknown_status_is_flagged_and_reported_not_silently_skipped(self):
        SystemSettings.objects.filter(pk=1).update(alert_recipients=["cqa.head@example.com"])
        self.sync_plant(self.u1, {"Change Control": [EVENT_HEADERS, ["CC-9", "QA", "Typo status", d(2), "Opne"]],
                                  "CAPA": [EVENT_HEADERS], "Action Plans": [ACTION_HEADERS]})
        self.sync_plant(self.u2, {"Change Control": [EVENT_HEADERS], "CAPA": [EVENT_HEADERS], "Action Plans": [ACTION_HEADERS]})
        plan = workflow.build_plan(sync=True)
        qa = next(g for g in plan.groups if g.group.unit == self.u1 and g.group.department == self.qa)
        self.assertEqual([e.reason for e in qa.flagged], ["data_issue"])
        self.assertEqual(qa.action, "held")  # no misleading "nothing pending"
        with mock.patch.object(providers.ConsoleProvider, "_send", return_value=providers.SendResult(providers.ACCEPTED, "m")):
            workflow.run_daily(kind=DailyRun.KIND_MANUAL, triggered_by="t")
        summary = Delivery.objects.get(kind="alert")
        self.assertIn("attention needed", summary.subject)
