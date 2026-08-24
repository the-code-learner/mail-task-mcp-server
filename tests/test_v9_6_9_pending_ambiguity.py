from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from mcp import Client
from mcp.server import MCPServer

from postmaster import runtime_control
from postmaster.pending_approval_v969 import (
    AmbiguousPendingPreviewError,
    PendingApprovalStore,
    PendingConfirmationAdapter,
)
from postmaster.runtime_v969 import install_runtime_v969_mcp


def _payload(result):
    if isinstance(result.structured_content, dict):
        return dict(result.structured_content)
    for block in result.content:
        text = getattr(block, "text", None)
        if isinstance(text, str):
            try:
                value = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                return value
    raise AssertionError(result)


class PendingApprovalAmbiguityV969Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db_path = Path(self.temp.name) / "pending.db"
        self.now = [1000.0]
        self.scope = "runtime_version_change"
        self.binding = {
            "operation": "pin-version",
            "target": "v9.6.8",
            "current_selector": "latest",
            "current_build": "v9.6.9-test",
            "current_version": "9.6.9",
        }

    def store(self):
        return PendingApprovalStore(
            self.db_path,
            ttl_seconds=300,
            clock=lambda: self.now[0],
        )

    def _consumed_at(self, preview_id: str):
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT consumed_at
                FROM mcp_pending_approvals_v969
                WHERE preview_id=?
                """,
                (preview_id,),
            ).fetchone()
        return None if row is None else row[0]

    def test_unique_implicit_match_consumes_once_and_replay_rejects(self):
        store = self.store()
        preview_id = store.issue(self.scope, self.binding)

        self.assertTrue(store.consume_matching(self.scope, self.binding))
        self.assertIsNotNone(self._consumed_at(preview_id))
        self.assertFalse(store.consume_matching(self.scope, self.binding))

    def test_duplicate_identical_implicit_match_is_ambiguous_and_consumes_none(self):
        store = self.store()
        p1 = store.issue(self.scope, self.binding)
        p2 = store.issue(self.scope, self.binding)
        self.assertNotEqual(p1, p2)

        with self.assertRaises(AmbiguousPendingPreviewError) as caught:
            store.consume_matching(self.scope, self.binding)

        self.assertIn("ambiguous_pending_preview", str(caught.exception))
        self.assertIn("multiple matching pending previews exist", str(caught.exception))
        self.assertIsNone(self._consumed_at(p1))
        self.assertIsNone(self._consumed_at(p2))
        self.assertEqual(store.pending_count(self.scope), 2)

    def test_exact_preview_selection_replay_and_second_preview(self):
        store = self.store()
        p1 = store.issue(self.scope, self.binding)
        p2 = store.issue(self.scope, self.binding)

        self.assertTrue(
            store.consume_matching(self.scope, self.binding, preview_id=p1)
        )
        self.assertIsNotNone(self._consumed_at(p1))
        self.assertIsNone(self._consumed_at(p2))

        self.assertFalse(
            store.consume_matching(self.scope, self.binding, preview_id=p1)
        )
        self.assertTrue(
            store.consume_matching(self.scope, self.binding, preview_id=p2)
        )
        self.assertIsNotNone(self._consumed_at(p2))

    def test_valid_preview_id_with_wrong_binding_rejects_without_consuming(self):
        store = self.store()
        p1 = store.issue(self.scope, self.binding)
        wrong = dict(self.binding, target="v9.6.7")

        self.assertFalse(
            store.consume_matching(self.scope, wrong, preview_id=p1)
        )
        self.assertIsNone(self._consumed_at(p1))

    def test_expired_exact_preview_rejects(self):
        store = self.store()
        p1 = store.issue(self.scope, self.binding)
        self.now[0] = 1301.0

        self.assertFalse(
            store.consume_matching(self.scope, self.binding, preview_id=p1)
        )

    def test_duplicate_rows_with_only_one_valid_pending_row_use_unique_match(self):
        store = self.store()
        p1 = store.issue(self.scope, self.binding)
        p2 = store.issue(self.scope, self.binding)

        self.assertTrue(
            store.consume_matching(self.scope, self.binding, preview_id=p1)
        )
        self.assertTrue(store.consume_matching(self.scope, self.binding))
        self.assertIsNotNone(self._consumed_at(p1))
        self.assertIsNotNone(self._consumed_at(p2))

    def test_different_bindings_are_not_ambiguous(self):
        store = self.store()
        b1 = dict(self.binding, target="v9.6.8")
        b2 = dict(self.binding, target="v9.6.7")
        p1 = store.issue(self.scope, b1)
        p2 = store.issue(self.scope, b2)

        self.assertTrue(store.consume_matching(self.scope, b1))
        self.assertIsNotNone(self._consumed_at(p1))
        self.assertIsNone(self._consumed_at(p2))

    def test_restart_preserves_unique_and_ambiguous_resolution(self):
        first = self.store()
        unique = first.issue(self.scope, self.binding)

        restarted = self.store()
        self.assertTrue(restarted.consume_matching(self.scope, self.binding))
        self.assertIsNotNone(self._consumed_at(unique))

        p1 = restarted.issue(self.scope, self.binding)
        p2 = restarted.issue(self.scope, self.binding)
        restarted_again = self.store()
        with self.assertRaises(AmbiguousPendingPreviewError):
            restarted_again.consume_matching(self.scope, self.binding)
        self.assertIsNone(self._consumed_at(p1))
        self.assertIsNone(self._consumed_at(p2))

    def test_compatibility_adapter_has_same_ambiguity_semantics(self):
        store = self.store()
        state = {"build": "stable"}
        adapter = PendingConfirmationAdapter(
            store,
            scope="privacy_proxy_provisioning",
            state_provider=lambda: dict(state),
        )
        binding = {
            "action": "prepare_provisioning",
            "worker_url": "https://worker.example",
        }
        p1 = adapter.issue(binding)
        p2 = adapter.issue(binding)

        with self.assertRaises(AmbiguousPendingPreviewError):
            adapter.consume(None, binding)

        self.assertTrue(adapter.consume(p1, binding))
        self.assertFalse(adapter.consume(p1, binding))
        self.assertTrue(adapter.consume(p2, binding))

    def test_two_concurrent_implicit_consumes_are_atomic_one_shot(self):
        issuer = self.store()
        issuer.issue(self.scope, self.binding)
        contenders = [self.store(), self.store()]
        barrier = threading.Barrier(2)

        def attempt(contender):
            barrier.wait()
            return contender.consume_matching(self.scope, self.binding)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, contenders))

        self.assertEqual(results.count(True), 1)
        self.assertEqual(results.count(False), 1)
        self.assertEqual(issuer.pending_count(self.scope), 0)


class RuntimeLifecycleAmbiguityV969Tests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.control = root / "runtime-control.json"
        self.env = patch.dict(
            os.environ,
            {"POSTMASTER_RUNTIME_CONTROL_PATH": str(self.control)},
            clear=False,
        )
        self.env.start()
        self.addCleanup(self.env.stop)

        self.state = {
            "ok": True,
            "version": "9.6.8",
            "build": "v9.6.8-test",
            "requested_version": "latest",
        }
        self.base = SimpleNamespace(
            passive_content_service_v969=lambda: SimpleNamespace()
        )
        self.core = SimpleNamespace(mcp=MCPServer("v9.6.9 ambiguity test"))
        for name in (
            "runtime_status",
            "runtime_version_change_preview",
            "runtime_version_change_execute",
            "privacy_proxy_provisioning_preview",
            "privacy_proxy_provisioning_execute",
            "privacy_proxy_status",
        ):
            self.core.mcp.add_tool(lambda: {"ok": True}, name=name)

        self.releases = patch.object(
            runtime_control,
            "stable_release_tags",
            return_value=(["v9.6.9", "v9.6.8", "v9.6.7"], "ok"),
        )
        self.releases.start()
        self.addCleanup(self.releases.stop)

        install_runtime_v969_mcp(
            self.base,
            self.core,
            lambda: dict(self.state),
            pending_db_path=str(root / "pending.db"),
        )

    async def _preview(self, client):
        return _payload(
            await client.call_tool(
                "runtime_version_change_preview",
                {"operation": "pin-version", "target_version": "v9.6.7"},
            )
        )

    async def test_public_lifecycle_unique_implicit_then_ambiguous_then_exact(self):
        with patch.object(
            runtime_control,
            "schedule_current_process_termination",
        ) as restart:
            async with Client(self.core.mcp) as client:
                unique = await self._preview(client)
                self.assertTrue(unique["ok"])
                self.assertFalse(unique["preview_id_is_authorization"])

                implicit = _payload(
                    await client.call_tool(
                        "runtime_version_change_execute",
                        {"operation": "pin-version", "target_version": "v9.6.7"},
                    )
                )
                self.assertTrue(implicit["ok"])

                p1 = await self._preview(client)
                p2 = await self._preview(client)
                self.assertNotEqual(p1["preview_id"], p2["preview_id"])

                pending = self.base.pending_approval_store_v969()
                self.assertEqual(
                    pending.pending_count("runtime_version_change"),
                    2,
                )

                ambiguous = _payload(
                    await client.call_tool(
                        "runtime_version_change_execute",
                        {"operation": "pin-version", "target_version": "v9.6.7"},
                    )
                )
                self.assertFalse(ambiguous["ok"])
                self.assertTrue(ambiguous["approval_required"])
                self.assertFalse(ambiguous["version_change_applied"])
                self.assertIn("ambiguous_pending_preview", ambiguous["error"])
                self.assertIn(
                    "multiple matching pending previews exist",
                    ambiguous["error"],
                )
                self.assertEqual(
                    pending.pending_count("runtime_version_change"),
                    2,
                )

                exact_p1 = _payload(
                    await client.call_tool(
                        "runtime_version_change_execute",
                        {
                            "operation": "pin-version",
                            "target_version": "v9.6.7",
                            "preview_id": p1["preview_id"],
                        },
                    )
                )
                self.assertTrue(exact_p1["ok"])
                self.assertEqual(
                    pending.pending_count("runtime_version_change"),
                    1,
                )

                replay_p1 = _payload(
                    await client.call_tool(
                        "runtime_version_change_execute",
                        {
                            "operation": "pin-version",
                            "target_version": "v9.6.7",
                            "preview_id": p1["preview_id"],
                        },
                    )
                )
                self.assertFalse(replay_p1["ok"])
                self.assertEqual(
                    pending.pending_count("runtime_version_change"),
                    1,
                )

                exact_p2 = _payload(
                    await client.call_tool(
                        "runtime_version_change_execute",
                        {
                            "operation": "pin-version",
                            "target_version": "v9.6.7",
                            "preview_id": p2["preview_id"],
                        },
                    )
                )
                self.assertTrue(exact_p2["ok"])
                self.assertEqual(
                    pending.pending_count("runtime_version_change"),
                    0,
                )

        self.assertEqual(restart.call_count, 3)


if __name__ == "__main__":
    unittest.main()
