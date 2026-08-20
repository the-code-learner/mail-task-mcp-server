from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from croniter import croniter


class SchedulerError(RuntimeError):
    pass


_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$")
VALID_ACTIONS = {"reminder", "action_required", "email"}
VALID_SCHEDULE_TYPES = {"once", "interval", "cron"}
VALID_APPROVAL_MODES = {"automatic", "approval_required", "manual_only"}
VALID_JOB_STATUSES = {
    "scheduled",
    "waiting_approval",
    "paused",
    "completed",
    "failed",
}


@dataclass(frozen=True)
class SchedulerSettings:
    db_path: str
    poll_seconds: int = 30
    default_owner_id: str = "default"
    default_owner_name: str = "Default User"
    seed_tinkerer_project: bool = True
    seed_tinkerer_profile: bool = True
    allow_automatic_email_jobs: bool = False

    @classmethod
    def from_env(cls) -> "SchedulerSettings":
        return cls(
            db_path=os.getenv("SCHEDULER_DB_PATH", "/data/scheduler.db"),
            poll_seconds=max(5, min(int(os.getenv("SCHEDULER_POLL_SECONDS", "30")), 3600)),
            default_owner_id=os.getenv("DEFAULT_OWNER_ID", "default").strip() or "default",
            default_owner_name=os.getenv("DEFAULT_OWNER_NAME", "Default User").strip() or "Default User",
            seed_tinkerer_project=_env_bool("SEED_TINKERER_PROJECT", True),
            seed_tinkerer_profile=_env_bool("SEED_TINKERER_PROFILE", True),
            allow_automatic_email_jobs=_env_bool("ALLOW_AUTOMATIC_EMAIL_JOBS", False),
        )


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _iso_utc(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _parse_iso(value: str, tz_name: str) -> datetime:
    raw = value.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise SchedulerError(
            "Invalid ISO date/time. Example: 2026-08-21T09:00:00+02:00"
        ) from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo(tz_name))
    return dt


def _validate_id(value: str, label: str) -> str:
    value = value.strip()
    if not _ID_RE.fullmatch(value):
        raise SchedulerError(
            f"{label} must match {_ID_RE.pattern} and be at most 64 characters"
        )
    return value


class SchedulerEngine:
    def __init__(self, settings: SchedulerSettings, *, mail_client=None):
        self.settings = settings
        # v8.1 compatibility field only. The task registry never requires
        # or executes an email client.
        self.mail_client = mail_client
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

        Path(self.settings.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
        self._seed_defaults()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.settings.db_path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=15000")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS owners (
                    id TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(owner_id) REFERENCES owners(id)
                );

                CREATE TABLE IF NOT EXISTS execution_profiles (
                    id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    identity TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(owner_id) REFERENCES owners(id),
                    FOREIGN KEY(project_id) REFERENCES projects(id)
                );

                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    action_type TEXT NOT NULL,
                    execution_profile_id TEXT,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    schedule_type TEXT NOT NULL,
                    schedule_value TEXT NOT NULL,
                    timezone TEXT NOT NULL,
                    approval_mode TEXT NOT NULL,
                    status TEXT NOT NULL,
                    next_run_utc TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_run_utc TEXT,
                    last_error TEXT,
                    FOREIGN KEY(owner_id) REFERENCES owners(id),
                    FOREIGN KEY(project_id) REFERENCES projects(id),
                    FOREIGN KEY(execution_profile_id) REFERENCES execution_profiles(id)
                );

                CREATE TABLE IF NOT EXISTS job_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL,
                    occurred_at_utc TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    result_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE INDEX IF NOT EXISTS idx_jobs_due
                    ON jobs(status, next_run_utc);
                CREATE INDEX IF NOT EXISTS idx_jobs_project
                    ON jobs(owner_id, project_id, status);
                CREATE INDEX IF NOT EXISTS idx_history_job
                    ON job_history(job_id, id DESC);
                """
            )

    def _seed_defaults(self) -> None:
        """Seed registry metadata without requiring any configured mailbox."""
        now = _iso_utc(_now_utc())
        owner_id = _validate_id(self.settings.default_owner_id, "default_owner_id")

        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO owners(id, display_name, created_at) VALUES (?, ?, ?)",
                (owner_id, self.settings.default_owner_name, now),
            )

            if self.settings.seed_tinkerer_project:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO projects(id, owner_id, name, description, active, created_at)
                    VALUES ('tinkerer', ?, 'Tinkering with the Tinkerer',
                            'YouTube channel, sponsorships, collaborations and related work.',
                            1, ?)
                    """,
                    (owner_id, now),
                )

            # v8.1 deliberately does not seed an email execution profile.
            # Existing profiles in the persistent DB are preserved unchanged.

    def start(self) -> None:
        """v8.1 registry-only mode: deliberately does not start a background worker."""
        return None

    def _worker(self) -> None:
        while not self._stop.wait(self.settings.poll_seconds):
            try:
                self._process_due_jobs()
            except Exception:
                # Keep the daemon alive. Per-job failures are recorded in the DB.
                continue

    def _record(self, conn: sqlite3.Connection, job_id: str, event_type: str, result: Any) -> None:
        try:
            payload = json.dumps(result, ensure_ascii=False, default=str)
        except Exception:
            payload = json.dumps({"result": str(result)}, ensure_ascii=False)
        conn.execute(
            """
            INSERT INTO job_history(job_id, occurred_at_utc, event_type, result_json)
            VALUES (?, ?, ?, ?)
            """,
            (job_id, _iso_utc(_now_utc()), event_type, payload),
        )

    def status(self) -> dict[str, Any]:
        with self._connect() as conn:
            counts = {
                row["status"]: row["n"]
                for row in conn.execute(
                    "SELECT status, COUNT(*) AS n FROM jobs GROUP BY status"
                ).fetchall()
            }
            owner_count = conn.execute("SELECT COUNT(*) FROM owners").fetchone()[0]
            project_count = conn.execute("SELECT COUNT(*) FROM projects WHERE active=1").fetchone()[0]
            profile_count = conn.execute(
                "SELECT COUNT(*) FROM execution_profiles WHERE enabled=1"
            ).fetchone()[0]
        return {
            "ok": True,
            "mode": "task_registry_only",
            "registry_only": True,
            "autonomous_execution": False,
            "db_path": self.settings.db_path,
            "worker_alive": False,
            "owners": owner_count,
            "active_projects": project_count,
            "active_execution_profiles": profile_count,
            "job_counts": counts,
        }

    def create_owner(self, owner_id: str, display_name: str) -> dict[str, Any]:
        owner_id = _validate_id(owner_id, "owner_id")
        display_name = display_name.strip()
        if not display_name:
            raise SchedulerError("display_name is required")
        now = _iso_utc(_now_utc())
        try:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO owners(id, display_name, created_at) VALUES (?, ?, ?)",
                    (owner_id, display_name, now),
                )
        except sqlite3.IntegrityError as exc:
            raise SchedulerError(f"Owner already exists: {owner_id}") from exc
        return {"ok": True, "owner_id": owner_id, "display_name": display_name}

    def list_owners(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, display_name, created_at FROM owners ORDER BY id"
            ).fetchall()
        return [dict(r) for r in rows]

    def _require_owner(self, conn: sqlite3.Connection, owner_id: str) -> sqlite3.Row:
        row = conn.execute("SELECT * FROM owners WHERE id=?", (owner_id,)).fetchone()
        if not row:
            raise SchedulerError(f"Unknown owner: {owner_id}")
        return row

    def _require_project(
        self, conn: sqlite3.Connection, owner_id: str, project_id: str
    ) -> sqlite3.Row:
        row = conn.execute(
            "SELECT * FROM projects WHERE id=? AND owner_id=? AND active=1",
            (project_id, owner_id),
        ).fetchone()
        if not row:
            raise SchedulerError(
                f"Unknown/disabled project {project_id!r} for owner {owner_id!r}"
            )
        return row

    def create_project(
        self,
        *,
        owner_id: str,
        project_id: str,
        name: str,
        description: str = "",
    ) -> dict[str, Any]:
        owner_id = _validate_id(owner_id, "owner_id")
        project_id = _validate_id(project_id, "project_id")
        name = name.strip()
        if not name:
            raise SchedulerError("Project name is required")
        now = _iso_utc(_now_utc())
        with self._connect() as conn:
            self._require_owner(conn, owner_id)
            try:
                conn.execute(
                    """
                    INSERT INTO projects(id, owner_id, name, description, active, created_at)
                    VALUES (?, ?, ?, ?, 1, ?)
                    """,
                    (project_id, owner_id, name, description.strip(), now),
                )
            except sqlite3.IntegrityError as exc:
                raise SchedulerError(f"Project already exists: {project_id}") from exc
        return {
            "ok": True,
            "owner_id": owner_id,
            "project_id": project_id,
            "name": name,
        }

    def list_projects(self, owner_id: str | None = None) -> list[dict[str, Any]]:
        with self._connect() as conn:
            if owner_id:
                rows = conn.execute(
                    """
                    SELECT id, owner_id, name, description, active, created_at
                    FROM projects WHERE owner_id=? ORDER BY id
                    """,
                    (owner_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT id, owner_id, name, description, active, created_at
                    FROM projects ORDER BY owner_id, id
                    """
                ).fetchall()
        return [dict(r) for r in rows]

    def create_execution_profile(
        self,
        *,
        owner_id: str,
        project_id: str,
        profile_id: str,
        provider: str,
        identity: str,
        description: str = "",
    ) -> dict[str, Any]:
        owner_id = _validate_id(owner_id, "owner_id")
        project_id = _validate_id(project_id, "project_id")
        profile_id = _validate_id(profile_id, "profile_id")
        provider = provider.strip().lower()
        identity = identity.strip().lower()

        if not provider:
            raise SchedulerError("provider cannot be empty")
        if not identity:
            raise SchedulerError("identity cannot be empty")

        # v8.1 registry-only semantics: profiles are descriptive references only.
        # They never authorize, select or execute an email account.

        now = _iso_utc(_now_utc())
        with self._connect() as conn:
            self._require_project(conn, owner_id, project_id)
            try:
                conn.execute(
                    """
                    INSERT INTO execution_profiles(
                        id, owner_id, project_id, provider, identity, description, enabled, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 1, ?)
                    """,
                    (
                        profile_id,
                        owner_id,
                        project_id,
                        provider,
                        identity,
                        description.strip(),
                        now,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise SchedulerError(f"Execution profile already exists: {profile_id}") from exc

        return {
            "ok": True,
            "profile_id": profile_id,
            "owner_id": owner_id,
            "project_id": project_id,
            "provider": provider,
            "identity": identity,
        }

    def list_execution_profiles(
        self,
        *,
        owner_id: str | None = None,
        project_id: str | None = None,
    ) -> list[dict[str, Any]]:
        query = """
            SELECT id, owner_id, project_id, provider, identity, description, enabled, created_at
            FROM execution_profiles
        """
        clauses = []
        args: list[Any] = []
        if owner_id:
            clauses.append("owner_id=?")
            args.append(owner_id)
        if project_id:
            clauses.append("project_id=?")
            args.append(project_id)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY owner_id, project_id, id"
        with self._connect() as conn:
            rows = conn.execute(query, args).fetchall()
        return [dict(r) for r in rows]

    def _require_profile_for_job(
        self,
        conn: sqlite3.Connection,
        *,
        owner_id: str,
        project_id: str,
        profile_id: str,
        action_type: str,
    ) -> sqlite3.Row:
        row = conn.execute(
            """
            SELECT * FROM execution_profiles
            WHERE id=? AND owner_id=? AND project_id=? AND enabled=1
            """,
            (profile_id, owner_id, project_id),
        ).fetchone()
        if not row:
            raise SchedulerError(
                "Execution profile is missing, disabled, or does not belong to the "
                "same owner/project as the job"
            )
        # Registry-only: profiles are passive metadata only.
        return row

    def _validate_payload(self, action_type: str, payload: dict[str, Any]) -> None:
        if not isinstance(payload, dict):
            raise SchedulerError("payload must be an object")
        # Registry-only mode: payload is descriptive metadata and is never executed.
        return

    def _next_run(
        self,
        *,
        schedule_type: str,
        schedule_value: str,
        tz_name: str,
        after_utc: datetime | None = None,
        for_creation: bool = False,
    ) -> datetime | None:
        if schedule_type not in VALID_SCHEDULE_TYPES:
            raise SchedulerError(
                f"schedule_type must be one of {sorted(VALID_SCHEDULE_TYPES)}"
            )
        try:
            tz = ZoneInfo(tz_name)
        except Exception as exc:
            raise SchedulerError(f"Unknown timezone: {tz_name}") from exc

        now = (after_utc or _now_utc()).astimezone(timezone.utc)

        if schedule_type == "once":
            dt = _parse_iso(schedule_value, tz_name).astimezone(timezone.utc)
            if for_creation and dt <= _now_utc():
                raise SchedulerError("A new one-shot job must be scheduled in the future")
            if after_utc is not None and dt <= after_utc.astimezone(timezone.utc):
                return None
            return dt

        if schedule_type == "interval":
            try:
                seconds = int(schedule_value)
            except ValueError as exc:
                raise SchedulerError("interval schedule_value must be integer seconds") from exc
            if seconds < 60:
                raise SchedulerError("Minimum interval is 60 seconds")
            return now + timedelta(seconds=seconds)

        # cron
        expr = schedule_value.strip()
        try:
            base_local = now.astimezone(tz)
            nxt = croniter(expr, base_local).get_next(datetime)
        except Exception as exc:
            raise SchedulerError(f"Invalid cron expression: {expr}") from exc
        if nxt.tzinfo is None:
            nxt = nxt.replace(tzinfo=tz)
        return nxt.astimezone(timezone.utc)

    def preview_schedule(
        self,
        *,
        schedule_type: str,
        schedule_value: str,
        timezone: str = "Europe/Rome",
        count: int = 5,
    ) -> dict[str, Any]:
        count = max(1, min(count, 20))
        cursor = _now_utc()
        out: list[str] = []
        if schedule_type == "once":
            nxt = self._next_run(
                schedule_type=schedule_type,
                schedule_value=schedule_value,
                tz_name=timezone,
                for_creation=False,
            )
            if nxt:
                out.append(_iso_utc(nxt))
        else:
            for _ in range(count):
                nxt = self._next_run(
                    schedule_type=schedule_type,
                    schedule_value=schedule_value,
                    tz_name=timezone,
                    after_utc=cursor,
                    for_creation=False,
                )
                if not nxt:
                    break
                out.append(_iso_utc(nxt))
                cursor = nxt
        return {
            "ok": True,
            "schedule_type": schedule_type,
            "schedule_value": schedule_value,
            "timezone": timezone,
            "occurrences_utc": out,
        }

    def create_job(
        self,
        *,
        owner_id: str,
        project_id: str,
        title: str,
        description: str,
        action_type: str,
        execution_profile_id: str | None,
        payload: dict[str, Any],
        schedule_type: str,
        schedule_value: str,
        timezone: str,
        approval_mode: str,
    ) -> dict[str, Any]:
        owner_id = _validate_id(owner_id, "owner_id")
        project_id = _validate_id(project_id, "project_id")
        title = title.strip()
        action_type = action_type.strip().lower()
        schedule_type = schedule_type.strip().lower()
        approval_mode = approval_mode.strip().lower()

        if not title:
            raise SchedulerError("title is required")
        if action_type not in VALID_ACTIONS:
            raise SchedulerError(f"action_type must be one of {sorted(VALID_ACTIONS)}")
        if approval_mode not in VALID_APPROVAL_MODES:
            raise SchedulerError(
                f"approval_mode must be one of {sorted(VALID_APPROVAL_MODES)}"
            )
        self._validate_payload(action_type, payload)

        next_run = self._next_run(
            schedule_type=schedule_type,
            schedule_value=schedule_value,
            tz_name=timezone,
            for_creation=True,
        )
        now = _iso_utc(_now_utc())
        job_id = "job_" + uuid.uuid4().hex[:16]

        with self._connect() as conn:
            self._require_project(conn, owner_id, project_id)
            if execution_profile_id:
                self._require_profile_for_job(
                    conn,
                    owner_id=owner_id,
                    project_id=project_id,
                    profile_id=execution_profile_id,
                    action_type="reminder",
                )

            conn.execute(
                """
                INSERT INTO jobs(
                    id, owner_id, project_id, title, description, action_type,
                    execution_profile_id, payload_json, schedule_type, schedule_value,
                    timezone, approval_mode, status, next_run_utc,
                    created_at, updated_at, last_run_utc, last_error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'scheduled', ?, ?, ?, NULL, NULL)
                """,
                (
                    job_id,
                    owner_id,
                    project_id,
                    title,
                    description.strip(),
                    action_type,
                    execution_profile_id,
                    json.dumps(payload, ensure_ascii=False),
                    schedule_type,
                    schedule_value.strip(),
                    timezone,
                    approval_mode,
                    _iso_utc(next_run),
                    now,
                    now,
                ),
            )
            self._record(
                conn,
                job_id,
                "created",
                {
                    "owner_id": owner_id,
                    "project_id": project_id,
                    "action_type": action_type,
                    "execution_profile_id": execution_profile_id,
                    "next_run_utc": _iso_utc(next_run),
                },
            )

        return self.get_job(job_id)

    def _row_to_job(self, row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        try:
            result["payload"] = json.loads(result.pop("payload_json"))
        except Exception:
            result["payload"] = {}
            result.pop("payload_json", None)
        return result

    def get_job(self, job_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            raise SchedulerError(f"Unknown job: {job_id}")
        return self._row_to_job(row)

    def list_jobs(
        self,
        *,
        owner_id: str | None = None,
        project_id: str | None = None,
        status: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 1000))
        q = "SELECT * FROM jobs"
        clauses = []
        args: list[Any] = []
        if owner_id:
            clauses.append("owner_id=?")
            args.append(owner_id)
        if project_id:
            clauses.append("project_id=?")
            args.append(project_id)
        if status:
            if status not in VALID_JOB_STATUSES:
                raise SchedulerError(f"Unknown status: {status}")
            clauses.append("status=?")
            args.append(status)
        if clauses:
            q += " WHERE " + " AND ".join(clauses)
        q += " ORDER BY COALESCE(next_run_utc, '9999') ASC, created_at DESC LIMIT ?"
        args.append(limit)
        with self._connect() as conn:
            rows = conn.execute(q, args).fetchall()
        return [self._row_to_job(r) for r in rows]

    def list_due_jobs(
        self,
        *,
        owner_id: str | None = None,
        project_id: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        # Registry-only mode: due tasks are computed on read; nothing is executed or mutated.
        now = _iso_utc(_now_utc())
        q = """
            SELECT * FROM jobs
            WHERE (
                status='waiting_approval'
                OR (status='scheduled' AND next_run_utc IS NOT NULL AND next_run_utc<=?)
            )
        """
        args: list[Any] = [now]
        if owner_id:
            q += " AND owner_id=?"
            args.append(owner_id)
        if project_id:
            q += " AND project_id=?"
            args.append(project_id)
        q += " ORDER BY next_run_utc ASC LIMIT ?"
        args.append(max(1, min(limit, 1000)))
        with self._connect() as conn:
            rows = conn.execute(q, args).fetchall()
        return [self._row_to_job(r) for r in rows]

    def update_job(
        self,
        *,
        job_id: str,
        title: str | None = None,
        description: str | None = None,
        payload: dict[str, Any] | None = None,
        schedule_type: str | None = None,
        schedule_value: str | None = None,
        timezone: str | None = None,
        approval_mode: str | None = None,
    ) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not row:
                raise SchedulerError(f"Unknown job: {job_id}")
            if row["status"] == "completed":
                raise SchedulerError("Completed jobs are immutable; create a new job instead")

            new_title = row["title"] if title is None else title.strip()
            new_desc = row["description"] if description is None else description.strip()
            new_payload = json.loads(row["payload_json"]) if payload is None else payload
            new_schedule_type = row["schedule_type"] if schedule_type is None else schedule_type.strip().lower()
            new_schedule_value = row["schedule_value"] if schedule_value is None else schedule_value.strip()
            new_timezone = row["timezone"] if timezone is None else timezone
            new_approval = row["approval_mode"] if approval_mode is None else approval_mode.strip().lower()

            if not new_title:
                raise SchedulerError("title cannot be empty")
            if new_approval not in VALID_APPROVAL_MODES:
                raise SchedulerError("Invalid approval_mode")
            if (
                row["action_type"] == "email"
                and new_approval == "automatic"
                and not self.settings.allow_automatic_email_jobs
            ):
                raise SchedulerError("Automatic email jobs are disabled server-side")
            self._validate_payload(row["action_type"], new_payload)

            next_run = self._next_run(
                schedule_type=new_schedule_type,
                schedule_value=new_schedule_value,
                tz_name=new_timezone,
                for_creation=True,
            )

            conn.execute(
                """
                UPDATE jobs
                SET title=?, description=?, payload_json=?, schedule_type=?, schedule_value=?,
                    timezone=?, approval_mode=?, status='scheduled', next_run_utc=?,
                    updated_at=?, last_error=NULL
                WHERE id=?
                """,
                (
                    new_title,
                    new_desc,
                    json.dumps(new_payload, ensure_ascii=False),
                    new_schedule_type,
                    new_schedule_value,
                    new_timezone,
                    new_approval,
                    _iso_utc(next_run),
                    _iso_utc(_now_utc()),
                    job_id,
                ),
            )
            self._record(conn, job_id, "updated", {"next_run_utc": _iso_utc(next_run)})
        return self.get_job(job_id)

    def pause_job(self, job_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not row:
                raise SchedulerError(f"Unknown job: {job_id}")
            if row["status"] == "completed":
                raise SchedulerError("Completed jobs cannot be paused")
            conn.execute(
                "UPDATE jobs SET status='paused', updated_at=? WHERE id=?",
                (_iso_utc(_now_utc()), job_id),
            )
            self._record(conn, job_id, "paused", {})
        return self.get_job(job_id)

    def resume_job(self, job_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not row:
                raise SchedulerError(f"Unknown job: {job_id}")
            if row["status"] == "completed":
                raise SchedulerError("Completed jobs cannot be resumed")
            next_run = self._next_run(
                schedule_type=row["schedule_type"],
                schedule_value=row["schedule_value"],
                tz_name=row["timezone"],
                after_utc=_now_utc(),
                for_creation=False,
            )
            if next_run is None:
                raise SchedulerError("The one-shot schedule is already in the past")
            conn.execute(
                """
                UPDATE jobs
                SET status='scheduled', next_run_utc=?, updated_at=?, last_error=NULL
                WHERE id=?
                """,
                (_iso_utc(next_run), _iso_utc(_now_utc()), job_id),
            )
            self._record(conn, job_id, "resumed", {"next_run_utc": _iso_utc(next_run)})
        return self.get_job(job_id)

    def _execute_action(
        self,
        conn: sqlite3.Connection,
        row: sqlite3.Row,
    ) -> dict[str, Any]:
        """Execution is intentionally disabled in the v8.1 task registry."""
        raise SchedulerError(
            "The v8.1 scheduler is a passive task registry and cannot execute actions. "
            "Use explicit MCP tools for the action, then mark the task complete."
        )

    def _advance_after_success(
        self,
        conn: sqlite3.Connection,
        row: sqlite3.Row,
        result: dict[str, Any],
        event_type: str,
    ) -> None:
        now = _now_utc()
        next_run = self._next_run(
            schedule_type=row["schedule_type"],
            schedule_value=row["schedule_value"],
            tz_name=row["timezone"],
            after_utc=now,
            for_creation=False,
        )
        if row["schedule_type"] == "once" or next_run is None:
            new_status = "completed"
            next_run_iso = None
        else:
            new_status = "scheduled"
            next_run_iso = _iso_utc(next_run)

        conn.execute(
            """
            UPDATE jobs
            SET status=?, next_run_utc=?, last_run_utc=?, updated_at=?, last_error=NULL
            WHERE id=?
            """,
            (
                new_status,
                next_run_iso,
                _iso_utc(now),
                _iso_utc(now),
                row["id"],
            ),
        )
        self._record(conn, row["id"], event_type, result)

    def _process_due_jobs(self) -> None:
        """Registry-only mode: intentionally no autonomous processing."""
        return None

    def approve_job(self, job_id: str) -> dict[str, Any]:
        raise SchedulerError(
            "The v8.1 scheduler is a task registry only and never executes actions. "
            "Use the email MCP tools explicitly, then complete the task."
        )

    def complete_job(self, job_id: str, *, note: str = "") -> dict[str, Any]:
        with self._lock:
            with self._connect() as conn:
                row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
                if not row:
                    raise SchedulerError(f"Unknown job: {job_id}")
                if row["status"] not in {"scheduled", "waiting_approval"}:
                    raise SchedulerError(
                        f"Task cannot be completed from status {row['status']}"
                    )
                if row["next_run_utc"]:
                    try:
                        due = datetime.fromisoformat(row["next_run_utc"])
                        if due.tzinfo is None:
                            due = due.replace(tzinfo=timezone.utc)
                        if due > _now_utc():
                            raise SchedulerError("Task is not due yet")
                    except ValueError:
                        pass
                result = {
                    "handled": True,
                    "note": note,
                    "action_type": row["action_type"],
                    "registry_only": True,
                }
                self._advance_after_success(
                    conn, row, result, event_type="completed_by_user"
                )
        return self.get_job(job_id)

    def delete_job(self, job_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, owner_id, project_id, title FROM jobs WHERE id=?",
                (job_id,),
            ).fetchone()
            if not row:
                raise SchedulerError(f"Unknown job: {job_id}")
            self._record(conn, job_id, "deleted", dict(row))
            conn.execute("DELETE FROM jobs WHERE id=?", (job_id,))
        return {"ok": True, "deleted_job_id": job_id}

    def get_job_history(self, job_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 1000))
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, job_id, occurred_at_utc, event_type, result_json
                FROM job_history WHERE job_id=?
                ORDER BY id DESC LIMIT ?
                """,
                (job_id, limit),
            ).fetchall()
        out = []
        for row in rows:
            d = dict(row)
            try:
                d["result"] = json.loads(d.pop("result_json"))
            except Exception:
                d["result"] = d.pop("result_json")
            out.append(d)
        return out
