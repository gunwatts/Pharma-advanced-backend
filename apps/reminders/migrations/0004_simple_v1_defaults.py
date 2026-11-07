"""V1 defaults: plant workbook layout (one sheet per QMS event type + one Action Plans sheet),
per-event-type reminder cycles, Plant QA Head role and Mon-Sat 08:00 schedule.

All values are ordinary configuration the admin can change in Setup.
"""

import datetime

from django.db import migrations

EVENT_COLUMNS = {"id": "QMS Number", "department": "Department", "description": "Description", "target_date": "Target Date", "status": "Status"}
ACTION_COLUMNS = {"id": "Action ID", "qms_number": "QMS Number", "department": "Department", "description": "Description",
                  "target_date": "Target Date", "status": "Status"}
EVENT_SHEETS = {"change_control": "Change Control", "deviation": "Deviation", "incident": "Incident", "capa": "CAPA", "oos": "OOS", "oot": "OOT"}
STATUSES = [("Open", False), ("In Progress", False), ("Pending Approval", False), ("Completed", True), ("Closed", True), ("Cancelled", True)]
OLD_DEFAULT_SUBJECT = "QA reminders for {{ unit_name }} / {{ department_name }} - {{ run_date }} ({{ total_count }} item(s))"


def forwards(apps, schema_editor):
    Category = apps.get_model("catalog", "Category")
    FieldDefinition = apps.get_model("catalog", "FieldDefinition")
    StatusDefinition = apps.get_model("catalog", "StatusDefinition")
    Relationship = apps.get_model("catalog", "Relationship")
    ReminderRule = apps.get_model("reminders", "ReminderRule")
    EmailTemplate = apps.get_model("reminders", "EmailTemplate")
    JobSchedule = apps.get_model("reminders", "JobSchedule")
    Role = apps.get_model("accounts", "Role")

    Role.objects.get_or_create(key="plant_qa", defaults={
        "name": "Plant QA Head", "description": "Sees today's reminders and send history for their own plant.",
        "permissions": ["records.view", "runs.view"], "is_system": True})

    for key, sheet in EVENT_SHEETS.items():
        cat = Category.objects.filter(key=key).first()
        if cat and not cat.sheet_name:
            cat.sheet_name, cat.source_columns, cat.display_fields = sheet, EVENT_COLUMNS, ["title"]
            cat.save()

    action, created = Category.objects.get_or_create(key="action_plan", defaults={
        "name": "Action Plan", "kind": "action_item", "sort_order": 200, "title_field": "title", "display_fields": ["title"],
        "sheet_name": "Action Plans", "source_columns": ACTION_COLUMNS})
    if created:
        for i, (fkey, label, ftype, required) in enumerate([("title", "Description", "text", True), ("target_date", "Target date", "date", True),
                                                             ("qms_ref", "QMS number", "text", True)]):
            FieldDefinition.objects.create(category=action, key=fkey, label=label, field_type=ftype, required=required, sort_order=(i + 1) * 10)
    Relationship.objects.get_or_create(child_category=action, defaults={"parent_category": None, "link_field": "qms_ref"})

    # The earlier audit examples are not part of the plant workbook template; keep them but inactive.
    Category.objects.filter(key__in=["audit", "audit_observation", "audit_action_item"], mappings__isnull=True).update(active=False)

    for cat in Category.objects.filter(key__in=list(EVENT_SHEETS) + ["action_plan"]):
        for value, closed in STATUSES:
            StatusDefinition.objects.get_or_create(category=cat, value=value, defaults={"label": value, "is_closed": closed})
        if not ReminderRule.objects.filter(category=cat).exists():
            ReminderRule.objects.create(name=f"{cat.name} reminders", category=cat, target_date_field="target_date", window_days=7,
                                        repeat_every_days=15, repeat_anchor="due_date", start_days_before=90, include_statuses=["Open"])

    template = EmailTemplate.objects.filter(is_active=True).first()
    if template and template.subject == OLD_DEFAULT_SUBJECT:
        template.subject = "QMS reminders - {{ unit_name }} / {{ department_name }} - {{ run_date }} ({{ action_required_count }} action required)"
        template.intro_text = ("Dear {{ department_name }} team,\n\n"
                               "Please find below the QMS events and action plans of {{ unit_name }} assigned to your department. "
                               "{{ action_required_count }} item(s) need action soon and {{ periodic_count }} item(s) are periodic reminders.")
        template.outro_text = "Please complete or update these items before their target dates.\n\nRegards,\nCorporate Quality Assurance"
        template.no_pending_subject = "No pending QMS reminders - {{ unit_name }} / {{ department_name }} - {{ run_date }}"
        template.no_pending_text = ("Dear {{ department_name }} team,\n\nAll plant workbooks were read successfully. "
                                    "There are no QMS events or action plans needing your attention today.\n\nRegards,\nCorporate Quality Assurance")
        template.save()

    JobSchedule.objects.filter(job="daily_reminders", days_of_week=[0, 1, 2, 3, 4]).update(days_of_week=[0, 1, 2, 3, 4, 5], run_time=datetime.time(8, 0))


class Migration(migrations.Migration):
    dependencies = [
        ("reminders", "0003_reminderrule_repeat_anchor_and_more"),
        ("catalog", "0003_category_sheet_name_category_source_columns_and_more"),
        ("accounts", "0002_initial"),
        ("sources", "0002_datasource_plant"),
        ("records", "0002_remove_record_uniq_record_source_id_and_more"),
    ]

    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
