"""Excel workbook reading and value normalization.

Workbooks are parsed from in-memory bytes and never persisted by this module.
"""

import io
import re
import zipfile
from datetime import date, datetime

from django.conf import settings
from openpyxl import load_workbook
from openpyxl.utils.datetime import from_excel
from openpyxl.utils.exceptions import InvalidFileException

DEFAULT_DATE_FORMATS = ["%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y", "%Y-%m-%d", "%d-%b-%Y", "%d-%b-%y", "%d %b %Y", "%d/%m/%y", "%m/%d/%Y"]


class WorkbookError(Exception):
    """User-safe error describing why a workbook could not be read."""


def normalize_header(value):
    return re.sub(r"\s+", " ", str(value or "")).strip().lower()


def open_workbook(content: bytes):
    if not content:
        raise WorkbookError("The file is empty.")
    if len(content) > settings.MAX_WORKBOOK_BYTES:
        raise WorkbookError(f"The file exceeds the maximum size of {settings.MAX_WORKBOOK_BYTES // (1024 * 1024)} MB.")
    if not zipfile.is_zipfile(io.BytesIO(content)):
        raise WorkbookError("The file is not a valid .xlsx/.xlsm workbook (legacy .xls is not supported).")
    try:
        return load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except (InvalidFileException, zipfile.BadZipFile, KeyError, OSError, ValueError):
        raise WorkbookError("The workbook could not be opened. It may be corrupt, password-protected or in an unsupported format.")


def list_sheets(content: bytes):
    wb = open_workbook(content)
    try:
        return list(wb.sheetnames)
    finally:
        wb.close()


def read_sheet(content: bytes, worksheet: str, header_row: int = 1, max_rows=None):
    """Return (headers, rows) where rows is a list of (excel_row_number, {header: value})."""
    max_rows = max_rows or settings.MAX_WORKBOOK_ROWS
    wb = open_workbook(content)
    try:
        if worksheet not in wb.sheetnames:
            raise WorkbookError(f"Worksheet '{worksheet}' was not found. Available: {', '.join(wb.sheetnames)}")
        ws = wb[worksheet]
        headers = None
        rows = []
        for idx, values in enumerate(ws.iter_rows(values_only=True), start=1):
            if idx < header_row:
                continue
            if idx == header_row:
                headers = [str(v).strip() if v is not None else "" for v in values]
                while headers and not headers[-1]:
                    headers.pop()
                seen = {}
                for h in headers:
                    if h:
                        key = normalize_header(h)
                        if key in seen:
                            raise WorkbookError(f"Duplicate column header '{h}' in row {header_row}.")
                        seen[key] = h
                if not any(headers):
                    raise WorkbookError(f"Row {header_row} has no column headers.")
                continue
            if all(v is None or (isinstance(v, str) and not v.strip()) for v in values):
                continue
            if len(rows) >= max_rows:
                raise WorkbookError(f"The worksheet has more than {max_rows} data rows.")
            rows.append((idx, {h: (values[i] if i < len(values) else None) for i, h in enumerate(headers) if h}))
        if headers is None:
            raise WorkbookError(f"Header row {header_row} is beyond the end of the worksheet.")
        return headers, rows
    finally:
        wb.close()


def to_text(value):
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, datetime):
        return value.date().isoformat() if value.time() == datetime.min.time() else value.isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    return re.sub(r"\s+", " ", str(value)).strip()


def parse_date(value, formats=None):
    """Return (date | None, error | None). Empty values return (None, None)."""
    if value is None:
        return None, None
    if isinstance(value, datetime):
        return value.date(), None
    if isinstance(value, date):
        return value, None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if 1 <= value < 2958466:
            try:
                return from_excel(value).date(), None
            except (ValueError, OverflowError):
                pass
        return None, f"'{value}' is not a valid date"
    text = str(value).strip()
    if not text:
        return None, None
    for fmt in (formats or []) + DEFAULT_DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date(), None
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text).date(), None
    except ValueError:
        return None, f"'{text[:40]}' is not a recognised date"


def parse_number(value):
    if value is None or (isinstance(value, str) and not value.strip()):
        return None, None
    if isinstance(value, bool):
        return None, "not a number"
    if isinstance(value, (int, float)):
        return float(value), None
    try:
        return float(str(value).replace(",", "").strip()), None
    except ValueError:
        return None, f"'{str(value)[:40]}' is not a number"
