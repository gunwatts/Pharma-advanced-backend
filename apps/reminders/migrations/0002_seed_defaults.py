"""Seed initial roles, example categories, relationships, template and schedules.

Everything seeded here is ordinary configuration that admins can edit or
deactivate from Advanced Setup; nothing is hard-coded in business logic.
"""

import datetime

from django.db import migrations

ALL_PERMISSIONS = ["admin.configure", "admin.users", "runs.view", "runs.manage", "records.view", "records.export",
                   "dashboards.view", "dashboards.manage", "audit.view"]

ROLES = [
    ("admin", "QA Administrator", "Full configuration and operations access.", ALL_PERMISSIONS),
    ("qa_manager", "QA Manager", "Views records, dashboards and run history; can export.",
     ["records.view", "records.export", "dashboards.view", "runs.view"]),
    ("viewer", "Department Viewer", "Views dashboards and records within their data scope.", ["records.view", "dashboards.view"]),
]

COMMON_FIELDS = [
    ("title", "Title / description", "text", True),
    ("target_date", "Target date", "date", True),
    ("initiated_on", "Initiated on", "date", False),
    ("owner", "Owner", "text", False),
]

CATEGORIES = [
    ("change_control", "Change Control", "qms", 10, []),
    ("capa", "CAPA", "qms", 20, []),
    ("deviation", "Deviation", "qms", 30, []),
    ("incident", "Incident", "qms", 40, []),
    ("oos", "OOS (Out of Specification)", "qms", 50, []),
    ("oot", "OOT (Out of Trend)", "qms", 60, []),
    ("audit", "Audit", "audit", 70, [("audit_type", "Audit type", "text", False)]),
    ("audit_observation", "Audit Observation", "audit", 80, [("audit_ref", "Audit number", "text", True)]),
    ("audit_action_item", "Audit Action Item", "action_item", 90, [("observation_ref", "Observation number", "text", True)]),
]

STATUSES = [("Open", False), ("In Progress", False), ("Pending Approval", False), ("Closed", True), ("Cancelled", True)]

RELATIONSHIPS = [("audit", "audit_observation", "audit_ref"), ("audit_observation", "audit_action_item", "observation_ref")]


def seed(apps, schema_editor):
    Role = apps.get_model("accounts", "Role")
    Category = apps.get_model("catalog", "Category")
    FieldDefinition = apps.get_model("catalog", "FieldDefinition")
    StatusDefinition = apps.get_model("catalog", "StatusDefinition")
    Relationship = apps.get_model("catalog", "Relationship")
    EmailTemplate = apps.get_model("reminders", "EmailTemplate")
    JobSchedule = apps.get_model("reminders", "JobSchedule")
    SystemSettings = apps.get_model("core", "SystemSettings")

    for key, name, desc, perms in ROLES:
        Role.objects.get_or_create(key=key, defaults={"name": name, "description": desc, "permissions": perms, "is_system": True})

    for key, name, kind, order, extra in CATEGORIES:
        cat, created = Category.objects.get_or_create(
            key=key, defaults={"name": name, "kind": kind, "sort_order": order, "title_field": "title",
                               "display_fields": ["title", "status", "owner"]})
        if not created:
            continue
        for i, (fkey, label, ftype, required) in enumerate(COMMON_FIELDS + extra):
            FieldDefinition.objects.create(category=cat, key=fkey, label=label, field_type=ftype, required=required, sort_order=(i + 1) * 10)
        for value, closed in STATUSES:
            StatusDefinition.objects.create(category=cat, value=value, label=value, is_closed=closed)

    for parent, child, link in RELATIONSHIPS:
        p, c = Category.objects.get(key=parent), Category.objects.get(key=child)
        Relationship.objects.get_or_create(child_category=c, defaults={"parent_category": p, "link_field": link})

    if not EmailTemplate.objects.exists():
        EmailTemplate.objects.create(
            name="Default daily reminder",
            is_active=True,
            subject="QA reminders for {{ unit_name }} / {{ department_name }} - {{ run_date }} ({{ total_count }} item(s))",
            intro_text=("Dear {{ department_name }} team,\n\n"
                        "The following QMS records, audit items and action items for {{ unit_name }} are due today or within "
                        "the next {{ window_days }} days. {{ due_today_count }} item(s) are due today."),
            outro_text="Please complete or update these records before their target dates.\n\nRegards,\nQA Reminder System",
            no_pending_subject="No pending QA reminders for {{ unit_name }} / {{ department_name }} - {{ run_date }}",
            no_pending_text=("Dear {{ department_name }} team,\n\nAll configured sources were synced and validated successfully. "
                             "There are no QMS records, audit items or action items due in the next {{ window_days }} days.\n\n"
                             "Regards,\nQA Reminder System"),
        )

    JobSchedule.objects.get_or_create(job="daily_reminders", defaults={"run_time": datetime.time(8, 0), "days_of_week": [0, 1, 2, 3, 4], "enabled": True})
    JobSchedule.objects.get_or_create(job="sync_sources", defaults={"run_time": datetime.time(7, 30), "days_of_week": [], "enabled": False})
    SystemSettings.objects.get_or_create(pk=1)


class Migration(migrations.Migration):
    dependencies = [
        ("reminders", "0001_initial"),
        ("accounts", "0002_initial"),
        ("catalog", "0002_initial"),
        ("core", "0001_initial"),
    ]

    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
