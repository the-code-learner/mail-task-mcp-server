from __future__ import annotations

import asyncio
import csv
import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from mcp.server import MCPServer
from starlette.applications import Starlette
from starlette.requests import Request

from postmaster.runtime_v980 import (
    MCP_COMMAND_COUNT_V980,
    MCP_STRUCTURED_DATA_COMMANDS_V980,
    install_runtime_v980,
)
from postmaster.structured_data_v980 import StructuredDataError, StructuredDataService
from postmaster.webgui_structured_data_v980 import (
    VIEW,
    install_webgui_structured_data_v980,
    render_data,
)


OWNER = "owner-a"
PROJECT = "project-a"
OTHER_PROJECT = "project-b"


def _columns():
    return [
        {"name": "id", "type": "text", "primary_key": True, "required": True},
        {"name": "name", "type": "text", "required": True},
        {"name": "score", "type": "integer", "default": 0},
    ]


class TrackingStructuredDataService(StructuredDataService):
    def __init__(self, *args, **kwargs):
        self.opened_connections: list[sqlite3.Connection] = []
        super().__init__(*args, **kwargs)

    def _connect(self) -> sqlite3.Connection:
        conn = super()._connect()
        self.opened_connections.append(conn)
        return conn


class StructuredDataServiceV980Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db_path = Path(self.temp.name) / "structured.db"
        self.service = StructuredDataService(self.db_path)

    def _create_people(self, project: str = PROJECT):
        return self.service.create_table(OWNER, project, "people", _columns())

    def test_status_hides_filesystem_path_and_connections_close_deterministically(self):
        tracking = TrackingStructuredDataService(Path(self.temp.name) / "tracking.db")
        status = tracking.status(OWNER, PROJECT)
        tracking.describe_project(OWNER, PROJECT)
        self.assertNotIn("path", status)
        self.assertNotIn(str(tracking.path), json.dumps(status, sort_keys=True))
        self.assertGreaterEqual(len(tracking.opened_connections), 3)
        for conn in tracking.opened_connections:
            with self.assertRaises(sqlite3.ProgrammingError):
                conn.execute("SELECT 1")

    def test_project_isolation_and_same_logical_table_do_not_mix(self):
        self._create_people(PROJECT)
        self._create_people(OTHER_PROJECT)
        self.service.insert(OWNER, PROJECT, "people", {"id": "a", "name": "Alpha"})
        self.service.insert(OWNER, OTHER_PROJECT, "people", {"id": "b", "name": "Beta"})

        first = self.service.query(OWNER, PROJECT, "people")
        second = self.service.query(OWNER, OTHER_PROJECT, "people")
        self.assertEqual([row["id"] for row in first["rows"]], ["a"])
        self.assertEqual([row["id"] for row in second["rows"]], ["b"])

    def test_idempotency_crud_upsert_and_describe_project_row_count(self):
        self._create_people()
        first = self.service.insert(
            OWNER,
            PROJECT,
            "people",
            {"id": "1", "name": "Ada", "score": 10},
            idempotency_key="insert-1",
        )
        replay = self.service.insert(
            OWNER,
            PROJECT,
            "people",
            {"id": "1", "name": "Ada", "score": 10},
            idempotency_key="insert-1",
        )
        self.assertEqual(first["row_id"], replay["row_id"])
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(self.service.query(OWNER, PROJECT, "people")["count"], 1)

        updated = self.service.update(
            OWNER, PROJECT, "people", {"id": "1"}, {"score": 11}
        )
        self.assertEqual(updated["updated"], 1)
        upserted = self.service.upsert(
            OWNER,
            PROJECT,
            "people",
            [
                {"id": "1", "name": "Ada Lovelace", "score": 12},
                {"id": "2", "name": "Grace", "score": 20},
            ],
            conflict_columns=["id"],
        )
        self.assertEqual(upserted["inserted"], 1)
        self.assertEqual(upserted["updated"], 1)
        described = self.service.describe_project(OWNER, PROJECT)
        self.assertEqual(described["tables"][0]["row_count"], 2)

    def test_overrides_keep_raw_and_effective_values_distinct(self):
        self._create_people()
        inserted = self.service.insert(
            OWNER, PROJECT, "people", {"id": "1", "name": "Raw", "score": 7}
        )
        self.service.set_override(
            OWNER,
            PROJECT,
            "people",
            inserted["row_id"],
            "name",
            "Effective",
            reason="human correction",
        )

        raw = self.service.query(OWNER, PROJECT, "people", effective=False)["rows"][0]
        effective = self.service.query(OWNER, PROJECT, "people", effective=True)["rows"][0]
        self.assertEqual(raw["name"], "Raw")
        self.assertEqual(effective["name"], "Effective")
        self.assertEqual(effective["_overrides"][0]["raw_value"], "Raw")
        self.assertEqual(effective["_overrides"][0]["value"], "Effective")

    def test_select_and_with_sql_are_project_scoped_read_only(self):
        self._create_people()
        self.service.insert(OWNER, PROJECT, "people", {"id": "1", "name": "Ada"})
        selected = self.service.query_sql_readonly(
            OWNER, PROJECT, "SELECT id,name FROM people WHERE id=?", params=["1"]
        )
        self.assertEqual(selected["rows"], [{"id": "1", "name": "Ada"}])
        with_query = self.service.query_sql_readonly(
            OWNER,
            PROJECT,
            "WITH chosen AS (SELECT id,name FROM people WHERE id=?) SELECT * FROM chosen",
            params=["1"],
        )
        self.assertEqual(with_query["rows"], [{"id": "1", "name": "Ada"}])

    def test_mutating_and_dangerous_sql_is_rejected(self):
        self._create_people()
        statements = [
            "INSERT INTO people(id,name) VALUES('2','x')",
            "UPDATE people SET name='x'",
            "DELETE FROM people",
            "DROP TABLE people",
            "ALTER TABLE people ADD COLUMN nope TEXT",
            "PRAGMA user_version=2",
            "WITH x AS (SELECT * FROM people) DELETE FROM people",
            "SELECT * FROM people; DELETE FROM people",
        ]
        for statement in statements:
            with self.subTest(statement=statement):
                with self.assertRaises(StructuredDataError):
                    self.service.query_sql_readonly(OWNER, PROJECT, statement)

    def test_json_csv_import_export_and_audit(self):
        self._create_people()
        imported_json = self.service.import_rows(
            OWNER,
            PROJECT,
            "people",
            json.dumps([{"id": "1", "name": "Ada", "score": 1}]),
            format="json",
        )
        self.assertEqual(imported_json["imported"], 1)
        imported_csv = self.service.import_rows(
            OWNER,
            PROJECT,
            "people",
            "id,name,score\n2,Grace,2\n",
            format="csv",
        )
        self.assertEqual(imported_csv["imported"], 1)

        exported_json = self.service.export(OWNER, PROJECT, table="people", format="json")
        self.assertEqual(len(json.loads(exported_json["content"])), 2)
        exported_csv = self.service.export(OWNER, PROJECT, table="people", format="csv")
        parsed = list(csv.DictReader(io.StringIO(exported_csv["content"])))
        self.assertEqual({row["id"] for row in parsed}, {"1", "2"})

        audit = self.service.audit_log(OWNER, PROJECT)
        operations = {event["operation"] for event in audit["events"]}
        self.assertIn("table.create", operations)
        self.assertIn("row.insert", operations)

    def test_delete_preview_is_real_non_mutating_then_confirmed(self):
        self._create_people()
        first = self.service.insert(OWNER, PROJECT, "people", {"id": "1", "name": "Ada"})
        self.service.insert(OWNER, PROJECT, "people", {"id": "2", "name": "Grace"})

        preview = self.service.delete(
            OWNER, PROJECT, "people", {"id": "1"}, confirm=False
        )
        self.assertFalse(preview["ok"])
        self.assertTrue(preview["approval_required"])
        self.assertEqual(preview["preview"]["matching_rows"], 1)
        self.assertEqual(preview["preview"]["sample_row_ids"], [first["row_id"]])
        self.assertEqual(self.service.query(OWNER, PROJECT, "people")["count"], 2)

        deleted = self.service.delete(
            OWNER, PROJECT, "people", {"id": "1"}, confirm=True
        )
        self.assertEqual(deleted["deleted"], 1)
        remaining = self.service.query(OWNER, PROJECT, "people")["rows"]
        self.assertEqual([row["id"] for row in remaining], ["2"])

    def test_additive_migration_applies_destructive_migration_stays_review_only(self):
        self._create_people()
        additive = self.service.create_migration(
            OWNER,
            PROJECT,
            "add nickname",
            [
                {
                    "action": "add_column",
                    "table": "people",
                    "column": {"name": "nickname", "type": "text"},
                }
            ],
            apply=True,
        )
        self.assertEqual(additive["status"], "applied")
        columns = {c["name"] for c in self.service.describe_table(OWNER, PROJECT, "people")["columns"]}
        self.assertIn("nickname", columns)

        destructive = self.service.create_migration(
            OWNER,
            PROJECT,
            "drop score",
            [{"action": "drop_column", "table": "people", "column": "score"}],
            apply=True,
            confirm_destructive=True,
        )
        self.assertTrue(destructive["destructive"])
        self.assertTrue(destructive["approval_required"])
        self.assertEqual(destructive["status"], "review")
        columns_after = {
            c["name"] for c in self.service.describe_table(OWNER, PROJECT, "people")["columns"]
        }
        self.assertIn("score", columns_after)


class RuntimeRegistryV980Tests(unittest.TestCase):
    def test_v980_registry_adds_exactly_21_tools_for_total_118(self):
        self.assertEqual(MCP_STRUCTURED_DATA_COMMANDS_V980, 21)
        self.assertEqual(MCP_COMMAND_COUNT_V980, 118)
        with tempfile.TemporaryDirectory() as tmp:
            mcp = MCPServer("v9.8.0 registry")

            def noop():
                return {"ok": True}

            for index in range(96):
                mcp.add_tool(noop, name=f"legacy_{index:03d}")

            def previous_runtime_status():
                return {"ok": True, "version_capability": "9.6.9"}

            mcp.add_tool(previous_runtime_status, name="runtime_status")

            class Scheduler:
                @staticmethod
                def list_projects(owner_id=None):
                    return [{"id": PROJECT, "owner_id": OWNER, "active": True}]

            base = SimpleNamespace(scheduler=lambda: Scheduler())
            core = SimpleNamespace(mcp=mcp)
            install_runtime_v980(
                base,
                core,
                previous_runtime_status,
                db_path=str(Path(tmp) / "registry.db"),
            )
            tools = asyncio.run(mcp.list_tools())
            names = {tool.name for tool in tools}
            self.assertEqual(len(names), 118)
            expected = {
                "db_status",
                "db_describe_project",
                "db_describe_table",
                "db_create_table",
                "db_alter_table",
                "db_query",
                "db_query_sql_readonly",
                "db_insert",
                "db_import",
                "db_upsert",
                "db_update",
                "db_delete",
                "db_create_index",
                "db_create_view",
                "db_create_migration",
                "db_list_migrations",
                "db_rollback_migration",
                "db_audit_log",
                "db_export",
                "db_set_override",
                "db_link_memory",
            }
            self.assertTrue(expected <= names)
            status = core.runtime_status()
            self.assertEqual(status["version_capability"], "9.8.0")
            self.assertEqual(status["mcp_command_count_expected"], 118)
            self.assertTrue(status["structured_data"]["project_scoped"])
            self.assertTrue(status["structured_data"]["physical_namespace_hidden"])


class WebGuiV980Tests(unittest.TestCase):
    def test_render_and_install_structured_data_control_plane(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = StructuredDataService(Path(tmp) / "webgui.db")
            service.create_table(OWNER, PROJECT, "people", _columns())

            class Scheduler:
                @staticmethod
                def list_projects(owner_id=None):
                    return [
                        {
                            "id": PROJECT,
                            "owner_id": OWNER,
                            "name": "Project A",
                            "active": True,
                        }
                    ]

            base = SimpleNamespace(
                scheduler=lambda: Scheduler(),
                structured_data_service=lambda: service,
                _safe_call=lambda fn: fn(),
                _csrf_value=lambda: "csrf-test",
            )
            request = Request(
                {
                    "type": "http",
                    "method": "GET",
                    "path": "/",
                    "headers": [],
                    "query_string": f"project={PROJECT}&data_table=people".encode(),
                    "server": ("testserver", 80),
                    "scheme": "http",
                }
            )
            html = render_data(base, request)
            self.assertIn("Structured Data", html)
            self.assertIn("Schema explorer", html)
            self.assertIn("Approval inbox", html)
            self.assertIn("Activity &amp; provenance", html)
            self.assertIn("people", html)

            app = Starlette()
            install_webgui_structured_data_v980(app, base)
            paths = {getattr(route, "path", "") for route in app.router.routes}
            self.assertIn("/dashboard/data/import", paths)
            self.assertIn("/dashboard/data/migration/create", paths)
            self.assertEqual(VIEW, "data")


if __name__ == "__main__":
    unittest.main()
