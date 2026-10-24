"""Create a complete, clearly-labelled DEMO configuration for evaluation/testing.

Uses synthetic data, example.com recipients and the log-only (console) sender,
so nothing is ever delivered to real people. Do not run in production.
"""

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.accounts.models import Role
from apps.catalog.models import Category, Department, RecipientGroup, Unit
from apps.core.timeutils import business_today
from apps.dashboards.models import Dashboard, DashboardAssignment, Widget
from apps.reminders.models import ReminderRule, SenderProfile
from apps.sources import connectors
from apps.sources.demo import BASE_MAP, DEMO_MAPPINGS, build_demo_workbook
from apps.sources.models import DataSource, SourceMapping
from apps.sources.sync import sync_all


class Command(BaseCommand):
    help = "Seed demo units, recipients (example.com), a log-only sender, a sample workbook, mappings, rules and a dashboard."

    def handle(self, *args, **opts):
        units = {code: Unit.objects.get_or_create(code=code, defaults={"name": name, "aliases": aliases})[0]
                 for code, name, aliases in [("UNIT1", "Plant 1", ["Plant-1", "P1"]), ("UNIT2", "Plant 2", ["Plant-2", "P2"])]}
        depts = {code: Department.objects.get_or_create(code=code, defaults={"name": name, "aliases": aliases})[0]
                 for code, name, aliases in [("QA", "Quality Assurance", []), ("QC", "Quality Control", []),
                                             ("PROD", "Production", []), ("ENG", "Engineering", [])]}
        for u in units.values():
            for d in depts.values():
                RecipientGroup.objects.get_or_create(unit=u, department=d, defaults={
                    "to_emails": [f"{d.code.lower()}.{u.code.lower()}@example.com"], "cc_emails": [f"qa.head.{u.code.lower()}@example.com"]})

        sender, _ = SenderProfile.objects.get_or_create(name="Demo log-only sender", defaults={
            "provider": SenderProfile.PROVIDER_CONSOLE, "from_address": "qa-reminders@example.com", "from_name": "QA Reminders (demo)",
            "secret_env_prefix": "SENDER_DEMO", "is_default": not SenderProfile.objects.filter(is_default=True).exists()})

        source, _ = DataSource.objects.get_or_create(name="Demo QMS workbook", defaults={"provider": DataSource.PROVIDER_UPLOAD,
                                                                                          "description": "Synthetic demo data"})
        content = build_demo_workbook(business_today())
        connectors.save_upload(source, SimpleUploadedFile("demo_qms.xlsx", content))
        source.uploaded_file_name, source.uploaded_at, source.last_check_ok = "demo_qms.xlsx", timezone.now(), True
        source.last_checked_at, source.last_check_message = timezone.now(), "Demo workbook uploaded."
        source.save()

        for key, sheet, extra in DEMO_MAPPINGS:
            cat = Category.objects.get(key=key)
            SourceMapping.objects.get_or_create(data_source=source, category=cat, worksheet=sheet, defaults={
                "id_column": "Record No", "unit_column": "Plant", "department_column": "Department", "status_column": "Status",
                "column_map": {**BASE_MAP, **extra}, "date_formats": ["%d-%m-%Y"], "last_validation_ok": True})
            ReminderRule.objects.get_or_create(name=f"{cat.name} - due within 7 days", category=cat,
                                               defaults={"target_date_field": "target_date", "window_days": 7})

        results = sync_all(triggered_by="seed_demo")
        for run in results.values():
            self.stdout.write(f"  {run.mapping}: {run.status} - {run.message}")

        dash, created = Dashboard.objects.get_or_create(
            name="QA Overview (demo)", defaults={"description": "Open records across QMS and audits", "is_published": True})
        if created:
            widgets = [
                dict(title="Open records", widget_type="kpi", width=1),
                dict(title="Records by category", widget_type="donut", group_by="category", width=1),
                dict(title="Records by department", widget_type="bar", group_by="department", width=1),
                dict(title="Due in the next 7 days", widget_type="upcoming", date_field="target_date", window_days=7, width=2, columns=["owner"]),
                dict(title="Overdue (follow up offline)", widget_type="overdue", date_field="target_date", width=1),
                dict(title="Target dates by month", widget_type="line", date_field="target_date", date_bucket="month", width=3),
            ]
            for i, w in enumerate(widgets):
                Widget.objects.create(dashboard=dash, position=i, **w)
            for role in Role.objects.all():
                DashboardAssignment.objects.get_or_create(dashboard=dash, role=role)
        self.stdout.write(self.style.SUCCESS(f"Demo data ready. Sender '{sender.name}' only logs emails (nothing is delivered)."))
