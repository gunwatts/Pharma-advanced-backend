"""Generate the deployment test workbook (docs/DEPLOYMENT_TEST_CASES.md, section F).

    python manage.py make_test_workbook --plant TEST --out test_plant.xlsx
    python manage.py make_test_workbook --plant TEST --upload          # attach to the TEST plant
    python manage.py make_test_workbook --plant TEST --variant duplicate_id --upload

Never use a production plant: create a dedicated test plant whose departments' To/Cc are test
mailboxes (or use the log-only sender).
"""

from datetime import date
from pathlib import Path

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.catalog.models import RecipientGroup, Unit
from apps.core.timeutils import business_today
from apps.simple import layout, testdata
from apps.sources import connectors
from apps.sources.models import DataSource


class Command(BaseCommand):
    help = "Create an Excel workbook with one row per functional test case and print the expected results."

    def add_arguments(self, parser):
        parser.add_argument("--plant", required=True, help="Code of the TEST plant (e.g. TEST).")
        parser.add_argument("--departments", help="Four department names for A,B,C,D. Default: the plant's first four departments.")
        parser.add_argument("--out", help="Write the workbook to this .xlsx file.")
        parser.add_argument("--upload", action="store_true", help="Attach the workbook to the plant as an uploaded file.")
        parser.add_argument("--variant", choices=sorted(testdata.REJECT_CASES), help="Add a row that must make the whole plant HELD.")
        parser.add_argument("--date", help="Pretend today is YYYY-MM-DD (default: today in the business timezone).")

    def handle(self, *args, **opts):
        plant = Unit.objects.filter(code__iexact=opts["plant"]).first()
        if plant is None:
            raise CommandError(f"Plant '{opts['plant']}' not found. Create a dedicated test plant in Setup first.")
        if opts["departments"]:
            departments = [d.strip() for d in opts["departments"].split(",") if d.strip()]
        else:
            departments = list(RecipientGroup.objects.filter(unit=plant, active=True).order_by("department__name")
                               .values_list("department__name", flat=True)[:4])
        today = date.fromisoformat(opts["date"]) if opts["date"] else business_today()
        try:
            content, table = testdata.build(plant.code, today, departments, opts["variant"])
        except ValueError as exc:
            raise CommandError(str(exc))
        if opts["out"]:
            Path(opts["out"]).write_bytes(content)
            self.stdout.write(f"Workbook written to {opts['out']}")
        if opts["upload"]:
            source = layout.plant_workbook(plant)
            source.provider, source.config = DataSource.PROVIDER_UPLOAD, {}
            source.uploaded_file_name = connectors.save_upload(source, SimpleUploadedFile(f"{plant.code}_TEST.xlsx", content))
            source.uploaded_at = timezone.now()
            source.save()
            self.stdout.write(f"Workbook uploaded to plant {plant.name}")
        if not opts["out"] and not opts["upload"]:
            raise CommandError("Use --out and/or --upload.")
        letters = dict(zip("ABCD", departments, strict=False))
        self.stdout.write(f"\nTest date {today:%d-%b-%Y}; departments: " + ", ".join(f"{k}={v}" for k, v in letters.items()))
        self.stdout.write(f"{'Case':5} {'Sheet':16} {'Record':22} {'Dept':18} {'Target':11} {'Status':13} Expected")
        for row in table:
            self.stdout.write(f"{row[0]:5} {row[1][:16]:16} {row[2][:22]:22} {row[3][:18]:18} {row[4]:11} {row[5][:13]:13} {row[6]}")
        self.stdout.write(f"F16   -                -                      {letters['D'][:18]:18} -           -             "
                          "'Nothing pending' mail (only if the plant workbook was read without rejected rows)")
