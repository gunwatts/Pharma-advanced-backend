"""Create a clearly-labelled DEMO setup: 2 plants, departments, example.com recipients, a log-only
sender and one synthetic workbook per plant. Nothing is ever delivered to real people."""

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.catalog.models import Department, RecipientGroup, Unit
from apps.core.models import SystemSettings
from apps.core.timeutils import business_today
from apps.reminders.models import SenderProfile
from apps.simple import layout
from apps.sources import connectors
from apps.sources.demo import build_plant_workbook
from apps.sources.sync import sync_all

DEPARTMENTS = [("QA", "Quality Assurance", ["QA"]), ("QC", "Quality Control", ["QC"]), ("PROD", "Production", []),
               ("ENG", "Engineering", []), ("STORES", "Stores", ["Warehouse"])]


class Command(BaseCommand):
    help = "Seed demo plants, departments, recipients (example.com), a log-only sender and synthetic plant workbooks."

    def handle(self, *args, **opts):
        depts = [Department.objects.get_or_create(code=c, defaults={"name": n, "aliases": a})[0] for c, n, a in DEPARTMENTS]
        SenderProfile.objects.get_or_create(name="Demo log-only sender", defaults={
            "provider": SenderProfile.PROVIDER_CONSOLE, "from_address": "qms.reminders@example.com", "from_name": "QMS Reminders (demo)",
            "secret_env_prefix": "SENDER_DEMO", "is_default": not SenderProfile.objects.filter(is_default=True).exists()})
        settings_obj = SystemSettings.load()
        if not settings_obj.alert_recipients:
            SystemSettings.objects.filter(pk=1).update(alert_recipients=["corporate.qa.head@example.com"])
        for code, name in (("P1", "Plant 1"), ("P2", "Plant 2")):
            plant, _ = Unit.objects.get_or_create(code=code, defaults={"name": name})
            for dept in depts:
                RecipientGroup.objects.get_or_create(unit=plant, department=dept, defaults={
                    "to_emails": [f"{dept.code.lower()}.head.{code.lower()}@example.com"], "cc_emails": [f"qa.head.{code.lower()}@example.com"]})
            source = layout.plant_workbook(plant)
            content = build_plant_workbook(code, business_today())
            source.uploaded_file_name = connectors.save_upload(source, SimpleUploadedFile(f"{code}_QMS.xlsx", content))
            source.uploaded_at, source.last_check_ok, source.last_check_message = timezone.now(), True, "Demo workbook uploaded."
            source.save()
        layout.rebuild_mappings()
        for run in sync_all(triggered_by="seed_demo").values():
            self.stdout.write(f"  {run.mapping}: {run.status} - {run.message}")
        self.stdout.write(self.style.SUCCESS("Demo ready: 2 plants x 5 departments. The demo sender only logs emails (nothing is delivered)."))
