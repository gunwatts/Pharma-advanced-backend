"""Synthetic demo workbook (no real data) used by `seed_demo` and tests."""

import io
from datetime import timedelta

from openpyxl import Workbook

HEADERS = ["Record No", "Plant", "Department", "Description", "Status", "Target Date", "Initiated On", "Owner"]


def _rows(prefix, today, extra=None):
    plan = [
        (0, "UNIT1", "QA", "Open"), (2, "UNIT1", "QC", "In Progress"), (5, "UNIT1", "Production", "Open"),
        (-3, "UNIT1", "QA", "Open"), (12, "UNIT1", "QA", "Pending Approval"), (1, "UNIT2", "QA", "Open"),
        (7, "UNIT2", "Engineering", "In Progress"), (3, "UNIT1", "QA", "Closed"),
    ]
    rows = []
    for i, (offset, unit, dept, status) in enumerate(plan, start=1):
        row = [f"{prefix}-{today.year}-{i:03d}", unit, dept, f"{prefix} sample record {i}", status,
               (today + timedelta(days=offset)).strftime("%d-%m-%Y"), (today - timedelta(days=30)).strftime("%d-%m-%Y"), f"Owner {i}"]
        rows.append(row + (extra(i) if extra else []))
    return rows


def build_demo_workbook(today) -> bytes:
    wb = Workbook()
    sheets = {
        "Change Control": ("CC", None, []),
        "Deviation": ("DEV", None, []),
        "CAPA": ("CAPA", None, []),
        "Audit": ("AUD", lambda i: ["Internal"], ["Audit Type"]),
        "Observations": ("OBS", lambda i: [f"AUD-{today.year}-{min(i, 3):03d}"], ["Audit No"]),
        "Action Items": ("ACT", lambda i: [f"OBS-{today.year}-{(i % 3) + 1:03d}"], ["Observation No"]),
    }
    first = True
    for title, (prefix, extra, extra_headers) in sheets.items():
        ws = wb.active if first else wb.create_sheet()
        first = False
        ws.title = title
        ws.append(HEADERS + extra_headers)
        for row in _rows(prefix, today, extra):
            ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


BASE_MAP = {"title": "Description", "target_date": "Target Date", "initiated_on": "Initiated On", "owner": "Owner"}
DEMO_MAPPINGS = [
    ("change_control", "Change Control", {}),
    ("deviation", "Deviation", {}),
    ("capa", "CAPA", {}),
    ("audit", "Audit", {"audit_type": "Audit Type"}),
    ("audit_observation", "Observations", {"audit_ref": "Audit No"}),
    ("audit_action_item", "Action Items", {"observation_ref": "Observation No"}),
]
