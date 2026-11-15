"""Completed task calendar visibility and date-placement regressions."""
from __future__ import annotations

import unittest
from datetime import datetime, timezone
from urllib.parse import urlencode
from zoneinfo import ZoneInfo
from unittest.mock import Mock

from starlette.requests import Request

from postmaster.webgui_tasks import task_fragment, _calendar_event_time
from postmaster.webgui_calendar_v984 import week_calendar_html


def request(params):
    return Request({"type": "http", "method": "GET", "path": "/",
                    "query_string": urlencode(params).encode(),
                    "headers": [], "scheme": "http", "server": ("localhost", 80)})


class CalendarHistoryTests(unittest.TestCase):
    def setUp(self):
        self.completed = {
            "id": "finished-1", "owner_id": "owner", "project_id": "proj",
            "title": "Sent scheduled email", "action_type": "scheduled_email_send",
            "status": "completed", "next_run_utc": None,
            "last_run_utc": "2026-11-15T08:30:00+00:00",
            "updated_at": "2026-11-15T09:15:00+00:00",
        }
        self.pending = {
            "id": "pending-1", "owner_id": "owner", "project_id": "proj",
            "title": "Future task", "action_type": "reminder",
            "status": "scheduled", "next_run_utc": "2026-11-15T16:00:00+00:00",
            "last_run_utc": None, "updated_at": "2026-11-01T00:00:00+00:00",
        }
        self.base = Mock()
        self.base._safe_call.side_effect = lambda fn,*args,**kwargs: fn(*args,**kwargs)
        self.base.scheduler.return_value.list_jobs.return_value = [self.completed, self.pending]
        self.base.scheduler.return_value.list_due_jobs.return_value = []
        self.base.scheduler.return_value.list_projects.return_value = []
        self.base._csrf_value.return_value = "csrf"

    def test_completed_uses_last_execution_not_updated_at(self):
        date = _calendar_event_time(self.completed)
        self.assertEqual(date, datetime(2026, 11, 15, 8, 30, tzinfo=timezone.utc))
        self.assertEqual(_calendar_event_time(self.pending),
                         datetime(2026, 11, 15, 16, 0, tzinfo=timezone.utc))

    def test_calendar_includes_completed_by_default_with_faded_style(self):
        html = task_fragment(self.base, request({
            "task_view": "calendar", "calendar_month": "2026-11"
        }))
        self.assertIn("Sent scheduled email", html)
        self.assertIn("Future task", html)
        self.assertIn("task-calendar-completed", html)
        self.assertIn("✓ Sent scheduled email", html)
        self.assertIn("09:30", html)  # Rome offset in November
        self.assertIn("Completed", html)
        self.assertIn("opacity:.48", html)

    def test_calendar_can_hide_completed_without_hiding_pending(self):
        html = task_fragment(self.base, request({
            "task_view": "calendar", "calendar_month": "2026-11",
            "hide_completed": "1",
        }))
        self.assertNotIn("Sent scheduled email", html)
        self.assertIn("Future task", html)
        self.assertIn("Show completed", html)

    def test_agenda_default_does_not_change(self):
        html = task_fragment(self.base, request({"task_view": "agenda"}))
        self.assertNotIn("Sent scheduled email", html)
        self.assertIn("Future task", html)

    def test_week_calendar_shows_historical_completed_time(self):
        from postmaster import webgui_tasks
        html = week_calendar_html(webgui_tasks, request({
            "task_view": "calendar", "calendar_mode": "week",
            "calendar_week": "2026-11-09",
        }), [self.completed, self.pending], [], None, True)
        self.assertIn("Sent scheduled email", html)
        self.assertIn("✓ Sent scheduled email", html)
        self.assertIn("task-calendar-completed", html)
        self.assertIn("09:30", html)
        self.assertIn("Future task", html)

    def test_completed_no_execution_falls_back_to_updated(self):
        row = {**self.completed, "last_run_utc": None}
        self.assertEqual(_calendar_event_time(row),
                         datetime(2026, 11, 15, 9, 15, tzinfo=timezone.utc))


if __name__ == "__main__":
    unittest.main()
