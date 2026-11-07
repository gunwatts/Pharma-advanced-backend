"""Synthetic demo plant workbooks (no real data) used by `seed_demo` and tests."""

import io
from datetime import timedelta

from openpyxl import Workbook

EVENT = ["QMS Number", "Department", "Description", "Target Date", "Status"]
ACTION = ["Action ID", "QMS Number", "Department", "Description", "Target Date", "Status"]


def build_plant_workbook(plant_code, today) -> bytes:
    """One plant workbook in the standard layout: event sheets + one Action Plans sheet."""
    d = lambda n: (today + timedelta(days=n)).strftime("%d-%m-%Y")  # noqa: E731
    p = plant_code
    sheets = {
        "Change Control": [EVENT, [f"{p}/CC/26/001", "Production", "Introduction of new product XYZ 10 mg tablets", d(60), "Open"],
                           [f"{p}/CC/26/002", "Engineering", "Replacement of FBD filter bags", d(20), "Open"],
                           [f"{p}/CC/26/003", "QA", "Revision of SOP for line clearance", d(-5), "Open"]],
        "Deviation": [EVENT, [f"{p}/DEV/26/014", "Production", "Temperature excursion in granulation area", d(5), "Open"]],
        "Incident": [EVENT],
        "CAPA": [EVENT, [f"{p}/CAPA/26/031", "QA", "Revise cleaning validation protocol", d(45), "Open"],
                 [f"{p}/CAPA/26/032", "QC", "HPLC column usage log", d(10), "Closed"]],
        "OOS": [EVENT, [f"{p}/OOS/26/004", "QC", "Assay OOS for batch B2611", d(3), "Open"]],
        "OOT": [EVENT],
        "Action Plans": [ACTION,
                         [f"{p}/CC/26/001-A1", f"{p}/CC/26/001", "QC", "Prepare specification and STP", d(0), "Open"],
                         [f"{p}/CC/26/001-A2", f"{p}/CC/26/001", "Stores", "Create material codes for packing materials", d(4), "Open"],
                         [f"{p}/CC/26/001-A3", f"{p}/CC/26/001", "QA", "Approve artwork", d(30), "Open"],
                         [f"{p}/CC/26/001-A4", f"{p}/CC/26/001", "Production", "Prepare BMR/BPR", d(15), "Open"],
                         [f"{p}/DEV/26/014-A1", f"{p}/DEV/26/014", "Engineering", "Calibrate area temperature sensors", d(2), "Open"],
                         [f"{p}/CAPA/26/031-A1", f"{p}/CAPA/26/031", "QA", "Train staff on revised protocol", d(-2), "Open"]],
    }
    wb = Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(name)
        for row in rows:
            ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
