"""Date eligibility - the single source of truth for which dates may be emailed.

Two mail sections:
* "Action required": today <= target_date <= today + window_days (every send day).
* "Periodic reminder": further away, on every N-th day of the cycle (counted back from the
  target date, or from when the item first appeared), optionally only within a horizon.
Any target date before today is NEVER eligible for email, regardless of status or rule.
Missing/invalid dates are never treated as eligible.
"""

from datetime import date, timedelta

SECTION_ACTION = "action_required"
SECTION_PERIODIC = "periodic"

REASON_DUE_TODAY = "due_today"
REASON_NEAR_DUE = "near_due"
REASON_PERIODIC = "periodic"
REASON_NOT_CYCLE_DAY = "not_cycle_day"
REASON_NOT_STARTED = "not_started"
REASON_WITHIN_WINDOW = "within_window"
REASON_OVERDUE = "overdue"
REASON_BEYOND_WINDOW = "beyond_window"
REASON_MISSING_DATE = "missing_date"
REASON_INVALID_DATE = "invalid_date"

REASON_LABELS = {
    REASON_DUE_TODAY: "Due today",
    REASON_WITHIN_WINDOW: "Due within the reminder window",
    REASON_NEAR_DUE: "Due soon - action required (daily reminder)",
    REASON_PERIODIC: "Periodic reminder day",
    REASON_NOT_CYCLE_DAY: "Not due soon and today is not a periodic reminder day",
    REASON_NOT_STARTED: "Target date is too far ahead (outside the reminder horizon)",
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


def reminder_decision(target, today: date, window_days: int, repeat_every_days=None, anchor="due_date",
                      start_days_before=None, first_seen: date = None, catch_up_days: int = 1):
    """Return (section | None, reason_code) for one record under one rule.

    ``catch_up_days`` is how many calendar days this run covers (e.g. 2 on Monday when Sunday
    has no run) so a periodic reminder whose cycle day fell on a non-send day is not lost.
    """
    if target is None:
        return None, REASON_MISSING_DATE
    days_left = (target - today).days
    if days_left < 0:
        return None, REASON_OVERDUE
    if days_left <= max(0, int(window_days)):
        return SECTION_ACTION, REASON_DUE_TODAY if days_left == 0 else REASON_NEAR_DUE
    if start_days_before is not None and days_left > start_days_before:
        return None, REASON_NOT_STARTED
    if not repeat_every_days:
        return None, REASON_BEYOND_WINDOW
    n = int(repeat_every_days)
    for back in range(max(1, int(catch_up_days))):
        day = today - timedelta(days=back)
        if anchor == "first_seen":
            if first_seen is not None and day >= first_seen and (day - first_seen).days % n == 0:
                return SECTION_PERIODIC, REASON_PERIODIC
        elif (target - day).days % n == 0:
            return SECTION_PERIODIC, REASON_PERIODIC
    return None, REASON_NOT_CYCLE_DAY
