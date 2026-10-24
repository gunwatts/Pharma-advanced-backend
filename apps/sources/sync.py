"""Source synchronization.

Safety rules implemented here:
* A failed fetch or structurally invalid worksheet changes nothing.
* If more rows are rejected than the mapping allows, the sync is "incomplete"
  and nothing is changed.
* Closed records and records missing from the source are removed only after a
  successful, validated sync. Rows whose ID could not be read block the
  "missing from source" cleanup, because they might be existing records.
* Sync is idempotent: records are keyed by (category, source ID).
"""

import logging
import re
from dataclasses import dataclass, field

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.utils import timezone

from apps.catalog.models import Category, Department, FieldDefinition, Relationship, StatusDefinition, Unit, normalize_label
from apps.records.models import Record
from apps.sources import connectors
from apps.sources.excel import WorkbookError, normalize_header, parse_date, parse_number, read_sheet, to_text
from apps.sources.models import SourceMapping, SyncRun

logger = logging.getLogger("qa.sync")

MAX_ERRORS_STORED = 200
# Issue codes that make a record unsafe to treat as "nothing pending".
BLOCKING_ISSUES = {"missing_required", "unknown_status"}


@dataclass
class ParsedRow:
    row_number: int
    source_id: str
    unit: Unit
    department: Department
    status: str
    is_closed: bool
    title: str
    data: dict
    issues: list
    parent_ref: str


@dataclass
class ParseResult:
    headers: list = field(default_factory=list)
    structure_errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    rows: list = field(default_factory=list)  # ParsedRow (open + closed)
    rejected: list = field(default_factory=list)  # {row, source_id, message}
    unmapped_columns: list = field(default_factory=list)
    rows_read: int = 0

    @property
    def rejected_ids(self):
        return {r["source_id"] for r in self.rejected if r.get("source_id")}

    @property
    def rejected_without_id(self):
        return sum(1 for r in self.rejected if not r.get("source_id"))

    @property
    def flagged(self):
        return [r for r in self.rows if r.issues and not r.is_closed]

    def errors_for_storage(self):
        items = [{"type": "structure", "message": m} for m in self.structure_errors]
        items += [{"type": "rejected", **r} for r in self.rejected]
        for row in self.flagged:
            for issue in row.issues:
                items.append({"type": "issue", "row": row.row_number, "source_id": row.source_id, **issue})
        return items[:MAX_ERRORS_STORED]


class Lookups:
    """Pre-loaded label -> object maps so matching does not hit the DB per row."""

    def __init__(self, category: Category):
        self.units = {}
        for unit in Unit.objects.filter(active=True):
            for label in unit.labels():
                self.units.setdefault(label, unit)
        self.departments = {}
        for dept in Department.objects.filter(active=True):
            for label in dept.labels():
                self.departments.setdefault(label, dept)
        self.statuses = {}
        for status in StatusDefinition.objects.filter(category=category):
            for label in status.labels():
                self.statuses.setdefault(label, status)
        self.fields = list(FieldDefinition.objects.filter(category=category, active=True))
        rel = Relationship.objects.filter(child_category=category, active=True).first()
        self.link_field = rel.link_field if rel else None


def _column_lookup(headers):
    return {normalize_header(h): h for h in headers if h}


def _validate_value(fd: FieldDefinition, raw, date_formats):
    """Return (stored_value, issue | None)."""
    if fd.field_type == FieldDefinition.TYPE_DATE:
        value, error = parse_date(raw, date_formats)
        if error:
            return None, {"field": fd.key, "code": "invalid_date", "message": f"{fd.label}: {error}"}
        return (value.isoformat() if value else None), None
    if fd.field_type == FieldDefinition.TYPE_NUMBER:
        value, error = parse_number(raw)
        if error:
            return None, {"field": fd.key, "code": "invalid_number", "message": f"{fd.label}: {error}"}
        return value, None

    text = to_text(raw)
    if not text:
        return None, None
    if fd.max_length and len(text) > fd.max_length:
        return text[: fd.max_length], {"field": fd.key, "code": "too_long", "message": f"{fd.label} exceeds {fd.max_length} characters"}
    if fd.field_type == FieldDefinition.TYPE_EMAIL:
        try:
            validate_email(text)
        except ValidationError:
            return text, {"field": fd.key, "code": "invalid_email", "message": f"{fd.label}: invalid email address"}
    if fd.field_type == FieldDefinition.TYPE_CHOICE and fd.choices:
        allowed = {normalize_label(c): c for c in fd.choices}
        if normalize_label(text) not in allowed:
            return text, {"field": fd.key, "code": "invalid_choice", "message": f"{fd.label}: '{text[:40]}' is not an allowed value"}
        text = allowed[normalize_label(text)]
    if fd.pattern:
        try:
            if not re.fullmatch(fd.pattern, text):
                return text, {"field": fd.key, "code": "pattern_mismatch", "message": f"{fd.label}: value does not match the expected format"}
        except re.error:
            pass
    return text, None


def parse_rows(mapping: SourceMapping, headers, rows, lookups: Lookups = None) -> ParseResult:
    """Validate and normalize worksheet rows according to a mapping. Pure: no DB writes."""
    lookups = lookups or Lookups(mapping.category)
    result = ParseResult(headers=[h for h in headers if h], rows_read=len(rows))
    cols = _column_lookup(headers)

    def resolve(column_label, purpose, required=True):
        if not column_label:
            if required:
                result.structure_errors.append(f"No column is mapped for {purpose}.")
            return None
        header = cols.get(normalize_header(column_label))
        if header is None:
            if required:
                result.structure_errors.append(f"Column '{column_label}' ({purpose}) was not found in the worksheet.")
            else:
                result.warnings.append(f"Optional column '{column_label}' ({purpose}) was not found; values will be empty.")
        return header

    id_col = resolve(mapping.id_column, "record ID")
    unit_col = None if mapping.fixed_unit_id else resolve(mapping.unit_column, "unit")
    dept_col = resolve(mapping.department_column, "department")
    status_col = resolve(mapping.status_column, "status", required=False) if mapping.status_column else None

    field_cols = {}
    column_map = mapping.column_map or {}
    known_keys = {fd.key for fd in lookups.fields}
    for key in column_map:
        if key not in known_keys:
            result.warnings.append(f"Mapping refers to unknown or inactive field '{key}'.")
    for fd in lookups.fields:
        label = column_map.get(fd.key)
        if not label:
            if fd.required:
                result.structure_errors.append(f"Required field '{fd.label}' is not mapped to a column.")
            continue
        field_cols[fd.key] = resolve(label, fd.label, required=fd.required)

    used = {c for c in [id_col, unit_col, dept_col, status_col, *field_cols.values()] if c}
    result.unmapped_columns = [h for h in result.headers if h not in used]
    if result.structure_errors:
        return result

    date_formats = mapping.date_formats or []
    has_status_defs = bool(lookups.statuses)
    seen = {}
    duplicates = set()
    title_field = mapping.category.title_field

    for row_number, values in rows:
        source_id = to_text(values.get(id_col))[:128]
        if not source_id:
            result.rejected.append({"row": row_number, "source_id": "", "message": "Record ID is empty."})
            continue
        if source_id in seen:
            duplicates.add(source_id)
            continue
        seen[source_id] = row_number

        if mapping.fixed_unit_id:
            unit = mapping.fixed_unit
        else:
            unit_text = to_text(values.get(unit_col))
            unit = lookups.units.get(normalize_label(unit_text))
            if unit is None:
                result.rejected.append({"row": row_number, "source_id": source_id,
                                        "message": f"Unit '{unit_text[:60]}' is not configured." if unit_text else "Unit is empty."})
                continue
        dept_text = to_text(values.get(dept_col))
        department = lookups.departments.get(normalize_label(dept_text))
        if department is None:
            result.rejected.append({"row": row_number, "source_id": source_id,
                                    "message": f"Department '{dept_text[:60]}' is not configured." if dept_text else "Department is empty."})
            continue

        issues = []
        status_text = to_text(values.get(status_col)) if status_col else ""
        is_closed = False
        status_value = status_text[:64]
        if status_col and has_status_defs:
            status_def = lookups.statuses.get(normalize_label(status_text))
            if status_def is None:
                issues.append({"field": "status", "code": "unknown_status",
                               "message": f"Status '{status_text[:40]}' is not configured for this category." if status_text else "Status is empty."})
            else:
                status_value = status_def.value
                is_closed = status_def.is_closed

        data = {}
        for fd in lookups.fields:
            col = field_cols.get(fd.key)
            raw = values.get(col) if col else None
            value, issue = _validate_value(fd, raw, date_formats)
            if issue:
                issues.append(issue)
            if value is None and fd.required and not (issue and issue["field"] == fd.key):
                issues.append({"field": fd.key, "code": "missing_required", "message": f"{fd.label} is required but empty."})
            data[fd.key] = value

        parent_ref = to_text(data.get(lookups.link_field))[:128] if lookups.link_field else ""
        title = to_text(data.get(title_field))[:500] if title_field else ""
        result.rows.append(ParsedRow(row_number, source_id, unit, department, status_value, is_closed, title, data, issues, parent_ref))

    if duplicates:
        result.rows = [r for r in result.rows if r.source_id not in duplicates]
        for dup in sorted(duplicates):
            result.rejected.append({"row": seen[dup], "source_id": dup,
                                    "message": "Duplicate record ID in the worksheet; all rows with this ID were rejected."})
    return result


def validate_mapping(mapping: SourceMapping, content: bytes = None, sample_size=10):
    """Dry validation used by the setup wizard. Never writes records."""
    try:
        content = content if content is not None else connectors.fetch_workbook(mapping.data_source)
        headers, rows = read_sheet(content, mapping.worksheet, mapping.header_row)
    except (connectors.SourceError, WorkbookError) as exc:
        return {"ok": False, "structure_errors": [str(exc)], "warnings": [], "headers": [], "rows_read": 0}
    result = parse_rows(mapping, headers, rows)
    open_rows = [r for r in result.rows if not r.is_closed]
    issue_counts = {}
    for row in result.flagged:
        for issue in row.issues:
            issue_counts[issue["code"]] = issue_counts.get(issue["code"], 0) + 1
    rejected_ok = len(result.rejected) <= mapping.max_rejected_rows
    return {
        "ok": not result.structure_errors and rejected_ok,
        "headers": result.headers,
        "structure_errors": result.structure_errors,
        "warnings": result.warnings,
        "unmapped_columns": result.unmapped_columns,
        "rows_read": result.rows_read,
        "rows_valid": len(result.rows),
        "rows_open": len(open_rows),
        "rows_closed": len(result.rows) - len(open_rows),
        "rows_rejected": len(result.rejected),
        "rows_flagged": len(result.flagged),
        "issue_counts": issue_counts,
        "rejected": result.rejected[:50],
        "flagged": [{"row": r.row_number, "source_id": r.source_id, "issues": r.issues} for r in result.flagged[:50]],
        "sample": [
            {"row": r.row_number, "source_id": r.source_id, "unit": r.unit.name, "department": r.department.name,
             "status": r.status, "closed": r.is_closed, "data": r.data}
            for r in result.rows[:sample_size]
        ],
        "rejected_threshold": mapping.max_rejected_rows,
    }


def _finish(run: SyncRun, mapping: SourceMapping, status, message="", result: ParseResult = None):
    now = timezone.now()
    run.status = status
    run.finished_at = now
    run.message = message[:500]
    if result is not None:
        run.rows_read = result.rows_read
        run.rows_rejected = len(result.rejected)
        run.rows_flagged = len(result.flagged)
        run.errors = result.errors_for_storage()
    run.save()
    mapping.last_attempt_at = now
    mapping.last_status = status
    mapping.last_rows_read = run.rows_read
    mapping.last_rows_rejected = run.rows_rejected
    if status == SyncRun.STATUS_SUCCESS:
        mapping.last_success_at = now
        mapping.last_rows_imported = run.rows_imported
        mapping.last_error = ""
    else:
        mapping.last_error = message[:500]
    mapping.save(update_fields=["last_attempt_at", "last_status", "last_rows_read", "last_rows_rejected",
                                "last_success_at", "last_rows_imported", "last_error"])
    log = logger.info if status == SyncRun.STATUS_SUCCESS else logger.warning
    log("Sync finished", extra={"mapping_id": mapping.pk, "status": status, "rows_read": run.rows_read,
                                "rows_rejected": run.rows_rejected, "sync_message": message[:200]})
    return run


def sync_mapping(mapping: SourceMapping, daily_run=None, triggered_by="", content: bytes = None) -> SyncRun:
    run = SyncRun(mapping=mapping, daily_run=daily_run, triggered_by=triggered_by[:150], status=SyncRun.STATUS_FAILED)
    run.save()
    try:
        if content is None:
            content = connectors.fetch_workbook(mapping.data_source)
        headers, rows = read_sheet(content, mapping.worksheet, mapping.header_row)
    except (connectors.SourceError, WorkbookError) as exc:
        return _finish(run, mapping, SyncRun.STATUS_FAILED, str(exc))
    except Exception:
        logger.exception("Unexpected error reading source", extra={"mapping_id": mapping.pk})
        return _finish(run, mapping, SyncRun.STATUS_FAILED, "Unexpected error while reading the workbook.")
    finally:
        content = None  # drop the workbook bytes as soon as possible

    result = parse_rows(mapping, headers, rows)
    if result.structure_errors:
        return _finish(run, mapping, SyncRun.STATUS_FAILED, "Worksheet structure is invalid: " + "; ".join(result.structure_errors), result)

    # IDs already owned by another source mapping of the same category are conflicts.
    ids = [r.source_id for r in result.rows]
    foreign = set(
        Record.objects.filter(category=mapping.category, source_id__in=ids).exclude(mapping=mapping).values_list("source_id", flat=True)
    ) if ids else set()
    if foreign:
        keep = []
        for row in result.rows:
            if row.source_id in foreign:
                result.rejected.append({"row": row.row_number, "source_id": row.source_id,
                                        "message": "This record ID is already provided by another source for the same category."})
            else:
                keep.append(row)
        result.rows = keep

    if len(result.rejected) > mapping.max_rejected_rows:
        return _finish(run, mapping, SyncRun.STATUS_INCOMPLETE,
                       f"{len(result.rejected)} rows were rejected (allowed: {mapping.max_rejected_rows}). No records were changed.", result)

    try:
        _apply(mapping, result, run)
    except Exception:
        logger.exception("Failed to apply sync", extra={"mapping_id": mapping.pk})
        return _finish(run, mapping, SyncRun.STATUS_FAILED, "Records could not be saved; existing records were left unchanged.", result)
    message = f"Imported {run.rows_imported} open records; removed {run.records_removed_closed} closed and {run.records_removed_missing} no longer in source."
    if result.rejected_without_id:
        message += " Missing-record cleanup skipped because some rows had no ID."
    return _finish(run, mapping, SyncRun.STATUS_SUCCESS, message, result)


def _apply(mapping: SourceMapping, result: ParseResult, run: SyncRun):
    now = timezone.now()
    open_rows = {r.source_id: r for r in result.rows if not r.is_closed}
    closed_ids = {r.source_id for r in result.rows if r.is_closed}
    protected_ids = result.rejected_ids  # rejected rows must not remove/overwrite existing records

    with transaction.atomic():
        existing = {rec.source_id: rec for rec in Record.objects.filter(mapping=mapping)}
        to_create, to_update = [], []
        for source_id, row in open_rows.items():
            rec = existing.get(source_id)
            values = dict(unit=row.unit, department=row.department, status=row.status, title=row.title,
                          data=row.data, issues=row.issues, parent_ref=row.parent_ref, last_synced_at=now)
            if rec is None:
                to_create.append(Record(category=mapping.category, mapping=mapping, source_id=source_id, **values))
            else:
                for key, value in values.items():
                    setattr(rec, key, value)
                to_update.append(rec)
        Record.objects.bulk_create(to_create, batch_size=500)
        if to_update:
            Record.objects.bulk_update(to_update, ["unit", "department", "status", "title", "data", "issues", "parent_ref", "last_synced_at"], batch_size=500)

        removed_closed = Record.objects.filter(mapping=mapping, source_id__in=closed_ids - protected_ids).delete()[0] if closed_ids else 0
        removed_missing = 0
        if not result.rejected_without_id:
            seen = set(open_rows) | closed_ids | protected_ids
            missing = [sid for sid in existing if sid not in seen]
            if missing:
                removed_missing = Record.objects.filter(mapping=mapping, source_id__in=missing).delete()[0]

    run.rows_imported = len(open_rows)
    run.rows_closed = len(closed_ids)
    run.records_created = len(to_create)
    run.records_updated = len(to_update)
    run.records_removed_closed = removed_closed
    run.records_removed_missing = removed_missing
    relink_parents()


def relink_parents():
    """Resolve parent links via explicit relationships (never by fuzzy text matching)."""
    for rel in Relationship.objects.filter(active=True):
        parents = dict(Record.objects.filter(category=rel.parent_category).values_list("source_id", "id"))
        children = list(Record.objects.filter(category=rel.child_category).only("id", "parent_ref", "parent_id", "issues"))
        changed = []
        for child in children:
            parent_id = parents.get(child.parent_ref) if child.parent_ref else None
            issues = [i for i in child.issues if i.get("code") != "parent_not_found"]
            if child.parent_ref and parent_id is None:
                issues.append({"field": rel.link_field, "code": "parent_not_found",
                               "message": f"Parent record '{child.parent_ref}' was not found among current records."})
            if child.parent_id != parent_id or issues != child.issues:
                child.parent_id = parent_id
                child.issues = issues
                changed.append(child)
        if changed:
            Record.objects.bulk_update(changed, ["parent", "issues"], batch_size=500)


def sync_all(daily_run=None, triggered_by="", categories=None):
    """Sync every active mapping. Returns {mapping_id: SyncRun}."""
    qs = SourceMapping.objects.filter(active=True, data_source__active=True, category__active=True).select_related(
        "data_source", "category", "fixed_unit")
    if categories is not None:
        qs = qs.filter(category__in=categories)
    results = {}
    content_cache = {}
    for mapping in qs:
        source_id = mapping.data_source_id
        content = content_cache.get(source_id)
        if content is None and source_id not in content_cache:
            try:
                content = connectors.fetch_workbook(mapping.data_source)
            except connectors.SourceError:
                content = None
            content_cache[source_id] = content
        # When the fetch failed, pass None so sync_mapping retries and records the error itself.
        results[mapping.pk] = sync_mapping(mapping, daily_run=daily_run, triggered_by=triggered_by, content=content)
    content_cache.clear()
    return results
