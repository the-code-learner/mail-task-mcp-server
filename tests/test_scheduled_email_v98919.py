"""Autonomous one-time email sends; test with fake SMTP, never real network."""
from __future__ import annotations

import tempfile
import threading
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from postmaster.scheduler_engine import SchedulerEngine, SchedulerError, SchedulerSettings
from postmaster.scheduled_email_v98919 import ScheduledEmailService


class Client:
    def __init__(self):
        self.settings = SimpleNamespace(tracking_default=True, email_address="sender@example.org")
        self.sent = []
        self.blocked = []
        self.error = None

    def _validate_recipients(self, addresses):
        if not addresses:
            raise ValueError("empty")
        return list(addresses)

    def suppressed_recipients(self, addresses):
        return self.blocked

    def send_email(self, **message):
        self.sent.append(dict(message))
        if self.error:
            raise self.error
        return {"sent": True, "ok": True}


class ScheduledEmailTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        settings = SchedulerSettings(db_path=str(Path(self.tmp.name) / "scheduler.db"),
                                     default_owner_id="tester")
        self.registry = SchedulerEngine(settings)
        self.client = Client()
        self.service = ScheduledEmailService(self.registry, lambda key: self.client,
                                             poll_seconds=1)
        self.future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        self.message = {"to": ["example@example.org"], "subject": "Scheduled hello",
                        "body": "Exact approved content", "track_opens": True}

    def schedule(self, key="compose-idempotency"):
        return self.service.schedule(account_id="sender", send_at=self.future,
                                     message=self.message, idempotency_key=key)

    def due(self, job_id):
        with self.registry._connect() as conn:
            conn.execute("UPDATE jobs SET next_run_utc=? WHERE id=?",
                         ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(), job_id))

    def test_sender_identity_change_fails_safely(self):
        job = self.schedule()
        self.due(job["id"])
        self.client.settings.email_address = "different@example.org"
        self.service.process_once()
        self.assertEqual(self.registry.get_job(job["id"])["status"], "failed")
        self.assertFalse(self.client.sent)

    def test_composer_markup_has_scheduling_form_fields(self):
        from postmaster.webgui_mail_v986 import modern_compose_markup, EDITOR
        from postmaster.webgui_v960 import _compose_panel
        from unittest.mock import MagicMock
        base = MagicMock()
        base._csrf_value.return_value = "csrf-demo"
        html = modern_compose_markup(_compose_panel(base, [], None))
        self.assertIn('name="scheduled_send_at"', html)
        self.assertIn('name="compose_action" value="schedule"', html)
        self.assertIn('name="scheduled_timezone"', html)

    def test_scheduling_is_not_sending_and_job_is_visible(self):
        job = self.schedule()
        self.assertEqual(job["action_type"], "scheduled_email_send")
        self.assertEqual(job["schedule_type"], "once")
        self.assertEqual(job["approval_mode"], "automatic")
        self.assertEqual(job["status"], "scheduled")
        self.assertEqual(self.client.sent, [])
        self.assertIn(job["id"], [row["id"] for row in self.registry.list_jobs()])
        self.assertEqual(job["payload"]["message"]["body"], "Exact approved content")

    def test_due_sends_once_without_second_approval(self):
        job = self.schedule()
        self.due(job["id"])
        self.assertTrue(self.service.process_once())
        self.assertEqual(self.registry.get_job(job["id"])["status"], "completed")
        self.assertEqual(len(self.client.sent), 1)
        self.assertEqual(self.client.sent[0]["subject"], "Scheduled hello")
        self.assertFalse(self.service.process_once())
        self.assertEqual(len(self.client.sent), 1)

    def test_duplicate_compose_submit_returns_same_job(self):
        first = self.schedule()
        second = self.schedule()
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(len(self.registry.list_jobs()), 1)
        modified = dict(self.message, subject="Different")
        with self.assertRaises(SchedulerError):
            self.service.schedule(account_id="sender", send_at=self.future,
                                  message=modified, idempotency_key="compose-idempotency")

    def test_worker_race_claim_is_atomic(self):
        job = self.schedule()
        self.due(job["id"])
        worker2 = ScheduledEmailService(
            SchedulerEngine(self.registry.settings), lambda key: self.client
        )
        threads = [threading.Thread(target=lambda service=s: service.process_once())
                   for s in (self.service, worker2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(self.client.sent), 1)
        self.assertEqual(self.registry.get_job(job["id"])["status"], "completed")

    def test_crash_after_claim_does_not_replay(self):
        job = self.schedule()
        self.due(job["id"])
        claimed = self.service._claim()
        self.assertEqual(claimed["id"], job["id"])
        restarted = ScheduledEmailService(SchedulerEngine(self.registry.settings),
                                          lambda key: self.client)
        self.assertFalse(restarted.process_once())
        self.assertEqual(self.registry.get_job(job["id"])["status"], "sending")
        self.assertEqual(len(self.client.sent), 0)
        self.assertEqual(restarted.status()["counts"]["sending"], 1)

    def test_smtp_exception_is_terminal_without_retries(self):
        job = self.schedule()
        self.due(job["id"])
        self.client.error = TimeoutError("SMTP unknown")
        self.service.process_once()
        self.assertEqual(self.registry.get_job(job["id"])["status"], "failed")
        self.assertIn("uncertain", self.registry.get_job(job["id"])["last_error"])
        self.assertFalse(self.service.process_once())

    def test_pause_resume_and_cancel_only_before_claim(self):
        job = self.schedule()
        self.registry.pause_job(job["id"])
        self.assertFalse(self.service.process_once())
        self.registry.resume_job(job["id"])
        self.service.cancel(job["id"])
        self.assertEqual(self.registry.get_job(job["id"])["status"], "cancelled")
        with self.assertRaises(SchedulerError):
            self.registry.resume_job(job["id"])

    def test_generic_completion_and_editing_rejected(self):
        job = self.schedule()
        with self.assertRaises(SchedulerError):
            self.registry.update_job(job_id=job["id"], title="Changed")
        self.due(job["id"])
        with self.assertRaises(SchedulerError):
            self.registry.complete_job(job["id"])

    def test_invalid_and_suppressed_recipients_never_scheduled(self):
        self.client.blocked = [{"recipient": "example@example.org"}]
        with self.assertRaises(SchedulerError):
            self.schedule()
        self.assertEqual(self.registry.list_jobs(), [])
        self.client.blocked = []
        self.message["to"] = ["bad address"]
        with self.assertRaises(SchedulerError):
            self.schedule()

    def test_tracking_off_and_attachments_never_bypass_approval(self):
        self.message["track_opens"] = False
        with self.assertRaises(SchedulerError):
            self.schedule()
        self.message["track_opens"] = True
        self.message["attachments"] = [{"file_id": "mutable"}]
        with self.assertRaises(SchedulerError):
            self.schedule()

    def test_other_legacy_job_types_remain_passive(self):
        job = self.schedule()
        with self.registry._connect() as conn:
            conn.execute("UPDATE jobs SET action_type='email' WHERE id=?", (job["id"],))
        self.due(job["id"])
        self.assertFalse(self.service.process_once())
        self.assertFalse(self.client.sent)

    def test_datetime_requires_future_and_disambiguates_dst(self):
        with self.assertRaises(SchedulerError):
            self.service.schedule(account_id="sender", send_at="2020-01-01T00:00",
                                  message=self.message)
        with self.assertRaises(SchedulerError):
            self.service.schedule(account_id="sender", send_at="2027-10-31T02:30",
                                  message=self.message, timezone_name="Europe/Rome")
        with self.assertRaises(SchedulerError):
            self.service.schedule(account_id="sender", send_at="2027-03-28T02:30",
                                  message=self.message, timezone_name="Europe/Rome")


if __name__ == "__main__":
    unittest.main()
