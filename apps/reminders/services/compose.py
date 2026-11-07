"""Build consolidated reminder emails. All dynamic values are HTML-escaped."""

from dataclasses import dataclass, field
from datetime import date

from django.conf import settings
from django.utils.html import escape

from apps.catalog.models import Category, FieldDefinition
from apps.reminders.services.eligibility import REASON_DUE_TODAY, SECTION_ACTION, SECTION_PERIODIC
from apps.reminders.services.templating import render_html_block, render_text

SECTION_TITLES = {
    SECTION_ACTION: "Action required - due soon",
    SECTION_PERIODIC: "Periodic reminders - upcoming items",
}
SECTION_NOTES = {
    SECTION_ACTION: "These items are due within the next few days. Please act on them now.",
    SECTION_PERIODIC: "Advance reminder of items assigned to your department with later target dates.",
}
SECTION_ORDER = [SECTION_ACTION, SECTION_PERIODIC]
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
    action = sum(1 for ev in items if ev.section == SECTION_ACTION)
    return {
        "unit_name": group.unit.name,
        "unit_code": group.unit.code,
        "department_name": group.department.name,
        "department_code": group.department.code,
        "run_date": fmt_date(run_date),
        "window_days": window_days,
        "total_count": len(items),
        "action_required_count": action,
        "periodic_count": len(items) - action,
        "qms_count": sum(1 for ev in items if ev.record.category.kind != Category.KIND_ACTION),
        "action_count": sum(1 for ev in items if ev.record.category.kind == Category.KIND_ACTION),
        "due_today_count": sum(1 for ev in items if ev.reason == REASON_DUE_TODAY),
        "app_url": settings.FRONTEND_URL or "",
    }


TABLE = 'style="border-collapse:collapse;width:100%;margin:0 0 18px 0;font-size:13px"'
TH = 'style="background:#0f4c75;color:#ffffff;text-align:left;padding:6px 8px;border:1px solid #d0d7de"'
TD = 'style="padding:6px 8px;border:1px solid #d0d7de;vertical-align:top"'
CTX = 'style="display:block;color:#475569;font-size:12px;margin-top:2px"'
DUE_SOON = 'style="padding:6px 8px;border:1px solid #d0d7de;vertical-align:top;font-weight:700;color:#a32020"'
COLUMNS = ["QMS No.", "Event type", "Action ID", "Description", "Target date", "Days left", "Status"]


def _row_values(ev, show_parent):
    """(qms_no, event_type, action_id, description, parent_context) for an event or an action plan."""
    rec = ev.record
    if rec.category.kind == Category.KIND_ACTION:
        parent = rec.parent
        qms_no = parent.source_id if parent else (rec.parent_ref or "")
        event_type = parent.category.name if parent else ""
        context = f"Event: {parent.title}" if (parent and show_parent and parent.title) else ""
        return qms_no, event_type, rec.source_id, rec.title, context
    return rec.source_id, rec.category.name, "", rec.title, ""


def _days_label(ev, run_date):
    days = (ev.target_date - run_date).days
    return "TODAY" if days == 0 else f"{days} day(s)"


def _render_section(items, run_date, show_parent):
    header = "".join(f"<th {TH}>{escape(c)}</th>" for c in COLUMNS)
    rows, lines = [], []
    for ev in sorted(items, key=lambda e: (e.target_date, _row_values(e, False)[0], e.record.source_id)):
        qms_no, event_type, action_id, description, context = _row_values(ev, show_parent)
        days = _days_label(ev, run_date)
        desc_html = escape(description) + (f"<span {CTX}>{escape(context)}</span>" if context else "")
        cells = [escape(qms_no), escape(event_type), escape(action_id or "-"), desc_html, escape(fmt_date(ev.target_date))]
        row = "".join(f"<td {TD}>{c}</td>" for c in cells)
        row += f"<td {DUE_SOON if ev.section == SECTION_ACTION else TD}>{escape(days)}</td><td {TD}>{escape(ev.record.status or '')}</td>"
        rows.append(f"<tr>{row}</tr>")
        line = f"  - {qms_no} ({event_type})" + (f" / action {action_id}" if action_id else "") + f": {description}"
        line += f" | target {fmt_date(ev.target_date)} ({days}) | {ev.record.status}"
        if context:
            line += f" | {context}"
        lines.append(line)
    return f"<table {TABLE}><thead><tr>{header}</tr></thead><tbody>{''.join(rows)}</tbody></table>", lines


def compose_reminder(group, items, template, run_date, window_days, recipients=None) -> EmailContent:
    """One consolidated department email with an 'Action required' and a 'Periodic reminders' section."""
    context = build_context(group, items, run_date, window_days)
    html_parts = [render_html_block(template.intro_text, context)]
    text_parts = [render_text(template.intro_text, context), ""]
    for section in SECTION_ORDER:
        section_items = [ev for ev in items if ev.section == section]
        if not section_items:
            continue
        color = "#a32020" if section == SECTION_ACTION else "#0f4c75"
        html_parts.append(f'<h3 style="color:{color};margin:18px 0 4px 0;font-size:15px">{escape(SECTION_TITLES[section])} ({len(section_items)})</h3>')
        html_parts.append(f'<p style="margin:0 0 8px 0;color:#475569;font-size:12px">{escape(SECTION_NOTES[section])}</p>')
        table_html, lines = _render_section(section_items, run_date, template.show_parent_context)
        html_parts.append(table_html)
        text_parts.append(f"{SECTION_TITLES[section]} ({len(section_items)})")
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
        + '<p style="color:#64748b;font-size:11px;margin-top:24px">Automated message from the QMS Reminder System. '
        "Items past their target date are not included; please follow them up directly.</p></body></html>"
    )
