from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from postmaster.knowledge_scopes import KnowledgeScopeStore
from postmaster.knowledge_store import KnowledgeStore
from postmaster.unsubscribe import UnsubscribeError, UnsubscribeManager
from postmaster import webgui_v960


class KnowledgeScopesV960Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = str(Path(self.tmp.name) / "knowledge.db")
        self.store = KnowledgeStore(self.db)
        self.first = self.store.create_item(
            kind="memory",
            owner_id="owner-a",
            project_id="project-a",
            title="first",
            content="first content",
        )
        self.second = self.store.create_item(
            kind="skill",
            owner_id="owner-a",
            project_id="project-b",
            title="second",
            content="second content",
        )
        self.global_item = self.store.create_item(
            kind="memory",
            owner_id="owner-b",
            project_id=None,
            title="global",
            content="global content",
        )
        self.scopes = KnowledgeScopeStore(self.db)

    def tearDown(self):
        self.tmp.cleanup()

    def test_migration_backfills_legacy_primary_scope(self):
        rows = self.scopes.scopes_for(self.first["id"])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["owner_id"], "owner-a")
        self.assertEqual(rows[0]["project_id"], "project-a")
        self.assertTrue(rows[0]["is_primary"])
        global_rows = self.scopes.scopes_for(self.global_item["id"])
        self.assertEqual(global_rows[0]["project_id"], None)
        self.assertTrue(global_rows[0]["is_primary"])

    def test_multi_project_filter_is_sql_or_over_real_scope_relation(self):
        self.scopes.set_scopes(
            self.first["id"],
            [
                {"owner_id": "owner-a", "project_id": "project-a"},
                {"owner_id": "owner-a", "project_id": "project-b"},
            ],
            primary_owner_id="owner-a",
            primary_project_id="project-a",
        )
        project_b = self.scopes.item_ids_for(
            owner_id="owner-a",
            project_ids=["project-b"],
            include_global=False,
        )
        self.assertIn(self.first["id"], project_b)
        self.assertIn(self.second["id"], project_b)
        either = self.scopes.item_ids_for(
            owner_id="owner-a",
            project_ids=["project-a", "project-b"],
            include_global=False,
        )
        self.assertEqual(either, {self.first["id"], self.second["id"]})

    def test_primary_reassignment_preserves_secondary_scope(self):
        self.scopes.set_scopes(
            self.first["id"],
            [
                {"owner_id": "owner-a", "project_id": "project-a"},
                {"owner_id": "owner-a", "project_id": "project-b"},
            ],
            primary_owner_id="owner-a",
            primary_project_id="project-a",
        )
        self.store.update_item(
            self.first["id"],
            owner_id="owner-b",
            project_id="project-c",
            set_project=True,
        )
        self.scopes.sync_primary(
            self.first["id"],
            owner_id="owner-b",
            project_id="project-c",
            remove_previous_primary=True,
        )
        rows = self.scopes.scopes_for(self.first["id"])
        values = {(row["owner_id"], row["project_id"], row["is_primary"]) for row in rows}
        self.assertIn(("owner-b", "project-c", True), values)
        self.assertIn(("owner-a", "project-b", False), values)
        self.assertNotIn(("owner-a", "project-a", True), values)


class AutomaticUnsubscribeV960Tests(unittest.TestCase):
    def test_signed_delivery_token_round_trip_and_tamper_rejection(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = UnsubscribeManager(
                key_path=str(Path(tmp) / "unsubscribe.key"),
                public_base_url="https://mail.example.test",
            )
            token = manager.sign_delivery("delivery-123")
            self.assertEqual(manager.resolve(token), "delivery-123")
            url = manager.url_for_delivery("delivery-123")
            self.assertTrue(url.startswith("https://mail.example.test/unsubscribe/"))
            altered = token[:-1] + ("A" if token[-1] != "A" else "B")
            with self.assertRaises(UnsubscribeError):
                manager.resolve(altered)

    def test_automatic_unsubscribe_requires_https_public_base(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = UnsubscribeManager(
                key_path=str(Path(tmp) / "unsubscribe.key"),
                public_base_url="http://mail.example.test",
            )
            with self.assertRaisesRegex(UnsubscribeError, "HTTPS"):
                manager.url_for_delivery("delivery-123")


class WebGuiContractsV960Tests(unittest.TestCase):
    def test_progressive_enhancement_and_safe_reader_contract_markers(self):
        self.assertIn("/dashboard/inbox/fragment", webgui_v960.SCRIPT)
        self.assertIn("/dashboard/knowledge/fragment", webgui_v960.SCRIPT)
        self.assertIn("v960-message-row", webgui_v960.STYLE)
        self.assertIn("v960-unread", webgui_v960.STYLE)
        self.assertIn("v960-detail-pane", webgui_v960.STYLE)
        self.assertIn("v960-scope-chip", webgui_v960.STYLE)

    def test_compose_handler_source_uses_idempotency_and_existing_backends(self):
        names = webgui_v960.compose_send.__code__.co_names
        self.assertIn("send_email", names)
        self.assertIn("create_draft", names)
        self.assertIn("reply_email", names)
        self.assertIn("follow_up_email", names)
        constants = " ".join(str(value) for value in webgui_v960.compose_send.__code__.co_consts)
        self.assertIn("idempotency_key", constants)


if __name__ == "__main__":
    unittest.main()
