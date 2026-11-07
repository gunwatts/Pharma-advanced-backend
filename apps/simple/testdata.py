"""Deployment test data: one plant workbook with a row per functional test case.

Used by `manage.py make_test_workbook` and docs/DEPLOYMENT_TEST_CASES.md (section F). Expected
outcomes assume the default rule (Action required within 7 days, periodic every 15 days counted
back from the target date, horizon 90 days, only "Open" reminded) and that departments A-D exist
for the test plant. Every description starts with the test-case ID.
"""

import io
from dataclasses import dataclass
from datetime import timedelta

from openpyxl import Workbook

from apps.catalog.models import Category
from apps.simple.layout import ACTION_ROLES, EVENT_ROLES, default_columns, layout_settings


@dataclass
class Case:
    case_id: str
    sheet: str  # "event" (first event sheet) or "action"
    dept: str  # department letter A-D (or a literal name for rejection cases)
    offset: int | None  # target date = today + offset; None = empty
    status: str
    expected: str
    qms: str = ""
    link: str = ""  # action plans: QMS number of the parent event


def cases(plant_code):
    p = plant_code
    ev = lambda n: f"TST/{p}/{n:03d}"  # noqa: E731
    return [
        Case("F01", "event", "A", 0, "Open", "Dept A mail, 'Action required', days left TODAY", ev(1)),
        Case("F02", "event", "A", 7, "Open", "Dept A mail, 'Action required' (last day of the daily window)", ev(2)),
        Case("F03", "event", "A", 8, "Open", "NOT mailed today (outside daily window, not a periodic day)", ev(3)),
        Case("F04", "event", "A", 15, "Open", "Dept A mail, 'Periodic reminders'", ev(4)),
        Case("F05", "event", "A", 30, "Open", "Dept A mail, 'Periodic reminders'", ev(5)),
        Case("F06", "event", "A", -1, "Open", "NOT mailed (overdue - never emailed)", ev(6)),
        Case("F07", "event", "A", 105, "Open", "NOT mailed (beyond the 90-day horizon)", ev(7)),
        Case("F08", "event", "A", 3, "Closed", "NOT mailed and not stored (finished status)", ev(8)),
        Case("F09", "event", "C", 3, "Under Review", "NOT mailed; flagged as unknown status; Dept C mail HELD; listed in daily summary", ev(9)),
        Case("F10", "event", "C", None, "Open", "NOT mailed; flagged as missing target date; listed in daily summary", ev(10)),
        Case("F11", "action", "B", 2, "Open", "Dept B mail, 'Action required', parent event F01 shown", f"{ev(1)}-A1", ev(1)),
        Case("F12", "action", "A", 45, "Open", "Dept A mail, 'Periodic reminders', parent event F01 shown", f"{ev(1)}-A2", ev(1)),
        Case("F13", "action", "B", 4, "Completed", "NOT mailed and not stored (finished status)", f"{ev(1)}-A3", ev(1)),
        Case("F14", "action", "B", 1, "Open", "Dept B mail, 'Action required', no parent shown (QMS number not found)", f"{ev(99)}-A1", ev(99)),
        Case("F15", "action", "A", 5, "Open", "Dept A mail only - never in Dept B's mail", f"{ev(2)}-A1", ev(2)),
    ]


REJECT_CASES = {
    "unknown_department": Case("F20", "event", "Purchase", 2, "Open",
                               "Whole plant HELD (row rejected: department not configured); no 'nothing pending' mails", "TST/REJ/001"),
    "duplicate_id": Case("F21", "event", "A", 2, "Open", "Whole plant HELD (duplicate record ID); previous data kept", "TST/{p}/001"),
}


def build(plant_code, today, departments, variant=None):
    """Return (xlsx bytes, rows for the expected-results table)."""
    if len(departments) < 4:
        raise ValueError("Give four department names (A, B, C, D) that exist for the test plant.")
    letters = dict(zip("ABCD", departments, strict=False))
    layout = layout_settings()
    event_cats = list(Category.objects.filter(active=True).exclude(sheet_name="").exclude(kind=Category.KIND_ACTION).order_by("sort_order", "name"))
    action_cat = Category.objects.filter(active=True, kind=Category.KIND_ACTION).exclude(sheet_name="").order_by("sort_order").first()
    if not event_cats or action_cat is None:
        raise ValueError("The workbook layout needs at least one active event sheet and one Action Plans sheet.")
    rows = cases(plant_code)
    if variant:
        extra = REJECT_CASES[variant]
        extra.qms = extra.qms.format(p=plant_code)
        rows.append(extra)

    def headers(cat):
        cols = cat.source_columns or default_columns(cat.kind)
        return [(role, cols[role]) for role in (ACTION_ROLES if cat.kind == Category.KIND_ACTION else EVENT_ROLES) if cols.get(role)]

    def value(case, role):
        if role == "id":
            return case.qms
        if role == "qms_number":
            return case.link
        if role == "department":
            return letters.get(case.dept, case.dept)
        if role == "description":
            return f"{case.case_id} test item - {case.expected[:60]}"
        if role == "target_date":
            return (today + timedelta(days=case.offset)).strftime("%d-%m-%Y") if case.offset is not None else None
        if role == "status":
            return case.status
        return None

    wb = Workbook()
    wb.remove(wb.active)
    for cat in event_cats + [action_cat]:
        ws = wb.create_sheet(cat.sheet_name[:31])
        for _ in range(layout["header_row"] - 1):
            ws.append([])
        cols = headers(cat)
        ws.append([h for _, h in cols])
        kind = "action" if cat.kind == Category.KIND_ACTION else "event"
        if kind == "event" and cat != event_cats[0]:
            continue  # test rows go on the first event sheet; other event sheets stay empty but valid
        for case in rows:
            if case.sheet == kind:
                ws.append([value(case, role) for role, _ in cols])
    buf = io.BytesIO()
    wb.save(buf)
    table = [(c.case_id, (event_cats[0].sheet_name if c.sheet == "event" else action_cat.sheet_name), c.qms,
              letters.get(c.dept, c.dept), (today + timedelta(days=c.offset)).isoformat() if c.offset is not None else "(empty)",
              c.status, c.expected) for c in rows]
    return buf.getvalue(), table
