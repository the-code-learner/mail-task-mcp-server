from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from starlette.requests import Request

from postmaster.scheduler_engine import SchedulerEngine, SchedulerSettings
from postmaster.webgui_helpers import decorate_version, render_markdown_safe
from postmaster.webgui_knowledge import knowledge_fragment
from postmaster.webgui_projects import files_fragment, project_overview_fragment
from postmaster.webgui_tasks import dashboard_job_update, task_fragment


def request(query: str = "") -> Request:
    return Request({
        "type": "http", "http_version": "1.1", "method": "GET", "scheme": "http",
        "path": "/", "raw_path": b"/", "query_string": query.encode(),
        "headers": [], "client": ("127.0.0.1", 12345), "server": ("localhost", 8000),
    })


class FakeKnowledgeStore:
    def __init__(self):
        self.items = [
            {"id": "mem_a", "kind": "memory", "owner_id": "owner-a", "project_id": "project-a", "title": "Architecture A", "content": "# Heading\n\n**bold**\n\n|A|B|\n|-|-|\n|1|2|\n\n```yaml\nkey: value\n```\n\n[link](https://example.com)\n<script>alert(1)</script>", "priority": 0.8, "always_include": True, "enabled": True, "tags": ["architecture"]},
            {"id": "skill_b", "kind": "skill", "owner_id": "owner-a", "project_id": "project-b", "title": "Skill B", "content": "B", "priority": 0.5, "always_include": False, "enabled": True, "tags": []},
            {"id": "global_a", "kind": "memory", "owner_id": "owner-a", "project_id": None, "title": "Global", "content": "global", "priority": 0.4, "always_include": False, "enabled": True, "tags": []},
        ]

    def list_items(self, project_id=None, include_global=True, limit=500, **kwargs):
        rows = list(self.items)
        if project_id is not None:
            rows = [row for row in rows if row["project_id"] == project_id or (include_global and row["project_id"] is None)]
        return rows[:limit]

    def get_item(self, item_id):
        for item in self.items:
            if item["id"] == item_id:
                return dict(item)
        raise ValueError("not found")


class FakeContextEngine:
    def __init__(self):
        self.store = FakeKnowledgeStore()

    def status(self):
        return {"semantic": {"available": False}, "missing_embeddings": 0}

    def search(self, query, project_id=None, include_global=True, limit=50, **kwargs):
        rows = self.store.list_items(project_id=project_id, include_global=include_global, limit=limit)
        return {"ok": True, "results": [{**row, "item_id": row["id"], "score": 1.0, "best_chunk": row["content"]} for row in rows if query.lower() in (row["title"] + row["content"]).lower()]}


class FakeFileStore:
    def __init__(self):
        self.items = [
            {"id": "file_a", "owner_id": "owner-a", "project_id": "project-a", "filename": "a.md", "media_type": "text/markdown", "size_bytes": 10, "tags": ["a"], "description": "A"},
            {"id": "file_b", "owner_id": "owner-a", "project_id": "project-b", "filename": "b.txt", "media_type": "text/plain", "size_bytes": 20, "tags": [], "description": "B"},
            {"id": "file_global", "owner_id": "owner-a", "project_id": None, "filename": "global.txt", "media_type": "text/plain", "size_bytes": 5, "tags": [], "description": "G"},
        ]

    def list_files(self, project_id=None, include_global=True, limit=500, **kwargs):
        rows = list(self.items)
        if project_id is not None:
            rows = [row for row in rows if row["project_id"] == project_id or (include_global and row["project_id"] is None)]
        return rows[:limit]

    def status(self):
        return {"files": len(self.items), "logical_bytes": 35, "max_bytes_per_file": 1048576}


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


class V945WebGuiProjectUxTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.engine = SchedulerEngine(SchedulerSettings(db_path=str(Path(self.tmp.name) / "scheduler.db"), default_owner_id="owner-a", default_owner_name="Owner A", seed_tinkerer_project=False, seed_tinkerer_profile=False))
        self.engine.create_project(owner_id="owner-a", project_id="project-a", name="Project A")
        self.engine.create_project(owner_id="owner-a", project_id="project-b", name="Project B")
        self.job_a = self.engine.create_job(owner_id="owner-a", project_id="project-a", title="Task A", description="A", action_type="reminder", execution_profile_id=None, payload={"a": 1}, schedule_type="interval", schedule_value="3600", timezone="Europe/Rome", approval_mode="approval_required")
        self.job_b = self.engine.create_job(owner_id="owner-a", project_id="project-b", title="Task B", description="B", action_type="reminder", execution_profile_id=None, payload={"b": 1}, schedule_type="interval", schedule_value="7200", timezone="Europe/Rome", approval_mode="approval_required")
        self.base = Base(self.engine)

    def tearDown(self):
        self.tmp.cleanup()

    def test_runtime_version_decoration_has_no_static_release_title(self):
        html = "<title>Postmaster MCP v9.1</title><h1>Postmaster MCP</h1><p>Persistent Context + multi-account IMAP/SMTP + analytics + task registry + small files · v9.1</p>"
        decorated = decorate_version(html, "9.4.5")
        self.assertIn("<title>Postmaster v9.4.5</title>", decorated)
        self.assertIn("<h1>Postmaster v9.4.5</h1>", decorated)
        runtime_source = Path(__import__("postmaster.runtime", fromlist=["x"]).__file__).read_text()
        self.assertNotIn("Postmaster v9.4.5", runtime_source)

    def test_markdown_viewer_supports_tables_code_links_and_strips_html(self):
        source = "# H\n\n**bold**\n\n|A|B|\n|-|-|\n|1|2|\n\n```yaml\nkey: value\n```\n\n[link](https://example.com)\n\n<script>alert(1)</script><img src=x onerror=alert(2)>"
        html = render_markdown_safe(source)
        self.assertIn("<h1>H</h1>", html)
        self.assertIn("<strong>bold</strong>", html)
        self.assertIn("<table>", html)
        self.assertIn("<pre>", html)
        self.assertIn('href="https://example.com"', html)
        self.assertNotIn("<script", html)
        self.assertNotIn("<img", html)
        self.assertNotIn("onerror", html)

    def test_task_project_filter_and_editor_are_webgui_only(self):
        html = task_fragment(self.base, request("project=project-a"))
        self.assertIn("Task A", html)
        self.assertNotIn("Task B", html)
        self.assertIn(">View<", html)
        self.assertIn(">Edit<", html)
        edit = task_fragment(self.base, request(f"project=project-a&edit_job={self.job_a['id']}"))
        for name in ("title", "description", "payload", "schedule_type", "schedule_value", "timezone", "approval_mode"):
            self.assertIn(f'name="{name}"', edit)
        runtime_source = Path(__import__("postmaster.runtime", fromlist=["x"]).__file__).read_text()
        self.assertNotIn("@mcp.tool", runtime_source)
        self.assertNotIn("mcp.add_tool", runtime_source)
        self.assertNotIn("mcp.remove_tool", runtime_source)

    def test_webgui_task_update_uses_existing_engine_fields(self):
        form = {"job_id": self.job_a["id"], "project_filter": "project-a", "title": "Task A updated", "description": "changed", "payload": '{"changed": true}', "schedule_type": "interval", "schedule_value": "1800", "timezone": "Europe/Rome", "approval_mode": "approval_required", "show_completed": ""}

        async def verified(_request):
            return form, None

        async def exercise():
            with patch.object(self.base, "_verified_form", verified, create=True):
                return await dashboard_job_update(self.base, request())

        response = asyncio.run(exercise())
        updated = self.engine.get_job(self.job_a["id"])
        self.assertEqual(updated["title"], "Task A updated")
        self.assertEqual(updated["payload"], {"changed": True})
        self.assertEqual(updated["schedule_value"], "1800")
        self.assertEqual(response.status_code, 303)
        self.assertIn("project=project-a", response.headers["location"])

    def test_knowledge_and_files_filters_and_read_only_view(self):
        knowledge = knowledge_fragment(self.base, request("project=project-a"))
        self.assertIn("Architecture A", knowledge)
        self.assertNotIn("Skill B", knowledge)
        self.assertNotIn(">Global<", knowledge)
        self.assertIn(">View<", knowledge)
        viewed = knowledge_fragment(self.base, request("project=project-a&view_knowledge=mem_a"))
        self.assertIn("markdown-viewer", viewed)
        self.assertIn("<table>", viewed)
        self.assertIn("<pre>", viewed)
        self.assertNotIn("<script", viewed)
        self.assertIn("Edit raw source", viewed)
        files = files_fragment(self.base, request("project=project-a"))
        self.assertIn("a.md", files)
        self.assertNotIn("b.txt", files)
        self.assertNotIn("global.txt", files)

    def test_project_overview_combines_three_existing_registries(self):
        html = project_overview_fragment(self.base, request("project=project-a"))
        self.assertIn("Project A", html)
        self.assertIn("Task A", html)
        self.assertNotIn("Task B", html)
        self.assertIn("Architecture A", html)
        self.assertNotIn("Skill B", html)
        self.assertIn("a.md", html)
        self.assertNotIn("b.txt", html)
        self.assertIn("Open Tasks", html)
        self.assertIn("Open Knowledge", html)
        self.assertIn("Open Files", html)


if __name__ == "__main__":
    unittest.main()
