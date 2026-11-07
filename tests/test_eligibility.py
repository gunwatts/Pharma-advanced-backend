from datetime import date, timedelta

from django.test import SimpleTestCase

from apps.reminders.services.eligibility import date_eligibility, parse_iso


class DateEligibilityTests(SimpleTestCase):
    today = date(2026, 10, 24)

    def check(self, offset, window=7):
        return date_eligibility(self.today + timedelta(days=offset), self.today, window)

    def test_yesterday_is_excluded(self):
        self.assertEqual(self.check(-1), (False, "overdue"))

    def test_long_overdue_excluded_even_with_large_window(self):
        self.assertEqual(self.check(-100, window=365), (False, "overdue"))

    def test_today_is_included(self):
        self.assertEqual(self.check(0), (True, "due_today"))

    def test_last_day_of_window_is_included(self):
        self.assertEqual(self.check(7), (True, "within_window"))

    def test_day_after_window_is_excluded(self):
        self.assertEqual(self.check(8), (False, "beyond_window"))

    def test_zero_window_only_today(self):
        self.assertEqual(self.check(0, window=0), (True, "due_today"))
        self.assertEqual(self.check(1, window=0), (False, "beyond_window"))

    def test_missing_date_is_never_eligible(self):
        self.assertEqual(date_eligibility(None, self.today, 7), (False, "missing_date"))

    def test_parse_iso(self):
        self.assertEqual(parse_iso("2026-10-24"), self.today)
        self.assertIsNone(parse_iso("not-a-date"))
        self.assertIsNone(parse_iso(None))


class ReminderDecisionTests(SimpleTestCase):
    """Two mail sections: daily 'action required' near the due date, periodic reminders before that."""

    today = date(2026, 11, 9)  # a Monday

    def decide(self, offset, **kw):
        from apps.reminders.services.eligibility import reminder_decision
        params = dict(window_days=7, repeat_every_days=15, anchor="due_date", start_days_before=90)
        params.update(kw)
        return reminder_decision(self.today + timedelta(days=offset), self.today, **params)

    def test_overdue_never_emailed(self):
        self.assertEqual(self.decide(-1), (None, "overdue"))

    def test_daily_action_window(self):
        self.assertEqual(self.decide(0), ("action_required", "due_today"))
        self.assertEqual(self.decide(7), ("action_required", "near_due"))

    def test_periodic_counted_back_from_target_date(self):
        self.assertEqual(self.decide(15), ("periodic", "periodic"))
        self.assertEqual(self.decide(30), ("periodic", "periodic"))
        self.assertEqual(self.decide(16), (None, "not_cycle_day"))

    def test_horizon_skips_far_items(self):
        self.assertEqual(self.decide(120), (None, "not_started"))
        self.assertEqual(self.decide(120, start_days_before=None), ("periodic", "periodic"))

    def test_no_periodic_when_cycle_disabled(self):
        self.assertEqual(self.decide(15, repeat_every_days=None), (None, "beyond_window"))

    def test_first_seen_anchor(self):
        first_seen = self.today - timedelta(days=10)
        self.assertEqual(self.decide(60, anchor="first_seen", repeat_every_days=10, first_seen=first_seen), ("periodic", "periodic"))
        self.assertEqual(self.decide(60, anchor="first_seen", repeat_every_days=10, first_seen=first_seen - timedelta(days=1)),
                         (None, "not_cycle_day"))

    def test_catch_up_covers_skipped_sunday(self):
        # Cycle day fell on Sunday (target 14 days after Monday = 15 days after Sunday): Monday's run with catch-up still reminds.
        self.assertEqual(self.decide(14), (None, "not_cycle_day"))
        self.assertEqual(self.decide(14, catch_up_days=2), ("periodic", "periodic"))
