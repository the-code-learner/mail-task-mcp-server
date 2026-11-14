"""Exactly one autonomous task type: an explicitly authorized one-time email send.

This is deliberately at-most-once after the SMTP handoff starts: SMTP cannot
guarantee exactly-once delivery if the process dies after transmission. A
crash/interruption is marked uncertain; it is never automatically retried.
Other legacy scheduler tasks remain passive.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo

from .scheduler_engine import SchedulerEngine, SchedulerError

log = logging.getLogger(__name__)
ACTION = "scheduled_email_send"
_TERMINAL = {"completed", "failed", "cancelled", "sending"}
_ADDR = re.compile(r"^[^@\s,<>]+@[^@\s,<>]+\.[^@\s,<>]+$")
_ALLOWED = {"to", "cc", "bcc", "subject", "body", "body_html", "body_amp",
            "track_opens", "campaign_id", "newsletter_mode", "unsubscribe_url",
            "unsubscribe_email", "one_click_unsubscribe", "automatic_unsubscribe",
            "dsn_notify_success", "attachments"}


def _when(value: str, tz_name: str) -> datetime:
    try:
        tz = ZoneInfo(tz_name)
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            # Reject nonexistent/ambiguous wall clock times; the user should
            # provide an explicit UTC offset for DST transitions.
            aware = parsed.replace(tzinfo=tz)
            if aware.astimezone(timezone.utc).astimezone(tz).replace(tzinfo=None) != parsed:
                raise SchedulerError("Nonexistent local time: specify an explicit timezone offset")
            if parsed.replace(tzinfo=tz, fold=0).utcoffset() != parsed.replace(tzinfo=tz, fold=1).utcoffset():
                raise SchedulerError("Ambiguous local time: specify an explicit timezone offset")
            parsed = aware
        utc = parsed.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError) as exc:
        raise SchedulerError("Invalid scheduled date/time or timezone") from exc
    if utc <= datetime.now(timezone.utc):
        raise SchedulerError("Scheduled email time must be in the future")
    return utc


def _validate_message(message: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(message, dict):
        raise SchedulerError("Email message must be an object")
    if set(message) - _ALLOWED:
        raise SchedulerError("Unsupported scheduled email fields: " + ", ".join(sorted(set(message) - _ALLOWED)))
    frozen = {}
    for field in ("to", "cc", "bcc"):
        values = message.get(field) or []
        if isinstance(values, str) or not isinstance(values, list):
            raise SchedulerError(field + " must be a list of email addresses")
        cleaned = []
        for value in values:
            address = str(value).strip()
            if not _ADDR.fullmatch(address):
                raise SchedulerError("Invalid recipient in " + field)
            if address.lower() not in {v.lower() for v in cleaned}:
                cleaned.append(address)
        frozen[field] = cleaned
    if not frozen["to"]:
        raise SchedulerError("Scheduled email requires at least one To recipient")
    frozen["subject"] = str(message.get("subject") or "").strip()
    frozen["body"] = str(message.get("body") or "")
    frozen["body_html"] = str(message.get("body_html") or "") or None
    frozen["body_amp"] = str(message.get("body_amp") or "") or None
    if not frozen["subject"] or not any([frozen["body"], frozen["body_html"], frozen["body_amp"]]):
        raise SchedulerError("A scheduled email requires subject and body")
    # A stored-file identifier is not an immutable attachment snapshot.
    # Reject instead of silently sending changed/deleted attachments later.
    if message.get("attachments"):
        raise SchedulerError("Scheduled email attachments require immutable snapshots; use a stored-file link in the body")
    frozen["attachments"] = None
    frozen["track_opens"] = message.get("track_opens")
    if frozen["track_opens"] not in (None, True, False):
        raise SchedulerError("track_opens must be true, false or omitted")
    for field in ("campaign_id", "unsubscribe_url", "unsubscribe_email"):
        frozen[field] = str(message.get(field) or "") or None
    for field, default in (("newsletter_mode", False), ("one_click_unsubscribe", False),
                           ("automatic_unsubscribe", True), ("dsn_notify_success", False)):
        frozen[field] = bool(message.get(field, default))
    return frozen


class ScheduledEmailService:
    def __init__(self, scheduler: SchedulerEngine, mail_client: Callable[[str], Any], *,
                 poll_seconds: float = 5.0):
        self.scheduler = scheduler
        self.mail_client = mail_client
        self.poll_seconds = max(1.0, float(poll_seconds))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def schedule(self, *, account_id: str, send_at: str, message: dict[str, Any],
                 timezone_name: str = "Europe/Rome", owner_id: str = "",
                 project_id: str = "", idempotency_key: str | None = None,
                 tracking_off_authorized: bool = False) -> dict[str, Any]:
        """Explicit invocation authorizes one exact message, with no later prompt."""
        aid = str(account_id or "").strip()
        if not aid:
            raise SchedulerError("An explicit sender account is required")
        due = _when(send_at, timezone_name)
        frozen = _validate_message(message)
        client = self.mail_client(aid)
        # Validate recipient and sender policies NOW. The ordinary client still
        # rechecks policy at send time; no background authorization bypass.
        recipients = list(dict.fromkeys(frozen["to"] + frozen["cc"] + frozen["bcc"]))
        client._validate_recipients(recipients)
        if client.suppressed_recipients(recipients):
            raise SchedulerError("Suppressed recipients cannot be autonomously scheduled")
        if not tracking_off_authorized and (
            frozen["track_opens"] is False or (
                frozen["track_opens"] is None
                and not bool(getattr(client.settings, "tracking_default", False))
            )
        ):
            raise SchedulerError("Tracking OFF requires fresh explicit chat approval before scheduling")
        key = str(idempotency_key or "").strip() or None
        if key and len(key) > 180:
            raise SchedulerError("idempotency_key is too long")
        job_id = "job_" + uuid.uuid4().hex[:16]
        now = datetime.now(timezone.utc).isoformat()
        owner = owner_id or self.scheduler.settings.default_owner_id
        project = project_id or "scheduled-email"
        payload = {"account_id": aid, "message": frozen, "authorized_at_utc": now,
                   "authorization": "explicit_schedule", "idempotency_key": key or job_id,
                   "tracking_off_authorized": bool(tracking_off_authorized),
                   "send_at": due.isoformat()}
        with self.scheduler._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if not project_id:
                # Dedicated visible project for mail scheduled directly from
                # the Inbox composer, without requiring preconfigured projects.
                conn.execute(
                    "INSERT OR IGNORE INTO projects(id,owner_id,name,description,active,created_at) "
                    "VALUES (?,?,?,'Autonomous one-time scheduled email sends',1,?)",
                    (project, owner, "Scheduled email", now),
                )
            else:
                self.scheduler._require_project(conn, owner, project)
            if key:
                # Re-submitting the same composer form cannot queue a second email.
                for existing in conn.execute(
                    "SELECT id,payload_json FROM jobs WHERE action_type=?", (ACTION,)
                ):
                    old = json.loads(existing["payload_json"])
                    if old.get("idempotency_key") == key:
                        if (old.get("message") != frozen or old.get("account_id") != aid
                                or old.get("send_at") != due.isoformat()):
                            raise SchedulerError("Idempotency key already used for a different message")
                        return self.scheduler.get_job(existing["id"])
            conn.execute(
                """INSERT INTO jobs
                (id,owner_id,project_id,title,description,action_type,
                 execution_profile_id,payload_json,schedule_type,schedule_value,
                 timezone,approval_mode,status,next_run_utc,created_at,updated_at,
                 last_run_utc,last_error)
                VALUES (?,?,?,?,?,?,NULL,?,'once',? ,?,'automatic','scheduled',?,?,?,NULL,NULL)""",
                (job_id, owner, project, "Send email: " + frozen["subject"][:180],
                 "Authorized scheduled email, one attempt only", ACTION,
                 json.dumps(payload, ensure_ascii=False), send_at, timezone_name,
                 due.isoformat(), now, now),
            )
            self.scheduler._record(conn, job_id, "email_send_scheduled", {
                "account_id": aid, "next_run_utc": due.isoformat(),
                "recipient_count": len(recipients), "authorized_at_utc": now,
            })
        return self.scheduler.get_job(job_id)

    def cancel(self, job_id: str) -> dict[str, Any]:
        with self.scheduler._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM jobs WHERE id=? AND action_type=?", (job_id, ACTION)).fetchone()
            if row is None:
                raise SchedulerError("Unknown scheduled email")
            if row["status"] != "scheduled":
                raise SchedulerError("Email is already claimed, sent, failed or cancelled")
            conn.execute("UPDATE jobs SET status='cancelled',next_run_utc=NULL,updated_at=? WHERE id=?",
                         (datetime.now(timezone.utc).isoformat(), job_id))
            self.scheduler._record(conn, job_id, "email_send_cancelled", {})
        return self.scheduler.get_job(job_id)

    def _claim(self) -> dict[str, Any] | None:
        with self.scheduler._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """SELECT * FROM jobs WHERE action_type=? AND approval_mode='automatic'
                AND schedule_type='once' AND status='scheduled'
                AND next_run_utc<=? ORDER BY next_run_utc,id LIMIT 1""",
                (ACTION, datetime.now(timezone.utc).isoformat()),
            ).fetchone()
            if not row:
                return None
            now = datetime.now(timezone.utc).isoformat()
            conn.execute("UPDATE jobs SET status='sending',last_run_utc=?,updated_at=? WHERE id=? AND status='scheduled'",
                         (now, now, row["id"]))
            self.scheduler._record(conn, row["id"], "email_send_claimed", {"at_utc": now})
            return dict(row)

    def _finish(self, job_id: str, *, success: bool, error: str = "") -> None:
        with self.scheduler._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "UPDATE jobs SET status=?,next_run_utc=NULL,last_error=?,updated_at=? WHERE id=? AND status='sending'",
                ("completed" if success else "failed", error[:800] or None, now, job_id),
            )
            self.scheduler._record(conn, job_id, "email_send_completed" if success else "email_send_failed",
                                   {"at_utc": now, "error": error[:250] if error else None})

    def process_once(self) -> bool:
        row = self._claim()
        if not row:
            return False
        job_id = row["id"]
        try:
            payload = json.loads(row["payload_json"])
            message = payload["message"]
            client = self.mail_client(payload["account_id"])
            recipients = list(dict.fromkeys(message["to"] + message["cc"] + message["bcc"]))
            # A newly suppressed recipient must stop a queued send; consent at
            # scheduling is not permission to ignore subsequent suppression.
            if client.suppressed_recipients(recipients):
                raise SchedulerError("Recipient became suppressed before scheduled delivery")
            result = client.send_email(
                **message, idempotency_key="scheduled-" + job_id,
            )
            if (not isinstance(result, dict) or result.get("ok") is False
                    or result.get("sent") is False
                    or (result.get("sent") is not True and result.get("ok") is not True)):
                raise RuntimeError("SMTP send failed or returned an unconfirmed result")
        except Exception as exc:
            log.exception("Scheduled email %s failed; not retrying automatically", job_id)
            self._finish(job_id, success=False,
                         error=f"Send outcome may be uncertain; no automatic retry: {type(exc).__name__}: {exc}")
        else:
            self._finish(job_id, success=True)
        return True

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                while not self._stop.is_set() and self.process_once():
                    pass
            except Exception:
                log.exception("Scheduled email worker loop failed")
            self._stop.wait(self.poll_seconds)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        # In-flight sends may belong to another worker/process. Never seize or
        # replay a 'sending' job on startup: SMTP delivery may already have
        # occurred, so it stays in an in-doubt state for operator review.
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="postmaster-scheduled-email")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)

    def status(self) -> dict[str, Any]:
        with self.scheduler._connect() as conn:
            rows = conn.execute(
                "SELECT status,COUNT(*) AS n FROM jobs WHERE action_type=? GROUP BY status", (ACTION,)
            )
            counts = {row["status"]: row["n"] for row in rows}
        return {"ok": True, "mode": "autonomous_scheduled_email_only",
                "worker_alive": bool(self._thread and self._thread.is_alive()),
                "counts": counts, "poll_seconds": self.poll_seconds}
