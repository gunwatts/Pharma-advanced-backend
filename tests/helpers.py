import io
import shutil
import tempfile
from datetime import timedelta

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from openpyxl import Workbook

from apps.catalog.models import Category, Department, RecipientGroup, Unit
from apps.core.models import SystemSettings
from apps.core.timeutils import business_today
from apps.reminders.models import ReminderRule, SenderProfile
from apps.sources import connectors
from apps.sources.models import DataSource, SourceMapping

HEADERS = ["Record No", "Plant", "Department", "Description", "Status", "Target Date", "Owner"]
COLUMN_MAP = {"title": "Description", "target_date": "Target Date", "owner": "Owner"}


def workbook(sheets):
    """sheets: {name: [header_row, *rows]} -> xlsx bytes"""
    wb = Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(name)
        for row in rows:
            ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def d(offset):
    return (business_today() + timedelta(days=offset)).strftime("%d-%m-%Y")


class QATestCase(TestCase):
    """Org with two units x two departments, console sender, upload source and helpers."""

    def setUp(self):
        cache.clear()
        self._tmp = tempfile.mkdtemp()
        self._override = override_settings(PRIVATE_UPLOAD_ROOT=self._tmp)
        self._override.enable()
        self.u1 = Unit.objects.create(code="U1", name="Plant 1", aliases=["P1"])
        self.u2 = Unit.objects.create(code="U2", name="Plant 2")
        self.qa = Department.objects.create(code="QA", name="Quality Assurance")
        self.qc = Department.objects.create(code="QC", name="Quality Control")
        self.groups = {}
        for u in (self.u1, self.u2):
            for dept in (self.qa, self.qc):
                self.groups[(u.code, dept.code)] = RecipientGroup.objects.create(
                    unit=u, department=dept, to_emails=[f"{dept.code}.{u.code}@example.com".lower()])
        self.sender = SenderProfile.objects.create(name="Console", provider="console", from_address="qa@example.com",
                                                   secret_env_prefix="SENDER_TEST", is_default=True, last_test_status="connected")
        self.cc = Category.objects.get(key="change_control")
        self.obs = Category.objects.get(key="audit_observation")
        self.act = Category.objects.get(key="audit_action_item")
        self.audit = Category.objects.get(key="audit")
        self.source = DataSource.objects.create(name="Test workbook", provider="upload")
        # Tests define their own rules; the old audit example categories are re-enabled for coverage.
        ReminderRule.objects.all().delete()
        Category.objects.filter(key__in=["audit", "audit_observation", "audit_action_item"]).update(active=True)
        SystemSettings.objects.filter(pk=1).update(reminders_active=True)

    def tearDown(self):
        self._override.disable()
        shutil.rmtree(self._tmp, ignore_errors=True)

    def upload(self, content, source=None):
        connectors.save_upload(source or self.source, SimpleUploadedFile("wb.xlsx", content))

    def mapping(self, category, sheet, extra_map=None, data_source=None, **kwargs):
        values = dict(id_column="Record No", unit_column="Plant", department_column="Department", status_column="Status",
                      column_map={**COLUMN_MAP, **(extra_map or {})}, date_formats=["%d-%m-%Y"])
        values.update(kwargs)
        return SourceMapping.objects.create(data_source=data_source or self.source, category=category, worksheet=sheet, **values)

    def rule(self, category, window=7, **kwargs):
        return ReminderRule.objects.create(name=f"{category.name} rule", category=category, target_date_field="target_date",
                                           window_days=window, **kwargs)

    def row(self, rid, unit="U1", dept="QA", offset=0, status="Open", title=None, owner="Owner"):
        return [rid, unit, dept, title or f"Record {rid}", status, d(offset) if offset is not None else None, owner]
