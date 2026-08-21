from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from starlette.requests import Request

from postmaster.scheduler_engine import SchedulerEngine, SchedulerSettings
from postmaster.webgui_helpers import (
    decorate_styles,
    project_color_class,
    project_label_html,
)
from postmaster.webgui_knowledge import knowledge_fragment
from postmaster.webgui_projects import files_fragment, project_overview_fragment
from postmaster.webgui_tasks import task_fragment


def request(params: dict[str, str] | None = None) -> Request:
    query = urlencode(params or {})
    return Request({
        "type": "http", "http_version": "1.1", "method": "GET", "scheme": "http",
        "path": "/", "raw_path": b"/", "query_string": query.encode(),
        "headers": [], "client": ("127.0.0.1", 12345), "server": ("localhost", 8000),
    })


class FakeKnowledgeStore:
    def __init__(self):
        self.items = [
            {
                "id": "mem_a", "kind": "memory", "owner_id": "owner-a",
                "project_id": "project-a", "title": "Architecture A", "content": "A",
                "priority": 0.8, "always_include": False, "enabled": True, "tags": [],
            },
            {
                "id": "global_a", "kind": "memory", "owner_id": "owner-a",
                "project_id": None, "title": "Global note", "content": "G",
                "priority": 0.5, "always_include": False, "enabled": True, "tags": [],
            },
        ]

    def list_items(self, project_id=None, include_global=True, limit=500, **kwargs):
        rows = list(self.items)
        if project_id is not None:
            rows = [
                row for row in rows
                if row["project_id"] == project_id or (include_global and row["project_id"] is None)
            ]
        return rows[:limit]

    def get_item(self, item_id):
        return next(dict(row) for row in self.items if row["id"] == item_id)


class FakeContextEngine:
    def __init__(self):
        self.store = FakeKnowledgeStore()

    @staticmethod
    def status():
        return {"semantic": {"available": False}, "missing_embeddings": 0}

    def search(self, query, project_id=None, include_global=True, limit=50, **kwargs):
        rows = self.store.list_items(project_id=project_id, include_global=include_global, limit=limit)
        return {"ok": True, "results": [{**row, "item_id": row["id"], "score": 1.0, "best_chunk": row["content"]} for row in rows]}


class FakeFileStore:
    def __init__(self):
        self.items = [
            {
                "id": "file_a", "owner_id": "owner-a", "project_id": "project-a",
                "filename": "a.md", "media_type": "text/markdown", "size_bytes": 10,
                "tags": [], "description": "A",
            },
            {
                "id": "file_global", "owner_id": "owner-a", "project_id": None,
                "filename": "global.txt", "media_type": "text/plain", "size_bytes": 5,
                "tags": [], "description": "G",
            },
        ]

    def list_files(self, project_id=None, include_global=True, limit=500, **kwargs):
        rows = list(self.items)
        if project_id is not None:
            rows = [
                row for row in rows
                if row["project_id"] == project_id or (include_global and row["project_id"] is None)
            ]
        return rows[:limit]

    def status(self):
        return {"files": len(self.items), "logical_bytes": 15, "max_bytes_per_file": 1048576}


class Base:
    def __init__(self, scheduler):
        self._scheduler = scheduler
        self._context = FakeContextEngine()
        self._files = FakeFileStore()

    @staticmethod
    def _safe_call(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    def scheduler(self):
        return self._scheduler

    def context_engine(self):
        return self._context

    def file_store(self):
        return self._files

    @staticmethod
    def _csrf_value():
        return "csrf"


class V951WebGuiFoundationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.engine = SchedulerEngine(SchedulerSettings(
            db_path=str(Path(self.tmp.name) / "scheduler.db"),
            default_owner_id="owner-a", default_owner_name="Owner A",
            seed_tinkerer_project=False, seed_tinkerer_profile=False,
        ))
        self.engine.create_project(owner_id="owner-a", project_id="project-a", name="Project A")
        self.engine.create_project(owner_id="owner-a", project_id="project-b", name="Project B")
        future = datetime.now(timezone.utc) + timedelta(days=2)
        later = datetime.now(timezone.utc) + timedelta(days=3)
        self.job_a = self.engine.create_job(
            owner_id="owner-a", project_id="project-a", title="Task A", description="A",
            action_type="reminder", execution_profile_id=None, payload={}, schedule_type="once",
            schedule_value=future.isoformat(), timezone="Europe/Rome",
            approval_mode="approval_required",
        )
        self.job_b = self.engine.create_job(
            owner_id="owner-a", project_id="project-b", title="Task B", description="B",
            action_type="reminder", execution_profile_id=None, payload={}, schedule_type="once",
            schedule_value=later.isoformat(), timezone="Europe/Rome",
            approval_mode="approval_required",
        )
        self.base = Base(self.engine)

    def tearDown(self):
        self.tmp.cleanup()

    def test_project_color_mapping_is_deterministic_and_global_is_neutral(self):
        first = project_color_class("project-a")
        second = project_color_class("project-a")
        self.assertEqual(first, second)
        self.assertRegex(first, r"^project-color-[0-7]$")
        self.assertEqual(project_color_class(None), "project-color-global")
        self.assertIn("project-color-global", project_label_html("global", None))

    def test_agenda_and_calendar_represent_the_same_filtered_registry_rows(self):
        due = datetime.fromisoformat(self.job_a["next_run_utc"]).astimezone(ZoneInfo("Europe/Rome"))
        agenda = task_fragment(self.base, request({"project": "project-a", "task_view": "agenda"}))
        calendar = task_fragment(self.base, request({
            "project": "project-a", "task_view": "calendar",
            "calendar_month": f"{due.year:04d}-{due.month:02d}",
        }))
        for html in (agenda, calendar):
            self.assertIn("Task A", html)
            self.assertIn(self.job_a["id"], html)
            self.assertNotIn("Task B", html)
            self.assertIn(project_color_class("project-a"), html)
        self.assertIn("Agenda", agenda)
        self.assertIn("Calendar", agenda)
        self.assertIn("task-calendar-grid", calendar)
        self.assertIn("stored <code>next_run_utc</code>", calendar)
        self.assertIn("does not synthesize future executions", calendar)
        self.assertIn("No cron worker runs here", calendar)

    def test_shared_project_color_is_reused_across_tasks_knowledge_files_and_projects(self):
        expected = project_color_class("project-a")
        task_html = task_fragment(self.base, request({"project": "project-a"}))
        knowledge_html = knowledge_fragment(self.base, request({"project": "project-a"}))
        files_html = files_fragment(self.base, request({"project": "project-a"}))
        project_html = project_overview_fragment(self.base, request({"project": "project-a"}))
        for html, value in (
            (task_html, "Task A"),
            (knowledge_html, "Architecture A"),
            (files_html, "a.md"),
            (project_html, "Project A"),
        ):
            self.assertIn(expected, html)
            self.assertIn(value, html)

    def test_global_knowledge_and_files_use_neutral_color(self):
        knowledge_html = knowledge_fragment(self.base, request())
        files_html = files_fragment(self.base, request())
        self.assertIn("Global note", knowledge_html)
        self.assertIn("global.txt", files_html)
        self.assertIn("project-color-global", knowledge_html)
        self.assertIn("project-color-global", files_html)

    def test_foundation_styles_include_calendar_and_project_tokens(self):
        html = decorate_styles("<html><head><style></style></head><body></body></html>")
        self.assertIn("webgui-v951-foundation", html)
        self.assertIn(".task-calendar-grid", html)
        self.assertIn(".project-color-global", html)
        self.assertIn(".project-color-7", html)


if __name__ == "__main__":
    unittest.main()
