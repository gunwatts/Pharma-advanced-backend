"""Simple V1 setup: one identical workbook per plant.

The admin describes the workbook once (one sheet per QMS event type + one Action Plans sheet, with
column headers) and gives each plant its Excel file. This module turns that into the engine's
categories, fields, statuses, rules, relationships and per-plant sheet mappings, so adding a plant,
department or event type never needs code.
"""

import io
import re

from django.db import transaction
from django.utils.text import slugify
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

from apps.catalog.models import Category, FieldDefinition, Relationship, StatusDefinition, Unit
from apps.core.models import SystemSettings
from apps.reminders.models import ReminderRule
from apps.sources.models import DataSource, SourceMapping

EVENT_ROLES = ["id", "department", "description", "target_date", "status"]
ACTION_ROLES = ["id", "qms_number", "department", "description", "target_date", "status"]
ROLE_LABELS = {"id": "Record ID", "qms_number": "QMS number (link to event)", "department": "Department", "description": "Description",
               "target_date": "Target date", "status": "Status"}
DEFAULT_EVENT_COLUMNS = {"id": "QMS Number", "department": "Department", "description": "Description", "target_date": "Target Date", "status": "Status"}
DEFAULT_ACTION_COLUMNS = {"id": "Action ID", "qms_number": "QMS Number", "department": "Department", "description": "Description",
                          "target_date": "Target Date", "status": "Status"}
DEFAULT_LAYOUT = {"header_row": 1, "date_formats": ["%d-%m-%Y", "%d/%m/%Y", "%d-%b-%Y"], "max_rejected_rows": 0}


def layout_settings():
    return {**DEFAULT_LAYOUT, **(SystemSettings.load().workbook_layout or {})}


def roles_for(category):
    return ACTION_ROLES if category.kind == Category.KIND_ACTION else EVENT_ROLES


def default_columns(kind):
    return dict(DEFAULT_ACTION_COLUMNS if kind == Category.KIND_ACTION else DEFAULT_EVENT_COLUMNS)


def unique_key(name):
    base = re.sub(r"[^a-z0-9_]", "_", slugify(name).replace("-", "_"))[:50].strip("_") or "event"
    if not base[0].isalpha():
        base = f"e_{base}"
    key, n = base, 2
    while Category.objects.filter(key=key).exists():
        key, n = f"{base}_{n}", n + 1
    return key


def ensure_fields(category):
    wanted = [("title", "Description", FieldDefinition.TYPE_TEXT, True), ("target_date", "Target date", FieldDefinition.TYPE_DATE, True)]
    if category.kind == Category.KIND_ACTION:
        wanted.append(("qms_ref", "QMS number", FieldDefinition.TYPE_TEXT, True))
    for i, (key, label, ftype, required) in enumerate(wanted):
        FieldDefinition.objects.update_or_create(category=category, key=key, defaults={
            "label": label, "field_type": ftype, "required": required, "active": True, "sort_order": (i + 1) * 10})
    if not category.title_field:
        category.title_field = "title"
        category.display_fields = ["title"]
        category.save(update_fields=["title_field", "display_fields"])
    if category.kind == Category.KIND_ACTION:
        Relationship.objects.update_or_create(child_category=category, defaults={"parent_category": None, "link_field": "qms_ref", "active": True})


def set_statuses(category, open_statuses, closed_statuses):
    keep = set()
    for value, closed in [(v, False) for v in open_statuses] + [(v, True) for v in closed_statuses]:
        StatusDefinition.objects.update_or_create(category=category, value=value, defaults={"label": value, "is_closed": closed})
        keep.add(value)
    StatusDefinition.objects.filter(category=category).exclude(value__in=keep).delete()


def rule_for(category):
    rule = ReminderRule.objects.filter(category=category).order_by("id").first()
    if rule is None:
        rule = ReminderRule.objects.create(name=f"{category.name} reminders", category=category, target_date_field="target_date",
                                           window_days=7, repeat_every_days=15, start_days_before=90, include_statuses=["Open"])
    return rule


def event_type_payload(category):
    rule = rule_for(category)
    statuses = list(category.statuses.all())
    return {
        "id": category.pk,
        "key": category.key,
        "name": category.name,
        "kind": "action" if category.kind == Category.KIND_ACTION else "event",
        "sheet_name": category.sheet_name,
        "active": category.active,
        "columns": {role: (category.source_columns or {}).get(role, "") for role in roles_for(category)},
        "open_statuses": [s.value for s in statuses if not s.is_closed],
        "closed_statuses": [s.value for s in statuses if s.is_closed],
        "rule": {"window_days": rule.window_days, "repeat_every_days": rule.repeat_every_days, "repeat_anchor": rule.repeat_anchor,
                 "start_days_before": rule.start_days_before},
        "sort_order": category.sort_order,
    }


@transaction.atomic
def save_event_type(data, category=None):
    kind = Category.KIND_ACTION if data.get("kind") == "action" else Category.KIND_QMS
    if category is None:
        category = Category.objects.create(key=unique_key(data["name"]), name=data["name"], kind=kind,
                                           sort_order=data.get("sort_order", 100 if kind != Category.KIND_ACTION else 200))
    category.name = data.get("name", category.name)
    category.sheet_name = data.get("sheet_name", category.sheet_name)
    category.active = data.get("active", category.active)
    columns = {**default_columns(category.kind), **(category.source_columns or {}), **(data.get("columns") or {})}
    category.source_columns = {role: columns.get(role, "").strip() for role in roles_for(category)}
    category.save()
    ensure_fields(category)
    if "open_statuses" in data or "closed_statuses" in data:
        set_statuses(category, data.get("open_statuses", []), data.get("closed_statuses", []))
    rule = rule_for(category)
    for key in ("window_days", "repeat_every_days", "repeat_anchor", "start_days_before"):
        if key in (data.get("rule") or {}):
            setattr(rule, key, data["rule"][key])
    rule.include_statuses = [s.value for s in category.statuses.filter(is_closed=False)]
    rule.exclude_statuses, rule.conditions = [], []
    rule.target_date_field, rule.active = "target_date", category.active
    rule.save()
    SystemSettings.bump_config_version()
    rebuild_mappings()
    return category


def plant_workbook(plant):
    source = DataSource.objects.filter(plant=plant).first()
    if source is None:
        source = DataSource.objects.create(name=f"{plant.name} workbook"[:128], provider=DataSource.PROVIDER_UPLOAD, plant=plant)
    return source


def rebuild_mappings():
    """Create/update one sheet mapping per (plant workbook x event-type sheet). Idempotent."""
    layout = layout_settings()
    categories = list(Category.objects.exclude(sheet_name=""))
    for source in DataSource.objects.filter(plant__isnull=False).select_related("plant"):
        plant = source.plant
        for cat in categories:
            cols = cat.source_columns or {}
            column_map = {"title": cols.get("description", ""), "target_date": cols.get("target_date", "")}
            if cat.kind == Category.KIND_ACTION:
                column_map["qms_ref"] = cols.get("qms_number", "")
            values = {
                "name": f"{plant.name} / {cat.sheet_name}"[:128], "worksheet": cat.sheet_name, "header_row": layout["header_row"],
                "id_column": cols.get("id", ""), "unit_column": "", "fixed_unit": plant, "department_column": cols.get("department", ""),
                "status_column": cols.get("status", ""), "column_map": {k: v for k, v in column_map.items() if v},
                "date_formats": layout["date_formats"], "max_rejected_rows": layout["max_rejected_rows"],
                "active": bool(cat.active and plant.active and source.active),
            }
            mapping = SourceMapping.objects.filter(data_source=source, category=cat).first()
            if mapping is None:
                SourceMapping.objects.create(data_source=source, category=cat, **values)
            else:
                changed = any(getattr(mapping, k) != v for k, v in values.items())
                if changed:
                    for k, v in values.items():
                        setattr(mapping, k, v)
                    mapping.last_validation_ok = None
                    mapping.save()


def plant_payload(plant):
    source = DataSource.objects.filter(plant=plant).first()
    mappings = list(SourceMapping.objects.filter(data_source=source, active=True)) if source else []
    statuses = {m.last_status for m in mappings}
    last = max((m.last_attempt_at for m in mappings if m.last_attempt_at), default=None)
    errors = [f"{m.worksheet}: {m.last_error}" for m in mappings if m.last_error]
    groups = {g.department_id: g for g in plant.recipient_groups.all()}
    return {
        "id": plant.pk, "code": plant.code, "name": plant.name, "active": plant.active, "aliases": plant.aliases,
        "workbook": None if source is None else {
            "id": source.pk, "provider": source.provider, "file": source.config.get("display_path") or source.uploaded_file_name or "",
            "web_url": source.config.get("web_url", ""), "linked": bool(source.config.get("item_id") or source.uploaded_file_name),
            "last_check_ok": source.last_check_ok, "last_check_message": source.last_check_message,
            "last_sync_status": "never" if not last else ("success" if statuses == {"success"} else "problem"),
            "last_sync_at": last, "errors": errors[:10],
        },
        "departments": [{"group_id": g.pk, "department": g.department_id, "department_name": g.department.name,
                         "to": g.to_emails, "cc": g.cc_emails, "active": g.active} for g in groups.values()],
    }


def template_workbook():
    """A standard plant workbook template built from the current layout (for plants to adopt)."""
    wb = Workbook()
    wb.remove(wb.active)
    layout = layout_settings()
    info = wb.create_sheet("How to use")
    info.append(["QMS reminder workbook template - one file per plant. Keep sheet names and column headers exactly as given."])
    info.append([f"Dates: real Excel dates, or text like {', '.join(layout['date_formats'])}."])
    info.append(["Status: reminders are sent only for open statuses; closed statuses stop reminders."])
    info.append(["Action Plans: every row must carry the QMS number of its event (any event sheet) and the responsible department."])
    bold, fill = Font(bold=True, color="FFFFFF"), PatternFill("solid", fgColor="0F4C75")
    for cat in Category.objects.filter(active=True).exclude(sheet_name="").order_by("sort_order", "name"):
        ws = wb.create_sheet(cat.sheet_name[:31])
        cols = cat.source_columns or default_columns(cat.kind)
        headers = [cols.get(role) for role in roles_for(cat) if cols.get(role)]
        for _ in range(layout["header_row"] - 1):
            ws.append([])
        ws.append(headers)
        for cell in ws[layout["header_row"]]:
            cell.font, cell.fill = bold, fill
        for i, _h in enumerate(headers, start=1):
            ws.column_dimensions[get_column_letter(i)].width = 24
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def plants():
    return Unit.objects.order_by("name")
