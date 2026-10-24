"""Reminder rule evaluation. Conditions use a fixed operator set - never SQL/Python."""

from dataclasses import dataclass

from apps.catalog.models import normalize_label
from apps.reminders.services.eligibility import date_eligibility, parse_iso

OPERATORS = {
    "equals": "equals",
    "not_equals": "does not equal",
    "contains": "contains",
    "not_contains": "does not contain",
    "in": "is one of",
    "not_in": "is not one of",
    "is_empty": "is empty",
    "not_empty": "is not empty",
}
SYSTEM_FIELDS = {"status", "source_id", "title", "unit", "department"}


def record_value(record, field):
    if field == "unit":
        return record.unit.name
    if field == "department":
        return record.department.name
    if field in SYSTEM_FIELDS:
        return getattr(record, field)
    return (record.data or {}).get(field)


def condition_matches(value, op, expected):
    text = normalize_label(value) if value is not None else ""
    if op == "is_empty":
        return text == ""
    if op == "not_empty":
        return text != ""
    if op in ("in", "not_in"):
        options = expected if isinstance(expected, list) else str(expected or "").split(",")
        hit = text in {normalize_label(o) for o in options}
        return hit if op == "in" else not hit
    target = normalize_label(expected)
    if op == "equals":
        return text == target
    if op == "not_equals":
        return text != target
    if op == "contains":
        return target in text
    if op == "not_contains":
        return target not in text
    raise ValueError(f"Unsupported operator: {op}")


def validate_conditions(conditions):
    errors = []
    if not isinstance(conditions, list):
        return ["Conditions must be a list."]
    for i, cond in enumerate(conditions, start=1):
        if not isinstance(cond, dict) or not cond.get("field") or cond.get("op") not in OPERATORS:
            errors.append(f"Condition {i}: choose a field and a supported operator.")
    return errors


@dataclass
class Evaluation:
    record: object
    eligible: bool
    reason: str
    rule: object = None
    target_date: object = None
    detail: str = ""


class RuleSet:
    """Active rules grouped by category, with M2M scopes pre-loaded."""

    def __init__(self, rules):
        self.by_category = {}
        for rule in rules:
            rule._unit_ids = {u.pk for u in rule.units.all()}
            rule._dept_ids = {d.pk for d in rule.departments.all()}
            self.by_category.setdefault(rule.category_id, []).append(rule)

    @property
    def category_ids(self):
        return set(self.by_category)


def evaluate_rule(record, rule, today):
    if rule._unit_ids and record.unit_id not in rule._unit_ids:
        return Evaluation(record, False, "out_of_rule_scope", rule)
    if rule._dept_ids and record.department_id not in rule._dept_ids:
        return Evaluation(record, False, "out_of_rule_scope", rule)
    status = normalize_label(record.status)
    if rule.exclude_statuses and status in {normalize_label(s) for s in rule.exclude_statuses}:
        return Evaluation(record, False, "status_excluded", rule, detail=record.status)
    if rule.include_statuses and status not in {normalize_label(s) for s in rule.include_statuses}:
        return Evaluation(record, False, "status_not_included", rule, detail=record.status)
    for cond in rule.conditions or []:
        if condition_matches(record_value(record, cond.get("field")), cond.get("op"), cond.get("value")):
            return Evaluation(record, False, "condition_excluded", rule,
                              detail=f"{cond.get('field')} {OPERATORS.get(cond.get('op'), cond.get('op'))} {cond.get('value', '')}".strip())

    issues = record.issues or []
    if any(i.get("field") == rule.target_date_field and i.get("code") == "invalid_date" for i in issues):
        return Evaluation(record, False, "invalid_date", rule)
    blocking = [i for i in issues if i.get("code") in ("missing_required", "unknown_status") and i.get("field") != rule.target_date_field]
    target = parse_iso((record.data or {}).get(rule.target_date_field))
    if blocking:
        return Evaluation(record, False, "data_issue", rule, target, detail="; ".join(i.get("message", "") for i in blocking)[:300])
    eligible, reason = date_eligibility(target, today, rule.window_days)
    return Evaluation(record, eligible, reason, rule, target)


def evaluate_record(record, ruleset: RuleSet, today):
    rules = ruleset.by_category.get(record.category_id, [])
    if not rules:
        return Evaluation(record, False, "no_active_rule")
    evaluations = [evaluate_rule(record, rule, today) for rule in rules]
    for ev in evaluations:
        if ev.eligible:
            return ev
    return evaluations[0]
