from __future__ import annotations

import hashlib
import sqlite3
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from starlette.requests import Request

from postmaster.file_store import FileStore
from postmaster.knowledge_scopes import KnowledgeScopeStore
from postmaster.knowledge_store import KnowledgeStore
from postmaster.project_scope_semantics import install_project_scope_semantics
from postmaster.project_service import (
    ProjectDeletionBlocked,
    ProjectService,
    ProjectServiceError,
    slugify_project_name,
)
from postmaster.scheduler_engine import SchedulerEngine, SchedulerSettings
from postmaster import webgui_v962 as v962
from postmaster import webgui_v962_views as views
from postmaster.webgui_v962_perf import BoundedBaseProxy, cached_structural, invalidate_structural_cache


ROOT = Path(__file__).resolve().parents[1]


def _request(query: str = "") -> Request:
    return Request({
        "type": "http", "http_version": "1.1", "method": "GET", "scheme": "https",
        "path": "/", "raw_path": b"/", "query_string": query.encode("utf-8"),
        "headers": [], "client": ("127.0.0.1", 12345), "server": ("testserver", 443),
    })


def _git_blob_sha1(payload: bytes) -> str:
    header = f"blob {len(payload)}\0".encode("ascii")
    return hashlib.sha1(header + payload, usedforsecurity=False).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ProjectServiceV962Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.scheduler = SchedulerEngine(SchedulerSettings(
            db_path=str(root / "scheduler.db"), default_owner_id="owner", default_owner_name="Owner",
            seed_tinkerer_project=False, seed_tinkerer_profile=False,
        ))
        self.knowledge = KnowledgeStore(str(root / "knowledge.db"))
        self.scopes = KnowledgeScopeStore(str(root / "knowledge.db"))
        self.files = FileStore(db_path=str(root / "files.db"), root=str(root / "files"))
        with patch("postmaster.project_service.knowledge_scope_store", return_value=self.scopes):
            self.service = ProjectService(self.scheduler, self.files)
        self.service.create(owner_id="owner", project_id="project-a", name="Project A")
        self.service.create(owner_id="owner", project_id="project-b", name="Project B")

    def tearDown(self):
        self.tmp.cleanup()
        invalidate_structural_cache()

    def add_knowledge(self, item_id: str, kind: str, project_id: str = "project-a") -> None:
        with self.knowledge._lock, self.knowledge._conn() as conn:
            conn.execute(
                """
                INSERT INTO knowledge_items(
                    id,owner_id,project_id,kind,title,content,priority,always_include,enabled,
                    metadata_json,revision,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (item_id, "owner", project_id, kind, item_id, "content", 0.5, 0, 1, "{}", 1, _now(), _now()),
            )
        self.scopes.sync_primary(item_id, owner_id="owner", project_id=project_id)

    def test_slug_generation_validation_duplicate_and_edit_immutable_id(self):
        self.assertEqual(slugify_project_name("  Crème brûlée / Demo  "), "creme-brulee-demo")
        with self.assertRaisesRegex(ProjectServiceError, "lowercase letters"):
            self.service.create(owner_id="owner", project_id="bad slug", name="Bad")
        with self.assertRaisesRegex(Exception, "Project already exists"):
            self.service.create(owner_id="owner", project_id="project-a", name="Duplicate")
        result = self.service.update(project_id="project-a", name="Renamed", description="Updated")
        self.assertEqual(result["project_id"], "project-a")
        self.assertEqual(self.service.get("project-a")["name"], "Renamed")
        with self.assertRaises(ProjectServiceError):
            self.service.get("renamed")

    def test_delete_is_non_destructive_and_unassigned_is_not_global(self):
        self.add_knowledge("memory-single", "memory")
        self.add_knowledge("skill-single", "skill")
        self.add_knowledge("memory-multi", "memory")
        self.scopes.set_scopes(
            "memory-multi",
            [
                {"owner_id": "owner", "project_id": "project-a"},
                {"owner_id": "owner", "project_id": "project-b"},
            ],
            primary_owner_id="owner", primary_project_id="project-a",
        )
        stored = self.files.save_bytes(
            owner_id="owner", project_id="project-a", filename="kept.txt", data=b"kept",
            description="", tags=[],
        )
        impact = self.service.impact("project-a")
        self.assertEqual(impact["memories"], 2)
        self.assertEqual(impact["skills"], 1)
        self.assertEqual(impact["files"], 1)
        with self.assertRaisesRegex(ProjectServiceError, "exact project_id"):
            self.service.delete(project_id="project-a", confirmation="wrong")

        result = self.service.delete(project_id="project-a", confirmation="project-a")
        self.assertEqual(result["content_deleted"], 0)
        self.assertEqual(result["global_promotions"], 0)
        self.assertEqual(result["unassigned"], 2)
        self.assertFalse(bool(self.service.get("project-a", active_only=False)["active"]))

        with self.knowledge._conn() as conn:
            rows = conn.execute(
                "SELECT id,project_id FROM knowledge_items ORDER BY id"
            ).fetchall()
        self.assertEqual(len(rows), 3)
        by_id = {str(row["id"]): row["project_id"] for row in rows}
        self.assertIsNone(by_id["memory-single"])
        self.assertIsNone(by_id["skill-single"])
        self.assertEqual(by_id["memory-multi"], "project-b")
        self.assertEqual(self.scopes.scopes_for("memory-single"), [])
        self.assertEqual(self.scopes.scopes_for("skill-single"), [])
        self.assertEqual(
            [scope["project_id"] for scope in self.scopes.scopes_for("memory-multi")],
            ["project-b"],
        )
        with self.scopes._connect() as conn:
            global_rows = conn.execute(
                "SELECT COUNT(*) FROM knowledge_item_scopes WHERE item_id IN (?,?) AND project_id=''",
                ("memory-single", "skill-single"),
            ).fetchone()[0]
        self.assertEqual(global_rows, 0)
        self.assertIsNone(self.files.get_info(str(stored["id"]))["project_id"])

    def test_unassigned_semantics_survive_scope_store_restart(self):
        self.add_knowledge("memory-restart", "memory")
        self.service.delete(project_id="project-a", confirmation="project-a")
        install_project_scope_semantics()
        restarted = KnowledgeScopeStore(self.scopes.db_path)
        self.assertEqual(restarted.scopes_for("memory-restart"), [])
        with restarted._connect() as conn:
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM knowledge_item_scopes WHERE item_id=? AND project_id=''",
                    ("memory-restart",),
                ).fetchone()[0],
                0,
            )

    def test_delete_blocked_by_execution_profile_before_any_detach(self):
        self.add_knowledge("memory-blocked", "memory")
        with self.scheduler._connect() as conn:
            conn.execute(
                """
                INSERT INTO execution_profiles(id,owner_id,project_id,provider,identity,description,enabled,created_at)
                VALUES(?,?,?,?,?,?,?,?)
                """,
                ("profile-a", "owner", "project-a", "test", "identity", "", 1, _now()),
            )
        impact = self.service.impact("project-a")
        self.assertTrue(impact["blocked"])
        self.assertEqual(impact["execution_profiles"], 1)
        with self.assertRaisesRegex(ProjectDeletionBlocked, "Delete blocked"):
            self.service.delete(project_id="project-a", confirmation="project-a")
        self.assertTrue(bool(self.service.get("project-a")["active"]))
        self.assertEqual(
            [scope["project_id"] for scope in self.scopes.scopes_for("memory-blocked")],
            ["project-a"],
        )

    def test_projects_html_escapes_values_and_requires_real_second_confirmation(self):
        self.service.create(
            owner_id="owner", project_id="unsafe-project",
            name='<script>alert("x")</script>', description='<img src=x onerror=alert(1)>',
        )
        base = SimpleNamespace()
        base.scheduler = lambda: self.scheduler
        base.file_store = lambda: self.files
        base._csrf_value = lambda: "csrf"
        base._safe_call = lambda fn, *args, **kwargs: fn(*args, **kwargs)
        invalidate_structural_cache()
        with patch.object(views, "_project_service", return_value=self.service):
            html = views.render_projects(base, _request("ui_view=projects&delete_project=project-a"))
        self.assertIn("<summary>New project</summary>", html)
        self.assertIn("<summary>Delete project</summary>", html)
        self.assertIn('name="confirm_project_id"', html)
        self.assertIn("Delete permanently", html)
        self.assertIn("Cancel", html)
        self.assertIn("Unassigned", html)
        self.assertIn("never Global", html)
        self.assertNotIn('<script>alert("x")</script>', html)
        self.assertNotIn('<img src=x onerror=alert(1)>', html)
        self.assertIn("&lt;script&gt;", html)


class WebGuiPerformanceV962Tests(unittest.TestCase):
    def tearDown(self):
        invalidate_structural_cache()

    def test_initial_dashboard_is_query_free_shell_and_only_tab_is_fetched(self):
        response = v962._shell(_request("ui_view=projects"))
        html = response.body.decode("utf-8")
        self.assertIn('data-v962-loaded="0"', html)
        self.assertIn('/dashboard/view/', html)
        self.assertNotIn("Operations Dashboard", html)
        self.assertNotIn("Stored files", html)
        self.assertNotIn("Retry history", html)
        self.assertIn("load(viewFromUrl(location.href),location.href,false,true)", html)

    def test_dispatch_does_not_render_unopened_tabs(self):
        fake = object()
        request = _request("ui_view=projects")
        with patch.object(views, "render_projects", return_value='<section id="panel-projects"></section>') as projects, patch.object(
            views, "render_tracking", side_effect=AssertionError("unopened tab queried")
        ) as tracking, patch.object(
            views, "render_deliveries", side_effect=AssertionError("unopened tab queried")
        ) as deliveries:
            result = views.render_view(fake, fake, request, "projects")
        self.assertIn("panel-projects", result)
        projects.assert_called_once()
        tracking.assert_not_called()
        deliveries.assert_not_called()

    def test_stale_fragment_requests_are_aborted_and_cannot_replace_newer_result(self):
        source = v962.SCRIPT
        self.assertIn("new AbortController()", source)
        self.assertIn("controller.abort()", source)
        self.assertIn("generations.get(view) !== generation", source)
        self.assertIn("target.classList.contains('active')", source)
        self.assertIn("next.classList.add('active')", source)

    def test_collapsible_state_survives_fragment_replacement_but_filters_stay_outside(self):
        sample = '<section class="tab-panel" id="panel-x"><form class="v951-toolbar">search</form><section class="card"><h2>New item</h2><form>editor</form></section><nav>pagination</nav></section>'
        wrapped = views._wrap_section_by_heading(sample, "New item", key="new-item")
        self.assertIn('data-v962-state-key="new-item"', wrapped)
        self.assertIn("<summary>New item</summary>", wrapped)
        self.assertIn('<form class="v951-toolbar">search</form>', wrapped)
        self.assertIn("<nav>pagination</nav>", wrapped)
        self.assertIn("sessionStorage", v962.SCRIPT)

    def test_identical_structural_reads_are_coalesced(self):
        calls = 0
        lock = threading.Lock()
        start = threading.Event()

        def loader():
            nonlocal calls
            start.wait()
            with lock:
                calls += 1
            time.sleep(0.03)
            return [{"id": "one"}]

        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = [pool.submit(cached_structural, "coalesce-test", loader, ttl=5.0) for _ in range(6)]
            start.set()
            results = [future.result() for future in futures]
        self.assertEqual(calls, 1)
        self.assertEqual(results, [[{"id": "one"}]] * 6)

    def test_proxy_pushes_limits_to_sources(self):
        class Scheduler:
            def __init__(self): self.limits = []
            def list_jobs(self, **kwargs): self.limits.append(kwargs["limit"]); return []
            def list_due_jobs(self, **kwargs): self.limits.append(kwargs["limit"]); return []
        class Store:
            def __init__(self): self.limits = []
            def list_items(self, **kwargs): self.limits.append(kwargs["limit"]); return []
        class Context:
            def __init__(self): self.store = Store(); self.search_limits=[]
            def search(self, *args, **kwargs): self.search_limits.append(kwargs["limit"]); return []
        class Files:
            def __init__(self): self.limits=[]
            def list_files(self, **kwargs): self.limits.append(kwargs["limit"]); return []
        class Analytics:
            def __init__(self): self.limits=[]
            def list_deliveries(self, **kwargs): self.limits.append(kwargs["limit"]); return []
        scheduler, context, files, analytics = Scheduler(), Context(), Files(), Analytics()
        base = SimpleNamespace(
            scheduler=lambda: scheduler, context_engine=lambda: context,
            file_store=lambda: files, analytics_store=lambda: analytics,
        )
        proxy = BoundedBaseProxy(base)
        proxy.scheduler().list_jobs(limit=1000)
        proxy.scheduler().list_due_jobs(limit=1000)
        proxy.context_engine().store.list_items(limit=1000)
        proxy.context_engine().search("x", limit=1000)
        proxy.file_store().list_files(limit=1000)
        proxy.analytics_store().list_deliveries(limit=1000)
        self.assertEqual(scheduler.limits, [251, 251])
        self.assertEqual(context.store.limits, [101])
        self.assertEqual(context.search_limits, [51])
        self.assertEqual(files.limits, [101])
        self.assertEqual(analytics.limits, [201])


class ReleaseBoundaryV962Tests(unittest.TestCase):
    def test_yaml_requirements_and_database_schema_boundary(self):
        self.assertEqual(
            _git_blob_sha1((ROOT / "postmaster-mcp.yml").read_bytes()),
            "f250cc5c33cae66ffe6cd8eea8c30cb49e8203a9",
        )
        self.assertNotIn("ALTER TABLE", (ROOT / "src/postmaster/project_service.py").read_text(encoding="utf-8"))
        self.assertNotIn("CREATE TABLE", (ROOT / "src/postmaster/project_service.py").read_text(encoding="utf-8"))
        self.assertNotIn("@mcp.tool", (ROOT / "src/postmaster/project_service.py").read_text(encoding="utf-8"))

    def test_v961_safe_reader_prefetch_contract_remains_source_of_inbox_renderer(self):
        source = (ROOT / "src/postmaster/webgui_v962_views.py").read_text(encoding="utf-8")
        self.assertIn("v960.render_inbox(proxy, request)", source)
        hotfix = (ROOT / "src/postmaster/webgui_v961.py").read_text(encoding="utf-8")
        self.assertIn("min(100, page * 25 + 1)", hotfix)
        self.assertIn('inspection="full"', hotfix)
        self.assertIn('content_mode="safe"', hotfix)


if __name__ == "__main__":
    unittest.main()
