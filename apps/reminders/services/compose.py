"""Build consolidated reminder emails. All dynamic values are HTML-escaped."""

from dataclasses import dataclass, field
from datetime import date

from django.conf import settings
from django.utils.html import escape

from apps.catalog.models import Category, FieldDefinition
from apps.reminders.services.eligibility import REASON_DUE_TODAY
from apps.reminders.services.templating import render_html_block, render_text

SECTION_TITLES = {
    Category.KIND_QMS: "QMS records",
    Category.KIND_AUDIT: "Audits and observations",
    Category.KIND_ACTION: "Action items",
}
SECTION_ORDER = [Category.KIND_QMS, Category.KIND_AUDIT, Category.KIND_ACTION]
SYSTEM_LABELS = {"source_id": "ID", "status": "Status", "title": "Title", "unit": "Unit", "department": "Department"}


@dataclass
class EmailContent:
    subject: str
    html: str
    text: str
    to: list = field(default_factory=list)
    cc: list = field(default_factory=list)


def fmt_date(value):
    return value.strftime("%d-%b-%Y") if isinstance(value, date) else ""


def _field_labels(category_ids):
    labels = {}
    for fd in FieldDefinition.objects.filter(category_id__in=category_ids):
        labels[(fd.category_id, fd.key)] = (fd.label, fd.field_type)
    return labels


def _display(record, key, labels):
    data = record.data or {}
    if key in ("unit", "department"):
        return getattr(record, key).name
    if key in ("source_id", "status") or (key == "title" and key not in data):
        return getattr(record, key) or ""
    value = data.get(key)
    if value is None:
        return ""
    label_type = labels.get((record.category_id, key), (key, "text"))[1]
    if label_type == FieldDefinition.TYPE_DATE:
        try:
            return date.fromisoformat(value).strftime("%d-%b-%Y")
        except (TypeError, ValueError):
            return str(value)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def build_context(group, items, run_date, window_days):
    counts = {kind: 0 for kind in SECTION_ORDER}
    for ev in items:
        counts[ev.record.category.kind] = counts.get(ev.record.category.kind, 0) + 1
    return {
        "unit_name": group.unit.name,
        "unit_code": group.unit.code,
        "department_name": group.department.name,
        "department_code": group.department.code,
        "run_date": fmt_date(run_date),
        "window_days": window_days,
        "total_count": len(items),
        "qms_count": counts[Category.KIND_QMS],
        "audit_count": counts[Category.KIND_AUDIT],
        "action_count": counts[Category.KIND_ACTION],
        "due_today_count": sum(1 for ev in items if ev.reason == REASON_DUE_TODAY),
        "app_url": settings.FRONTEND_URL or "",
    }


def _columns_for(category):
    cols = [c for c in (category.display_fields or []) if c not in ("source_id",)]
    return ["source_id"] + (cols or (["title"] if category.title_field else []))


TABLE = 'style="border-collapse:collapse;width:100%;margin:0 0 18px 0;font-size:13px"'
TH = 'style="background:#0f4c75;color:#ffffff;text-align:left;padding:6px 8px;border:1px solid #d0d7de"'
TD = 'style="padding:6px 8px;border:1px solid #d0d7de;vertical-align:top"'
CTX = 'style="padding:6px 8px;border:1px solid #d0d7de;background:#f3f6f9;font-size:12px;color:#334155"'


def _render_category_table(category, items, labels, show_parent):
    cols = _columns_for(category)
    header = "".join(f"<th {TH}>{escape(labels.get((category.pk, c), (SYSTEM_LABELS.get(c, c),))[0])}</th>" for c in cols)
    header += f"<th {TH}>Target date</th><th {TH}>Due</th>"
    if show_parent:
        header += f"<th {TH}>Parent record</th>"
    body = []
    text_lines = []
    for ev in items:
        rec = ev.record
        cells = "".join(f"<td {TD}>{escape(_display(rec, c, labels))}</td>" for c in cols)
        due = "TODAY" if ev.reason == REASON_DUE_TODAY else f"in {(ev.target_date - ev.run_date).days} day(s)"
        cells += f"<td {TD}>{escape(fmt_date(ev.target_date))}</td><td {TD}>{escape(due)}</td>"
        parent_text = ""
        if show_parent:
            parent = rec.parent
            parent_text = f"{parent.category.name} {parent.source_id}: {parent.title}".strip(": ") if parent else ""
            cells += f"<td {CTX}>{escape(parent_text)}</td>"
        body.append(f"<tr>{cells}</tr>")
        line = " | ".join(_display(rec, c, labels) for c in cols) + f" | Target: {fmt_date(ev.target_date)} ({due})"
        if parent_text:
            line += f" | Parent: {parent_text}"
        text_lines.append("  - " + line)
    html = f"<table {TABLE}><thead><tr>{header}</tr></thead><tbody>{''.join(body)}</tbody></table>"
    return html, text_lines


def compose_reminder(group, items, template, run_date, window_days, recipients=None) -> EmailContent:
    """One consolidated email with QMS, audit and action-item sections."""
    for ev in items:
        ev.run_date = run_date
    context = build_context(group, items, run_date, window_days)
    labels = _field_labels({ev.record.category_id for ev in items})
    html_parts = [render_html_block(template.intro_text, context)]
    text_parts = [render_text(template.intro_text, context), ""]

    for kind in SECTION_ORDER:
        section = [ev for ev in items if ev.record.category.kind == kind]
        if not section:
            continue
        html_parts.append(f'<h3 style="color:#0f4c75;margin:18px 0 8px 0;font-size:15px">{escape(SECTION_TITLES[kind])} ({len(section)})</h3>')
        text_parts.append(f"{SECTION_TITLES[kind]} ({len(section)})")
        by_category, categories = {}, {}
        for ev in section:
            categories[ev.record.category.pk] = ev.record.category
            by_category.setdefault(ev.record.category.pk, []).append(ev)
        for category in sorted(categories.values(), key=lambda c: (c.sort_order, c.name)):
            evs = sorted(by_category[category.pk], key=lambda e: (e.target_date, e.record.parent.source_id if e.record.parent else "", e.record.source_id))
            show_parent = template.show_parent_context and any(e.record.parent_id for e in evs)
            html_parts.append(f'<div style="font-weight:600;margin:6px 0">{escape(category.name)}</div>')
            table_html, lines = _render_category_table(category, evs, labels, show_parent)
            html_parts.append(table_html)
            text_parts.append(f" {category.name}:")
            text_parts.extend(lines)
        text_parts.append("")

    html_parts.append(render_html_block(template.outro_text, context))
    text_parts.append(render_text(template.outro_text, context))
    subject = render_text(template.subject, context)[:255]
    to, cc = recipients if recipients else (group.to_emails, group.cc_emails)
    return EmailContent(subject=subject, html=_wrap(subject, "".join(html_parts)), text="\n".join(text_parts).strip(), to=list(to), cc=list(cc))


def compose_no_pending(group, template, run_date, window_days, recipients=None) -> EmailContent:
    context = build_context(group, [], run_date, window_days)
    subject = render_text(template.no_pending_subject, context)[:255]
    to, cc = recipients if recipients else (group.to_emails, group.cc_emails)
    return EmailContent(subject=subject, html=_wrap(subject, render_html_block(template.no_pending_text, context)),
                        text=render_text(template.no_pending_text, context), to=list(to), cc=list(cc))


def _wrap(title, inner):
    return (
        "<!doctype html><html><head><meta charset=\"utf-8\"><title>"
        + escape(title)
        + "</title></head><body style=\"font-family:Segoe UI,Arial,sans-serif;color:#1f2933;font-size:14px;line-height:1.45\">"
        + inner
        + '<p style="color:#64748b;font-size:11px;margin-top:24px">Automated message from the QA Reminder System. '
        "Overdue records are not included in reminders; follow them up from the dashboard.</p></body></html>"
    )
