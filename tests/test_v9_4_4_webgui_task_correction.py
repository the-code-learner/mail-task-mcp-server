from __future__ import annotations

import asyncio
import inspect
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from mcp import Client
from mcp.types import TextContent
from starlette.requests import Request

from postmaster.scheduler_engine import SchedulerEngine, SchedulerError, SchedulerSettings


class V944WebGuiTaskCorrectionTests(unittest.TestCase):
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

    def tearDown(self) -> None:
        self.tmp.cleanup()

    @staticmethod
    def _future_once(hours: int = 2) -> str:
        return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()

    @staticmethod
    def _request(query: str = "") -> Request:
        return Request(
            {
                "type": "http",
                "http_version": "1.1",
                "method": "GET",
                "scheme": "http",
                "path": "/",
                "raw_path": b"/",
                "query_string": query.encode("utf-8"),
                "headers": [],
                "client": ("127.0.0.1", 12345),
                "server": ("localhost", 8000),
            }
        )

    def _create_job(
        self,
        *,
        title: str,
        schedule_type: str = "interval",
        schedule_value: str = "3600",
        description: str | None = None,
        payload: dict | None = None,
    ) -> dict:
        return self.engine.create_job(
            owner_id="owner-a",
            project_id="project-a",
            title=title,
            description=description if description is not None else f"Description for {title}",
            action_type="reminder",
            execution_profile_id=None,
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

    def _mark_completed(self, job_id: str) -> None:
        self._set_job_fields(
            job_id,
            status="completed",
            next_run_utc=None,
            last_run_utc=datetime.now(timezone.utc).isoformat(),
        )

    def test_scheduler_list_jobs_signature_and_semantics_match_v942(self) -> None:
        signature = inspect.signature(SchedulerEngine.list_jobs)
        self.assertEqual(
            list(signature.parameters),
            ["self", "owner_id", "project_id", "status", "limit"],
        )
        self.assertNotIn("include_completed", signature.parameters)
        self.assertEqual(signature.parameters["limit"].default, 200)

        active = self._create_job(title="Active")
        completed = self._create_job(title="Completed")
        self._mark_completed(completed["id"])

        default_rows = self.engine.list_jobs()
        self.assertEqual(
            {row["id"] for row in default_rows},
            {active["id"], completed["id"]},
        )
        self.assertEqual(
            [row["id"] for row in self.engine.list_jobs(status="completed")],
            [completed["id"]],
        )
        with self.assertRaisesRegex(SchedulerError, "Unknown job: job_missing"):
            self.engine.get_job("job_missing")

    def test_mcp_list_jobs_schema_output_and_single_get_job_match_v942(self) -> None:
        runtime_root = self.root / "runtime"
        runtime_root.mkdir()
        old_scheduler_db = os.environ.get("SCHEDULER_DB_PATH")
        os.environ["SCHEDULER_DB_PATH"] = str(runtime_root / "scheduler.db")
        try:
            import postmaster.runtime as runtime

            runtime._base.scheduler.cache_clear()
            runtime_engine = runtime._base.scheduler()
            runtime_engine.create_project(
                owner_id=runtime_engine.settings.default_owner_id,
                project_id="runtime-project",
                name="Runtime project",
            )
            active = runtime_engine.create_job(
                owner_id=runtime_engine.settings.default_owner_id,
                project_id="runtime-project",
                title="Runtime active",
                description="MCP v9.4.2 compatibility",
                action_type="reminder",
                execution_profile_id=None,
                payload={"mcp": True},
                schedule_type="interval",
                schedule_value="3600",
                timezone="Europe/Rome",
                approval_mode="approval_required",
            )
            completed = runtime_engine.create_job(
                owner_id=runtime_engine.settings.default_owner_id,
                project_id="runtime-project",
                title="Runtime completed",
                description="Must remain visible through MCP by default",
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
                    (completed["id"],),
                )

            list_signature = inspect.signature(runtime._base.list_jobs)
            self.assertEqual(
                list(list_signature.parameters),
                ["owner_id", "project_id", "status", "limit"],
            )
            self.assertNotIn("include_completed", list_signature.parameters)
            self.assertEqual(list_signature.parameters["limit"].default, 200)

            async def exercise_mcp():
                async with Client(runtime.mcp, raise_exceptions=True) as client:
                    tools = await client.list_tools()
                    listed = await client.call_tool("list_jobs", {})
                    completed_only = await client.call_tool(
                        "list_jobs", {"status": "completed"}
                    )
                    detail = await client.call_tool(
                        "get_job", {"job_id": completed["id"]}
                    )
                    return tools, listed, completed_only, detail

            tools, listed, completed_only, detail = asyncio.run(exercise_mcp())
            names = [tool.name for tool in tools.tools]
            self.assertEqual(names.count("get_job"), 1)
            tool_map = {tool.name: tool for tool in tools.tools}
            properties = tool_map["list_jobs"].input_schema["properties"]
            self.assertEqual(
                set(properties), {"owner_id", "project_id", "status", "limit"}
            )
            self.assertNotIn("include_completed", properties)
            self.assertEqual(properties["limit"].get("default"), 200)

            def decoded_rows(result) -> list[dict]:
                self.assertFalse(result.is_error)
                texts = [
                    content.text
                    for content in result.content
                    if isinstance(content, TextContent)
                ]
                self.assertTrue(texts)
                if len(texts) == 1:
                    payload = json.loads(texts[0])
                    self.assertIsInstance(payload, list)
                    return payload
                rows = [json.loads(text) for text in texts]
                self.assertTrue(all(isinstance(row, dict) for row in rows))
                return rows

            default_rows = decoded_rows(listed)
            self.assertEqual(
                {row["id"] for row in default_rows},
                {active["id"], completed["id"]},
            )
            self.assertFalse(
                isinstance(json.loads(listed.content[0].text), dict)
                and "jobs" in json.loads(listed.content[0].text)
            )
            self.assertEqual(
                [row["id"] for row in decoded_rows(completed_only)],
                [completed["id"]],
            )
            detail_payload = json.loads(detail.content[0].text)
            self.assertEqual(detail_payload["id"], completed["id"])

            server_source = Path(runtime._base.__file__).read_text(encoding="utf-8")
            runtime_source = Path(runtime.__file__).read_text(encoding="utf-8")
            runtime_core_source = Path(runtime._core.__file__).read_text(encoding="utf-8")
            self.assertEqual(server_source.count("def get_job(job_id: str):"), 1)
            self.assertNotIn("def get_job(job_id", runtime_source)
            self.assertNotIn("def get_job(job_id", runtime_core_source)

            status = runtime.build_status()
            self.assertNotIn("task_detail_view", status)
            self.assertNotIn("completed_tasks_hidden_by_default", status)
        finally:
            try:
                import postmaster.runtime as runtime

                runtime._base.scheduler.cache_clear()
            except Exception:
                pass
            if old_scheduler_db is None:
                os.environ.pop("SCHEDULER_DB_PATH", None)
            else:
                os.environ["SCHEDULER_DB_PATH"] = old_scheduler_db

    def test_webgui_default_show_hide_counts_actions_and_safe_detail(self) -> None:
        import postmaster.runtime as runtime

        active = self._create_job(
            title="Active <unsafe>",
            description="Active description",
        )
        paused = self._create_job(title="Paused task")
        self._set_job_fields(paused["id"], status="paused")
        completed = self._create_job(
            title="Completed detail",
            description='Completed <img src=x onerror="alert(1)"> description',
            payload={"html": "<script>alert(1)</script>", "nested": {"value": 7}},
        )
        self._mark_completed(completed["id"])

        with patch.object(runtime._base, "scheduler", return_value=self.engine):
            default_html = runtime._task_dashboard_fragment(self._request())
            self.assertIn("Active &lt;unsafe&gt;", default_html)
            self.assertIn("Paused task", default_html)
            self.assertNotIn("Completed detail", default_html)
            self.assertIn("Show completed (1)", default_html)
            self.assertIn("1 completed hidden", default_html)
            self.assertIn("3 stored", default_html)
            self.assertIn("/dashboard/job/pause", default_html)
            self.assertIn("/dashboard/job/resume", default_html)
            self.assertIn(">View<", default_html)

            shown_html = runtime._task_dashboard_fragment(
                self._request("show_completed=1")
            )
            self.assertIn("Completed detail", shown_html)
            self.assertIn("Hide completed", shown_html)
            self.assertIn("1 completed", shown_html)
            self.assertNotIn("completed hidden", shown_html)

            detail_html = runtime._task_dashboard_fragment(
                self._request(f"view_job={completed['id']}")
            )
            self.assertIn("Task detail", detail_html)
            self.assertIn("Completed detail", detail_html)
            self.assertIn("Show completed (1)", detail_html)
            for field in (
                "id", "owner_id", "project_id", "title", "description",
                "action_type", "execution_profile_id", "schedule_type",
                "schedule_value", "timezone", "approval_mode", "status",
                "next_run_utc", "created_at", "updated_at", "last_run_utc",
                "last_error", "payload",
            ):
                self.assertIn(f"<th>{field}</th>", detail_html)
            self.assertNotIn("<script>alert(1)</script>", detail_html)
            self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", detail_html)
            self.assertNotIn('<img src=x onerror="alert(1)">', detail_html)
            self.assertIn("&lt;img src=x onerror=&quot;alert(1)&quot;&gt;", detail_html)

        self.assertEqual(self.engine.get_job(active["id"])["status"], "scheduled")
        self.assertEqual(self.engine.get_job(completed["id"])["status"], "completed")

    def test_pause_resume_due_complete_recurring_and_status_do_not_regress(self) -> None:
        paused = self._create_job(title="Pause regression")
        paused_state = self.engine.pause_job(paused["id"])
        self.assertEqual(paused_state["status"], "paused")
        resumed_state = self.engine.resume_job(paused["id"])
        self.assertEqual(resumed_state["status"], "scheduled")

        once = self._create_job(
            title="Due once",
            schedule_type="once",
            schedule_value=self._future_once(),
            payload={"source": "create-regression"},
        )
        self._force_due(once["id"])
        self.assertIn(once["id"], [row["id"] for row in self.engine.list_due_jobs()])
        once_done = self.engine.complete_job(once["id"], note="once complete")
        self.assertEqual(once_done["status"], "completed")
        self.assertIsNone(once_done["next_run_utc"])
        self.assertIn(once["id"], [row["id"] for row in self.engine.list_jobs()])
        self.assertEqual(self.engine.status()["job_counts"].get("completed"), 1)
        self.assertNotIn(once["id"], [row["id"] for row in self.engine.list_due_jobs()])

        recurring = self._create_job(
            title="Recurring",
            schedule_type="interval",
            schedule_value="3600",
        )
        self._force_due(recurring["id"])
        recurring_done = self.engine.complete_job(recurring["id"], note="advance")
        self.assertEqual(recurring_done["status"], "scheduled")
        self.assertIsNotNone(recurring_done["last_run_utc"])
        self.assertGreater(
            datetime.fromisoformat(recurring_done["next_run_utc"]),
            datetime.now(timezone.utc),
        )


if __name__ == "__main__":
    unittest.main()
