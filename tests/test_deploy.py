"""Automated checks behind the deployment test cases (docs/DEPLOYMENT_TEST_CASES.md)."""

import io
from contextlib import redirect_stdout
from datetime import date
from unittest import mock

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import override_settings
from openpyxl import load_workbook

from apps.accounts.models import Role
from apps.catalog.models import Department, RecipientGroup, Unit
from apps.core.models import SystemSettings
from apps.reminders.models import ReminderRule
from apps.reminders.services import workflow
from apps.simple import layout
from apps.sources.models import SourceMapping

from .helpers import QATestCase


def run_check(*args):
    out = io.StringIO()
    code = 0
    try:
        call_command("deploy_check", *args, stdout=out)
    except SystemExit as exc:
        code = exc.code
    return code, out.getvalue()


class DeployCheckTests(QATestCase):
    def test_reports_each_area_and_fails_on_debug(self):
        with override_settings(DEBUG=True):
            code, out = run_check()
        self.assertEqual(code, 1)
        for name in ("DEBUG off", "Database reachable", "Migrations applied", "Business timezone", "Administrator account", "Active mail template"):
            self.assertIn(name, out)
        self.assertRegex(out, r"FAIL\s+DEBUG off")

    @override_settings(DEBUG=False, SECRET_KEY="x" * 50, ALLOWED_HOSTS=["qms.company.local"], SECURE_SSL_REDIRECT=True)
    def test_passes_core_checks_when_configured(self):
        user = User.objects.create_user("cqa", password="Str0ng-Passphrase!")
        user.profile.role = Role.objects.get(key="admin")
        user.profile.save()
        code, out = run_check()
        self.assertNotRegex(out, r"FAIL\s")
        self.assertEqual(code, 0, out)
        self.assertRegex(out, r"PASS\s+Migrations applied")
        self.assertRegex(out, r"WARN\s+Microsoft 365 app details")  # not configured in tests

    def test_unknown_timezone_fails(self):
        SystemSettings.objects.filter(pk=1).update(business_timezone="Mars/Olympus")
        code, out = run_check()
        self.assertRegex(out, r"FAIL\s+Business timezone")

    def test_online_check_reports_unreachable_workbook(self):
        plant = Unit.objects.create(code="TEST", name="Test Plant")
        layout.plant_workbook(plant)
        with mock.patch("apps.core.m365.access_token", return_value="t"):
            code, out = run_check("--online")
        self.assertRegex(out, r"PASS\s+Microsoft 365 token")
        self.assertRegex(out, r"FAIL\s+Workbook TEST")


class TestWorkbookTests(QATestCase):
    """Runs the section-F test workbook through the real engine and asserts the documented outcomes."""

    def setUp(self):
        super().setUp()
        SourceMapping.objects.update(active=False)
        ReminderRule.objects.all().delete()
        self.plant = Unit.objects.create(code="TEST", name="Test Plant")
        self.depts = [Department.objects.get(code=c) for c in ("QA", "QC")] + [
            Department.objects.create(code="PROD", name="Production"), Department.objects.create(code="ENG", name="Engineering")]
        for d in self.depts:
            RecipientGroup.objects.create(unit=self.plant, department=d, to_emails=[f"{d.code.lower()}@example.com"])
        for cat in layout.Category.objects.exclude(sheet_name=""):
            layout.rule_for(cat)
        layout.plant_workbook(self.plant)
        layout.rebuild_mappings()
        self.today = date(2026, 11, 10)  # a Tuesday

    def generate(self, variant=None):
        args = ["--plant", "TEST", "--upload", "--date", self.today.isoformat(), "--departments", ",".join(d.name for d in self.depts)]
        if variant:
            args += ["--variant", variant]
        out = io.StringIO()
        call_command("make_test_workbook", *args, stdout=out)
        return out.getvalue()

    def plan(self):
        with mock.patch("apps.reminders.services.workflow.business_today", return_value=self.today):
            return workflow.build_plan(run_date=self.today, sync=True)

    def test_expected_outcomes(self):
        printed = self.generate()
        self.assertIn("F16", printed)
        plan = self.plan()
        g = {gp.group.department.name: gp for gp in plan.groups if gp.group.unit == self.plant}
        a, b, c, d = (g[x.name] for x in self.depts)
        ids = lambda items, section=None: {e.record.title[:3] for e in items if section is None or e.section == section}  # noqa: E731
        self.assertEqual(ids(a.items, "action_required"), {"F01", "F02", "F15"})
        self.assertEqual(ids(a.items, "periodic"), {"F04", "F05", "F12"})
        self.assertEqual(ids(b.items, "action_required"), {"F11", "F14"})
        self.assertTrue({"F03", "F06", "F07"} <= ids(a.excluded))
        self.assertEqual({e.reason for e in a.excluded}, {"not_cycle_day", "overdue", "not_started"})
        self.assertEqual(ids(c.flagged), {"F09", "F10"})
        self.assertEqual((a.action, b.action, c.action, d.action), ("reminder", "reminder", "held", "no_pending"))
        self.assertIn("F01 test item", a.email.html)  # F12 shows its parent event (F01) in Dept A's mail
        self.assertNotIn("F15", b.email.html)
        self.assertFalse(any(e.record.title.startswith(("F08", "F13")) for gp in plan.groups for e in gp.items + gp.excluded))

    def test_rejected_rows_hold_the_whole_plant(self):
        for variant in ("unknown_department", "duplicate_id"):
            self.generate(variant)
            plan = self.plan()
            actions = {gp.action for gp in plan.groups if gp.group.unit == self.plant}
            self.assertEqual(actions, {"held"}, variant)

    def test_workbook_file_has_layout_headers(self):
        import os
        import tempfile

        from django.core.management import call_command as cc
        path = os.path.join(tempfile.mkdtemp(), "t.xlsx")
        with redirect_stdout(io.StringIO()):
            cc("make_test_workbook", "--plant", "TEST", "--out", path, "--departments", "QA,QC,Production,Engineering", stdout=io.StringIO())
        wb = load_workbook(path)
        self.assertEqual([c.value for c in wb["Action Plans"][1]], ["Action ID", "QMS Number", "Department", "Description", "Target Date", "Status"])
        self.assertTrue(any(str(c.value).startswith("F01") for c in wb[wb.sheetnames[0]]["C"]))
