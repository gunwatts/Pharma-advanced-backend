"""Dashboard widget queries.

Every query starts from the user's server-side scoped record set; widget
configuration can only choose among fixed fields and operators (no SQL).
"""

from collections import Counter
from datetime import date, timedelta

from apps.accounts.permissions import scope_queryset
from apps.catalog.models import FieldDefinition
from apps.core.timeutils import business_today
from apps.records.models import Record
from apps.reminders.services.eligibility import parse_iso
from apps.reminders.services.rules import OPERATORS, condition_matches

SYSTEM_GROUP_FIELDS = {"category": "Category", "unit": "Unit", "department": "Department", "status": "Status"}


class QueryError(ValueError):
    pass


def scoped_records(user):
    return scope_queryset(Record.objects.select_related("category", "unit", "department", "parent", "parent__category"), user)


def value_of(record, field):
    if field == "category":
        return record.category.name
    if field == "unit":
        return record.unit.name
    if field == "department":
        return record.department.name
    if field in ("status", "source_id", "title"):
        return getattr(record, field)
    if field == "has_issues":
        return "Yes" if record.issues else "No"
    return (record.data or {}).get(field)


def validate_filters(filters):
    if not isinstance(filters, list):
        raise QueryError("Filters must be a list.")
    for f in filters:
        if not isinstance(f, dict) or not f.get("field") or f.get("op") not in OPERATORS:
            raise QueryError("Each filter needs a field and a supported operator.")


def apply_filters(records, filters):
    validate_filters(filters)
    for f in filters:
        records = [r for r in records if condition_matches(value_of(r, f["field"]), f["op"], f.get("value"))]
    return records


def base_records(user, category_id=None, filters=None):
    qs = scoped_records(user)
    if category_id:
        qs = qs.filter(category_id=category_id)
    return apply_filters(list(qs), filters or [])


def _bucket(d: date, size):
    if size == "day":
        return d.isoformat()
    if size == "week":
        monday = d - timedelta(days=d.weekday())
        return monday.isoformat()
    return d.strftime("%Y-%m")


def _row(record, columns, date_types):
    row = {"id": record.pk, "category": record.category.name, "source_id": record.source_id, "title": record.title,
           "status": record.status, "unit": record.unit.name, "department": record.department.name, "has_issues": bool(record.issues)}
    for col in columns:
        if col not in row:
            row[col] = value_of(record, col)
    return row


def _date_types(category_id):
    qs = FieldDefinition.objects.filter(field_type=FieldDefinition.TYPE_DATE)
    if category_id:
        qs = qs.filter(category_id=category_id)
    return {fd.key for fd in qs}


def widget_data(widget, user, today=None):
    today = today or business_today()
    records = base_records(user, widget.category_id, widget.filters)
    wtype = widget.widget_type

    if wtype == "kpi":
        return {"value": len(records)}

    if wtype in ("pie", "donut", "bar"):
        field = widget.group_by or "status"
        counts = Counter(str(value_of(r, field) or "(empty)") for r in records)
        items = [{"key": k, "label": k, "count": v} for k, v in counts.most_common()]
        if widget.sort_by == "label":
            items.sort(key=lambda i: i["label"], reverse=widget.sort_desc)
        return {"group_by": field, "series": items[: max(1, widget.limit or 10) if wtype != "bar" else max(1, widget.limit or 20)], "total": len(records)}

    if wtype in ("line", "area"):
        if not widget.date_field:
            raise QueryError("Choose a date field for line/area charts.")
        counts = Counter()
        for r in records:
            d = parse_iso(value_of(r, widget.date_field))
            if d:
                counts[_bucket(d, widget.date_bucket)] += 1
        return {"series": [{"key": k, "label": k, "count": counts[k]} for k in sorted(counts)], "total": len(records)}

    if wtype in ("upcoming", "overdue"):
        if not widget.date_field:
            raise QueryError("Choose a date field.")
        rows = []
        for r in records:
            d = parse_iso(value_of(r, widget.date_field))
            if d is None:
                continue
            if wtype == "upcoming" and today <= d <= today + timedelta(days=widget.window_days):
                rows.append((d, r))
            elif wtype == "overdue" and d < today:
                rows.append((d, r))
        rows.sort(key=lambda x: x[0])
        out = []
        for d, r in rows[: widget.limit or 10]:
            row = _row(r, widget.columns or [], set())
            row["target_date"] = d.isoformat()
            row["days"] = (d - today).days
            out.append(row)
        return {"rows": out, "total": len(rows)}

    if wtype == "table":
        if widget.sort_by:
            def key(r):
                v = value_of(r, widget.sort_by)
                return (v is None, str(v) if v is not None else "")
            records.sort(key=key, reverse=widget.sort_desc)
        return {"rows": [_row(r, widget.columns or [], set()) for r in records[: widget.limit or 25]], "total": len(records)}

    raise QueryError(f"Unsupported widget type '{wtype}'.")


def drill_records(widget, user, group_value=None, today=None):
    """Records behind a widget (and optionally one chart segment) for click-through."""
    today = today or business_today()
    records = base_records(user, widget.category_id, widget.filters)
    if group_value is not None:
        if widget.widget_type in ("line", "area") and widget.date_field:
            records = [r for r in records if (d := parse_iso(value_of(r, widget.date_field))) and _bucket(d, widget.date_bucket) == group_value]
        else:
            field = widget.group_by or "status"
            records = [r for r in records if str(value_of(r, field) or "(empty)") == group_value]
    if widget.widget_type in ("upcoming", "overdue") and widget.date_field:
        def keep(r):
            d = parse_iso(value_of(r, widget.date_field))
            if d is None:
                return False
            return d < today if widget.widget_type == "overdue" else today <= d <= today + timedelta(days=widget.window_days)
        records = [r for r in records if keep(r)]
    return records
