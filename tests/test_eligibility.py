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
