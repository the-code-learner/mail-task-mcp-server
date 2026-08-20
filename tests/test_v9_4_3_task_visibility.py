from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from mcp import Client
from mcp.types import TextContent

from postmaster.scheduler_engine import SchedulerEngine, SchedulerError, SchedulerSettings


class V943TaskVisibilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.engine = SchedulerEngine(
            SchedulerSettings(
                db_path=str(self.root / "scheduler.db"),
                default_owner_id="owner-a",
                default_owner_name="Owner A",
                seed_tinkerer_project=False,
                seed_tinkerer_profile=False,
            )
        )
        self.engine.create_project(
            owner_id="owner-a", project_id="project-a", name="Project A"
        )
        self.engine.create_project(
            owner_id="owner-a", project_id="project-b", name="Project B"
        )
        self.engine.create_owner("owner-b", "Owner B")
        self.engine.create_project(
            owner_id="owner-b", project_id="project-c", name="Project C"
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    @staticmethod
    def _future_once(hours: int = 2) -> str:
        return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()

    def _create_job(
        self,
        *,
        owner_id: str = "owner-a",
        project_id: str = "project-a",
        title: str = "Task",
        schedule_type: str = "interval",
        schedule_value: str = "3600",
        payload: dict | None = None,
        execution_profile_id: str | None = None,
    ) -> dict:
        return self.engine.create_job(
            owner_id=owner_id,
            project_id=project_id,
            title=title,
            description=f"Description for {title}",
            action_type="reminder",
            execution_profile_id=execution_profile_id,
            payload=payload or {"kind": "regression"},
            schedule_type=schedule_type,
            schedule_value=schedule_value,
            timezone="Europe/Rome",
            approval_mode="approval_required",
        )

    def _set_job_fields(self, job_id: str, **fields) -> None:
        assignments = ", ".join(f"{name}=?" for name in fields)
        with self.engine._connect() as conn:
            conn.execute(
                f"UPDATE jobs SET {assignments} WHERE id=?",
                [*fields.values(), job_id],
            )

    def _force_due(self, job_id: str) -> None:
        self._set_job_fields(
            job_id,
            next_run_utc=(datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(),
        )

    def _mark_completed(self, job_id: str, created_at: str | None = None) -> None:
        fields = {
            "status": "completed",
            "next_run_utc": None,
            "last_run_utc": datetime.now(timezone.utc).isoformat(),
        }
        if created_at is not None:
            fields["created_at"] = created_at
            fields["updated_at"] = created_at
        self._set_job_fields(job_id, **fields)

    def test_list_jobs_hides_completed_by_default_and_can_include_them(self) -> None:
        active = self._create_job(title="Active")
        completed = self._create_job(title="Completed")
        self._mark_completed(completed["id"])

        default_rows = self.engine.list_jobs()
        self.assertEqual([row["id"] for row in default_rows], [active["id"]])

        all_rows = self.engine.list_jobs(include_completed=True)
        self.assertEqual({row["id"] for row in all_rows}, {active["id"], completed["id"]})

        completed_rows = self.engine.list_jobs(status="completed")
        self.assertEqual([row["id"] for row in completed_rows], [completed["id"]])

    def test_explicit_non_completed_status_filter_still_works(self) -> None:
        scheduled = self._create_job(title="Scheduled")
        paused = self._create_job(title="Paused")
        completed = self._create_job(title="Completed")
        self._set_job_fields(paused["id"], status="paused")
        self._mark_completed(completed["id"])

        scheduled_rows = self.engine.list_jobs(status="scheduled", include_completed=True)
        self.assertEqual([row["id"] for row in scheduled_rows], [scheduled["id"]])
        paused_rows = self.engine.list_jobs(status="paused")
        self.assertEqual([row["id"] for row in paused_rows], [paused["id"]])

    def test_owner_project_filters_combine_with_completed_visibility(self) -> None:
        a_active = self._create_job(owner_id="owner-a", project_id="project-a", title="A active")
        a_done = self._create_job(owner_id="owner-a", project_id="project-a", title="A done")
        b_done = self._create_job(owner_id="owner-a", project_id="project-b", title="B done")
        c_done = self._create_job(owner_id="owner-b", project_id="project-c", title="C done")
        for row in (a_done, b_done, c_done):
            self._mark_completed(row["id"])

        scoped_default = self.engine.list_jobs(owner_id="owner-a", project_id="project-a")
        self.assertEqual([row["id"] for row in scoped_default], [a_active["id"]])

        scoped_all = self.engine.list_jobs(
            owner_id="owner-a", project_id="project-a", include_completed=True
        )
        self.assertEqual({row["id"] for row in scoped_all}, {a_active["id"], a_done["id"]})

        scoped_done = self.engine.list_jobs(
            owner_id="owner-a", project_id="project-a", status="completed"
        )
        self.assertEqual([row["id"] for row in scoped_done], [a_done["id"]])

    def test_limit_is_applied_after_completed_visibility_filter(self) -> None:
        visible_one = self._create_job(title="Visible one")
        visible_two = self._create_job(title="Visible two")
        hidden = [self._create_job(title=f"Hidden {index}") for index in range(3)]

        for row in (visible_one, visible_two):
            self._set_job_fields(
                row["id"],
                status="paused",
                next_run_utc=None,
                created_at="2026-01-01T00:00:00+00:00",
                updated_at="2026-01-01T00:00:00+00:00",
            )
        for index, row in enumerate(hidden):
            self._mark_completed(row["id"], f"2026-12-0{index + 1}T00:00:00+00:00")

        rows = self.engine.list_jobs(limit=2)
        self.assertEqual(len(rows), 2)
        self.assertEqual({row["id"] for row in rows}, {visible_one["id"], visible_two["id"]})

    def test_get_job_returns_complete_record_and_clear_not_found(self) -> None:
        self.engine.create_execution_profile(
            owner_id="owner-a",
            project_id="project-a",
            profile_id="profile-a",
            provider="generic",
            identity="operator",
            description="Regression profile",
        )
        created = self._create_job(
            title="Detailed task",
            payload={"nested": {"value": 7}},
            execution_profile_id="profile-a",
        )
        detail = self.engine.get_job(created["id"])
        required = {
            "id", "owner_id", "project_id", "title", "description", "action_type",
            "execution_profile_id", "schedule_type", "schedule_value", "timezone",
            "approval_mode", "status", "next_run_utc", "created_at", "updated_at",
            "last_run_utc", "last_error", "payload",
        }
        self.assertTrue(required.issubset(detail.keys()))
        self.assertEqual(detail["execution_profile_id"], "profile-a")
        self.assertEqual(detail["payload"], {"nested": {"value": 7}})
        self.assertNotIn("payload_json", detail)

        with self.assertRaisesRegex(SchedulerError, "Job not found"):
            self.engine.get_job("job_missing")

    def test_completed_records_remain_stored_and_scheduler_status_counts_them(self) -> None:
        job = self._create_job(
            title="One shot",
            schedule_type="once",
            schedule_value=self._future_once(),
        )
        self._force_due(job["id"])
        completed = self.engine.complete_job(job["id"], note="handled")
        self.assertEqual(completed["status"], "completed")
        self.assertIsNone(completed["next_run_utc"])
        self.assertEqual(self.engine.get_job(job["id"])["status"], "completed")
        self.assertEqual(self.engine.status()["job_counts"].get("completed"), 1)
        with self.engine._connect() as conn:
            stored = conn.execute("SELECT COUNT(*) FROM jobs WHERE id=?", (job["id"],)).fetchone()[0]
        self.assertEqual(stored, 1)
        self.assertEqual(self.engine.list_jobs(), [])
        self.assertEqual([row["id"] for row in self.engine.list_jobs(status="completed")], [job["id"]])

    def test_create_complete_due_and_recurring_advancement_do_not_regress(self) -> None:
        once = self._create_job(
            title="Due once",
            schedule_type="once",
            schedule_value=self._future_once(),
            payload={"source": "create-regression"},
        )
        self.assertEqual(once["status"], "scheduled")
        self.assertEqual(once["payload"], {"source": "create-regression"})
        self._force_due(once["id"])
        self.assertEqual([row["id"] for row in self.engine.list_due_jobs()], [once["id"]])
        once_done = self.engine.complete_job(once["id"], note="once complete")
        self.assertEqual(once_done["status"], "completed")
        self.assertNotIn(once["id"], [row["id"] for row in self.engine.list_due_jobs()])

        recurring = self._create_job(title="Recurring", schedule_type="interval", schedule_value="3600")
        self._force_due(recurring["id"])
        recurring_done = self.engine.complete_job(recurring["id"], note="advance")
        self.assertEqual(recurring_done["status"], "scheduled")
        self.assertIsNotNone(recurring_done["next_run_utc"])
        self.assertIsNotNone(recurring_done["last_run_utc"])
        self.assertGreater(
            datetime.fromisoformat(recurring_done["next_run_utc"]),
            datetime.now(timezone.utc),
        )

    def test_mcp_tools_expose_structured_list_get_job_and_capabilities(self) -> None:
        runtime_root = self.root / "runtime"
        runtime_root.mkdir()
        old_scheduler_db = os.environ.get("SCHEDULER_DB_PATH")
        os.environ["SCHEDULER_DB_PATH"] = str(runtime_root / "scheduler.db")
        try:
            import postmaster.runtime as runtime

            runtime.scheduler.cache_clear()
            runtime_engine = runtime.scheduler()
            runtime_engine.create_project(
                owner_id=runtime_engine.settings.default_owner_id,
                project_id="runtime-project",
                name="Runtime project",
            )
            visible = runtime_engine.create_job(
                owner_id=runtime_engine.settings.default_owner_id,
                project_id="runtime-project",
                title="Runtime visible",
                description="MCP serialization",
                action_type="reminder",
                execution_profile_id=None,
                payload={"mcp": True},
                schedule_type="interval",
                schedule_value="3600",
                timezone="Europe/Rome",
                approval_mode="approval_required",
            )
            hidden = runtime_engine.create_job(
                owner_id=runtime_engine.settings.default_owner_id,
                project_id="runtime-project",
                title="Runtime completed",
                description="Hidden by default",
                action_type="reminder",
                execution_profile_id=None,
                payload={"mcp": True},
                schedule_type="interval",
                schedule_value="3600",
                timezone="Europe/Rome",
                approval_mode="approval_required",
            )
            with runtime_engine._connect() as conn:
                conn.execute(
                    "UPDATE jobs SET status='completed', next_run_utc=NULL WHERE id=?",
                    (hidden["id"],),
                )

            async def exercise_mcp():
                async with Client(runtime.mcp, raise_exceptions=True) as client:
                    tools = await client.list_tools()
                    listed = await client.call_tool("list_jobs", {})
                    included = await client.call_tool("list_jobs", {"include_completed": True})
                    completed = await client.call_tool("list_jobs", {"status": "completed"})
                    detail = await client.call_tool("get_job", {"job_id": hidden["id"]})
                    missing = await client.call_tool("get_job", {"job_id": "job_missing"})
                    return tools, listed, included, completed, detail, missing

            tools, listed, included, completed, detail, missing = asyncio.run(exercise_mcp())
            tool_map = {tool.name: tool for tool in tools.tools}
            self.assertIn("get_job", tool_map)
            self.assertIn("include_completed", tool_map["list_jobs"].input_schema["properties"])

            def payload(result):
                self.assertFalse(result.is_error)
                self.assertEqual(len(result.content), 1)
                self.assertIsInstance(result.content[0], TextContent)
                return json.loads(result.content[0].text)

            default_payload = payload(listed)
            self.assertEqual(default_payload["count"], 1)
            self.assertEqual([row["id"] for row in default_payload["jobs"]], [visible["id"]])
            self.assertEqual(payload(included)["count"], 2)
            self.assertEqual([row["id"] for row in payload(completed)["jobs"]], [hidden["id"]])
            self.assertEqual(payload(detail)["id"], hidden["id"])
            missing_payload = payload(missing)
            self.assertFalse(missing_payload["ok"])
            self.assertIn("not found", missing_payload["error"].lower())

            status = runtime.build_status()
            self.assertTrue(status["task_detail_view"])
            self.assertTrue(status["completed_tasks_hidden_by_default"])
        finally:
            try:
                import postmaster.runtime as runtime
                runtime.scheduler.cache_clear()
            except Exception:
                pass
            if old_scheduler_db is None:
                os.environ.pop("SCHEDULER_DB_PATH", None)
            else:
                os.environ["SCHEDULER_DB_PATH"] = old_scheduler_db


if __name__ == "__main__":
    unittest.main()
