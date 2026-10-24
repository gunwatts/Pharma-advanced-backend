"""Date eligibility - the single source of truth for which dates may be emailed.

Eligible when: today <= target_date <= today + window_days.
Any target date before today is NEVER eligible for email, regardless of
status or window. Missing/invalid dates are never treated as eligible.
"""

from datetime import date, timedelta

REASON_DUE_TODAY = "due_today"
REASON_WITHIN_WINDOW = "within_window"
REASON_OVERDUE = "overdue"
REASON_BEYOND_WINDOW = "beyond_window"
REASON_MISSING_DATE = "missing_date"
REASON_INVALID_DATE = "invalid_date"

REASON_LABELS = {
    REASON_DUE_TODAY: "Due today",
    REASON_WITHIN_WINDOW: "Due within the reminder window",
    REASON_OVERDUE: "Target date has passed - overdue records are never emailed (dashboard follow-up only)",
    REASON_BEYOND_WINDOW: "Target date is beyond the reminder window",
    REASON_MISSING_DATE: "Target date is empty - flagged for admin review",
    REASON_INVALID_DATE: "Target date is invalid - flagged for admin review",
    "data_issue": "Record has data issues that need admin review",
    "status_excluded": "Status is excluded by the rule",
    "status_not_included": "Status is not in the rule's included statuses",
    "condition_excluded": "Excluded by a rule condition",
    "out_of_rule_scope": "Unit/department is not covered by the rule",
    "no_active_rule": "No active reminder rule for this category",
    "unrouted": "No active recipients configured for this unit/department",
}


def parse_iso(value):
    if not value:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def date_eligibility(target, today: date, window_days: int):
    """Return (eligible: bool, reason_code)."""
    if target is None:
        return False, REASON_MISSING_DATE
    if target < today:
        return False, REASON_OVERDUE
    if target == today:
        return True, REASON_DUE_TODAY
    if target <= today + timedelta(days=max(0, int(window_days))):
        return True, REASON_WITHIN_WINDOW
    return False, REASON_BEYOND_WINDOW
