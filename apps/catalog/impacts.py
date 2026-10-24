"""Describe configuration that depends on an item, so admins are warned before destructive changes."""

from apps.dashboards.models import Widget
from apps.records.models import Record
from apps.reminders.models import ReminderRule
from apps.sources.models import SourceMapping


def field_usage(category_id, key):
    impacts = []
    for m in SourceMapping.objects.filter(category_id=category_id):
        if key in (m.column_map or {}):
            impacts.append(f"Excel mapping '{m}' maps this field.")
    for r in ReminderRule.objects.filter(category_id=category_id):
        if r.target_date_field == key:
            impacts.append(f"Reminder rule '{r.name}' uses it as the target date.")
        if any(c.get("field") == key for c in r.conditions or []):
            impacts.append(f"Reminder rule '{r.name}' has a condition on it.")
    from apps.catalog.models import Category, Relationship
    cat = Category.objects.filter(pk=category_id).first()
    if cat and (key in (cat.display_fields or []) or cat.title_field == key):
        impacts.append(f"Category '{cat.name}' displays it in emails/tables.")
    for rel in Relationship.objects.filter(child_category_id=category_id, link_field=key):
        impacts.append(f"Relationship '{rel}' uses it as the parent link.")
    for w in Widget.objects.filter(category_id=category_id).select_related("dashboard"):
        used = key in (w.group_by, w.date_field, w.sort_by) or key in (w.columns or []) or any(f.get("field") == key for f in w.filters or [])
        if used:
            impacts.append(f"Dashboard widget '{w.dashboard.name}: {w.title}' uses it.")
    return impacts


def category_usage(category):
    impacts = []
    n = Record.objects.filter(category=category).count()
    if n:
        impacts.append(f"{n} current record(s) belong to this category.")
    impacts += [f"Excel mapping '{m}'." for m in SourceMapping.objects.filter(category=category)]
    impacts += [f"Reminder rule '{r.name}'." for r in ReminderRule.objects.filter(category=category)]
    impacts += [f"Dashboard widget '{w.dashboard.name}: {w.title}'." for w in Widget.objects.filter(category=category).select_related("dashboard")]
    if hasattr(category, "parent_relationship"):
        impacts.append(f"Relationship '{category.parent_relationship}'.")
    impacts += [f"Relationship '{r}'." for r in category.child_relationships.all()]
    return impacts


def status_usage(status):
    return [f"Reminder rule '{r.name}' filters on this status." for r in ReminderRule.objects.filter(category_id=status.category_id)
            if status.value in (r.include_statuses or []) + (r.exclude_statuses or [])]


def org_usage(obj, kind):
    impacts = []
    filt = {kind: obj}
    n = Record.objects.filter(**filt).count()
    if n:
        impacts.append(f"{n} current record(s) are assigned to it.")
    impacts += [f"Recipient group '{g}'." for g in obj.recipient_groups.all()]
    return impacts
