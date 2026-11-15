"""Backward compatible task scheduled/completed time migration tests."""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from postmaster.scheduler_engine import SchedulerEngine, SchedulerSettings
from postmaster.scheduled_email_v98919 import ScheduledEmailService
from postmaster.webgui_tasks import _calendar_event_time


class FakeMailClient:
    def __init__(self):
        from types import SimpleNamespace
        self.settings = SimpleNamespace(
            tracking_default=True, email_address="sender@example.test"
        )
        self.sent = []

    def _validate_recipients(self, recipients):
        return recipients

    def suppressed_recipients(self, recipients):
        return []

    def send_email(self, **kwargs):
        self.sent.append(kwargs)
        return {"ok": True, "sent": True}


class BackwardCompatibleDates(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = str(Path(self.temp.name) / "registry.sqlite")
        self.settings = SchedulerSettings(db_path=self.db, default_owner_id="tester")
        self.scheduler = SchedulerEngine(self.settings)

    def test_legacy_schema_migrated_idempotently_preserving_jobs(self):
        # Recreate the exact legacy column set while retaining primary job keys
        # and history records. No network or real mail delivery is involved.
        with sqlite3.connect(self.db) as conn:
            conn.execute("DROP TABLE jobs")
            conn.execute("""CREATE TABLE jobs (
                id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, project_id TEXT NOT NULL,
                title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
                action_type TEXT NOT NULL, execution_profile_id TEXT,
                payload_json TEXT NOT NULL DEFAULT '{}', schedule_type TEXT NOT NULL,
                schedule_value TEXT NOT NULL, timezone TEXT NOT NULL,
                approval_mode TEXT NOT NULL, status TEXT NOT NULL,
                next_run_utc TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                last_run_utc TEXT, last_error TEXT
            )""")
            conn.execute("""INSERT INTO jobs
                (id,owner_id,project_id,title,action_type,schedule_type,schedule_value,
                 timezone,approval_mode,status,created_at,updated_at,last_run_utc)
                VALUES ('legacy-done','tester','tinkerer','Legacy done','reminder',
                'once','2026-11-01T09:00:00+00:00','Europe/Rome','manual','completed',
                '2026-10-01T08:00:00+00:00','2026-11-08T15:00:00+00:00',
                '2026-11-01T09:07:00+00:00')""")
            conn.execute("""INSERT INTO job_history
                (job_id,occurred_at_utc,event_type,result_json)
                VALUES ('legacy-done','2026-11-01T09:08:00+00:00',
                        'completed_by_user','{}')""")
        migrated = SchedulerEngine(self.settings)
        row = migrated.get_job("legacy-done")
        self.assertEqual(row["status"], "completed")
        self.assertIsNone(row["next_run_utc"])
        self.assertEqual(row["scheduled_for_utc"], "2026-11-01T09:00:00+00:00")
        self.assertEqual(row["completed_at_utc"], "2026-11-01T09:08:00+00:00")
        self.assertEqual(row["last_run_utc"], "2026-11-01T09:07:00+00:00")
        self.assertEqual(row["updated_at"], "2026-11-08T15:00:00+00:00")
        self.assertEqual(
            SchedulerEngine(self.settings).get_job("legacy-done")["completed_at_utc"],
            "2026-11-01T09:08:00+00:00"
        )
        self.assertEqual(
            _calendar_event_time(row),
            datetime(2026, 11, 1, 9, tzinfo=timezone.utc)
        )

    def test_new_regular_task_stores_original_deadline(self):
        due = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()
        task = self.scheduler.create_job(
            owner_id="tester", project_id="tinkerer", title="Preserve due",
            description="", action_type="reminder", execution_profile_id=None,
            payload={}, schedule_type="once", schedule_value=due,
            timezone="Europe/Rome", approval_mode="manual",
        )
        self.assertEqual(task["scheduled_for_utc"], task["next_run_utc"])
        self.assertIsNone(task["completed_at_utc"])

    def test_autonomous_email_preserves_deadline_after_success(self):
        client = FakeMailClient()
        service = ScheduledEmailService(self.scheduler, lambda _: client)
        due = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
        job = service.schedule(
            account_id="sender", send_at=due, timezone_name="Europe/Rome",
            message={"to": ["receiver@example.test"], "subject": "Smoke",
                     "body": "Scheduled safely", "track_opens": True},
        )
        self.assertEqual(job["scheduled_for_utc"], job["next_run_utc"])
        self.assertIsNone(job["completed_at_utc"])
        with self.scheduler._connect() as conn:
            conn.execute("UPDATE jobs SET next_run_utc=? WHERE id=?",
                         ((datetime.now(timezone.utc)-timedelta(seconds=2)).isoformat(),
                          job["id"]))
        service.process_once()
        done = self.scheduler.get_job(job["id"])
        self.assertEqual(done["status"], "completed")
        self.assertIsNone(done["next_run_utc"])
        self.assertEqual(done["scheduled_for_utc"], due)
        self.assertIsNotNone(done["completed_at_utc"])
        self.assertNotEqual(done["completed_at_utc"], done["scheduled_for_utc"])
        self.assertEqual(len(client.sent), 1)

    def test_calendar_preserves_old_row_fallback(self):
        row = {"status": "completed", "last_run_utc": "2026-11-02T10:00:00+00:00",
               "updated_at": "2026-11-03T12:00:00+00:00"}
        self.assertEqual(
            _calendar_event_time(row),
            datetime(2026, 11, 2, 10, tzinfo=timezone.utc)
        )
        row["scheduled_for_utc"] = "2026-11-02T09:30:00+00:00"
        self.assertEqual(
            _calendar_event_time(row),
            datetime(2026, 11, 2, 9, 30, tzinfo=timezone.utc)
        )


if __name__ == "__main__":
    unittest.main()
