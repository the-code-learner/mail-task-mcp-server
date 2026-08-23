from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from mcp import Client
from mcp.server import MCPServer

from postmaster import runtime_control
from postmaster.confirmation_v967 import (
    CONFIRMATION_TTL_SECONDS,
    PersistentConfirmationTokens,
)
from postmaster.email_privacy_v963 import PrivacyProxyStore
from postmaster.privacy_provisioning_v966 import PrivacyProxyProvisioning
from postmaster.runtime_v967 import (
    MCP_COMMAND_COUNT_V967,
    configure_privacy_proxy_confirmations,
    install_runtime_v967,
)


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_SINGLE_YAML_BLOB = "f250cc5c33cae66ffe6cd8eea8c30cb49e8203a9"
EXPECTED_V967_TOOLS = {
    "runtime_status",
    "runtime_version_change_preview",
    "runtime_version_change_execute",
    "privacy_proxy_status",
    "privacy_proxy_provisioning_preview",
    "privacy_proxy_provisioning_execute",
}
STABLE_RELEASES = ["v9.6.7", "v9.6.6", "v9.6.5"]


def _result_payload(result):
    if isinstance(result.structured_content, dict):
        return dict(result.structured_content)
    for block in result.content:
        text = getattr(block, "text", None)
        if not isinstance(text, str):
            continue
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise AssertionError(f"tool did not return a JSON object: {result!r}")


def _catalog_snapshot(listed) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for tool in listed.tools:
        annotations = tool.annotations
        result[tool.name] = {
            "input_schema": tool.input_schema,
            "annotations": None if annotations is None else annotations.model_dump(),
            "description": tool.description,
        }
    return result


def _blob_sha(path: Path) -> str:
    payload = path.read_bytes()
    return hashlib.sha1(
        f"blob {len(payload)}\0".encode("ascii") + payload,
        usedforsecurity=False,
    ).hexdigest()


class PersistentConfirmationV967Tests(unittest.TestCase):
    def test_stateless_token_survives_service_restart_and_replay_is_persistent(self):
        with tempfile.TemporaryDirectory() as temp:
            key_path = Path(temp) / "confirmation.key"
            db_path = Path(temp) / "confirmation.db"
            first = PersistentConfirmationTokens(
                scope="runtime_version_change",
                key_path=key_path,
                db_path=db_path,
            )
            binding = {
                "operation": "pin-version",
                "target": "v9.6.6",
                "current_selector": "latest",
                "current_build": "v9.6.7",
            }
            token = first.issue(binding)
            self.assertEqual(first.consumed_count(), 0)

            restarted = PersistentConfirmationTokens(
                scope="runtime_version_change",
                key_path=key_path,
                db_path=db_path,
            )
            self.assertTrue(restarted.consume(token, binding))
            self.assertEqual(restarted.consumed_count(), 1)

            restarted_again = PersistentConfirmationTokens(
                scope="runtime_version_change",
                key_path=key_path,
                db_path=db_path,
            )
            self.assertFalse(restarted_again.consume(token, binding))

    def test_mismatch_consumes_nonce_and_token_contains_only_digest_not_binding_secrets(self):
        with tempfile.TemporaryDirectory() as temp:
            tokens = PersistentConfirmationTokens(
                scope="privacy_proxy_provisioning",
                key_path=Path(temp) / "confirmation.key",
                db_path=Path(temp) / "confirmation.db",
            )
            binding = {
                "action": "provision",
                "worker_origin": "https://worker.example",
                "shared_secret": "must-never-appear",
                "private_key": "must-never-appear-either",
            }
            token = tokens.issue(binding)
            encoded_payload = token.split(".", 2)[1]
            payload = json.loads(
                base64.urlsafe_b64decode(
                    encoded_payload + "=" * (-len(encoded_payload) % 4)
                ).decode("utf-8")
            )
            self.assertEqual(
                set(payload),
                {"v", "scope", "nonce", "iat", "exp", "binding"},
            )
            self.assertNotIn("must-never-appear", token)
            self.assertNotIn("must-never-appear-either", token)

            self.assertFalse(tokens.consume(token, {**binding, "worker_origin": "https://other.example"}))
            self.assertEqual(tokens.consumed_count(), 1)
            self.assertFalse(tokens.consume(token, binding))

    def test_expiry_target_is_300_seconds(self):
        now = [1_000.0]
        with tempfile.TemporaryDirectory() as temp:
            key_path = Path(temp) / "confirmation.key"
            db_path = Path(temp) / "confirmation.db"
            tokens = PersistentConfirmationTokens(
                scope="runtime_version_change",
                key_path=key_path,
                db_path=db_path,
                clock=lambda: now[0],
            )
            self.assertEqual(tokens.ttl_seconds, 300)
            self.assertEqual(CONFIRMATION_TTL_SECONDS, 300)
            token = tokens.issue({"operation": "pin-version"})
            now[0] = 1_301.0
            restarted = PersistentConfirmationTokens(
                scope="runtime_version_change",
                key_path=key_path,
                db_path=db_path,
                clock=lambda: now[0],
            )
            self.assertFalse(restarted.consume(token, {"operation": "pin-version"}))
            self.assertEqual(restarted.consumed_count(), 0)


class McpLifecycleV967Tests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.control_path = root / "runtime-control.json"
        self.confirmation_key_path = root / "mcp-confirmation.key"
        self.confirmation_db_path = root / "mcp-confirmation.db"
        self.env = patch.dict(
            os.environ,
            {"POSTMASTER_RUNTIME_CONTROL_PATH": str(self.control_path)},
            clear=False,
        )
        self.env.start()
        self.addCleanup(self.env.stop)

        self.runtime_state = {
            "ok": True,
            "version": "9.6.7",
            "build": "v9.6.7-test",
            "requested_version": "latest",
        }

        def legacy_build_status(operation="status", **_kwargs):
            return dict(self.runtime_state)

        self.legacy_build_status = legacy_build_status
        self.store = PrivacyProxyStore(str(root / "proxy.db"), str(root / "proxy.key"))
        self.provisioning = PrivacyProxyProvisioning(self.store)
        self.core = SimpleNamespace(mcp=MCPServer("Postmaster v9.6.7 lifecycle test"))
        self.base = SimpleNamespace()
        self.release_patch = patch.object(
            runtime_control,
            "stable_release_tags",
            return_value=(list(STABLE_RELEASES), "ok"),
        )
        self.release_patch.start()
        self.addCleanup(self.release_patch.stop)
        install_runtime_v967(
            self.base,
            self.core,
            self.legacy_build_status,
            provisioning_service=self.provisioning,
            confirmation_key_path=str(self.confirmation_key_path),
            confirmation_db_path=str(self.confirmation_db_path),
            replace_runtime_confirmation_backend=True,
        )
        self.runtime_tokens = runtime_control.initialize_version_change_approvals()
        self.privacy_tokens = self.base.privacy_proxy_confirmation_tokens_v967

    async def _list_snapshot(self):
        async with Client(self.core.mcp) as client:
            return _catalog_snapshot(await client.list_tools())

    async def test_tool_schema_and_annotations_are_stable_across_disconnect_reconnect(self):
        first = await self._list_snapshot()
        second = await self._list_snapshot()
        self.assertEqual(first, second)
        self.assertEqual(set(first), EXPECTED_V967_TOOLS)

        for name in (
            "runtime_status",
            "runtime_version_change_preview",
            "privacy_proxy_status",
            "privacy_proxy_provisioning_preview",
        ):
            annotations = first[name]["annotations"]
            self.assertTrue(annotations["read_only_hint"])
            self.assertFalse(annotations["destructive_hint"])
            self.assertTrue(annotations["idempotent_hint"])
        self.assertFalse(first["runtime_status"]["annotations"]["open_world_hint"])
        self.assertTrue(first["runtime_version_change_preview"]["annotations"]["open_world_hint"])
        self.assertFalse(first["privacy_proxy_status"]["annotations"]["open_world_hint"])
        self.assertFalse(first["privacy_proxy_provisioning_preview"]["annotations"]["open_world_hint"])

        for name in ("runtime_version_change_execute", "privacy_proxy_provisioning_execute"):
            annotations = first[name]["annotations"]
            self.assertFalse(annotations["read_only_hint"])
            self.assertTrue(annotations["destructive_hint"])
            self.assertFalse(annotations["idempotent_hint"])
            self.assertTrue(annotations["open_world_hint"])

    async def test_runtime_connect_preview_reconnect_execute_status_and_replay(self):
        self.assertFalse(self.control_path.exists())
        self.assertEqual(self.runtime_tokens.consumed_count(), 0)

        async with Client(self.core.mcp) as first_client:
            before = _catalog_snapshot(await first_client.list_tools())
            preview = _result_payload(
                await first_client.call_tool(
                    "runtime_version_change_preview",
                    {"operation": "pin-version", "target_version": "v9.6.6"},
                )
            )
        self.assertTrue(preview["ok"])
        self.assertTrue(preview["approval_required"])
        self.assertFalse(preview["version_change_applied"])
        self.assertEqual(preview["action_preview"]["target_version_ref"], "v9.6.6")
        self.assertEqual(preview["confirmation_expires_in_seconds"], 300)
        token = preview["confirmation_token"]
        self.assertFalse(self.control_path.exists(), "runtime preview must not persist runtime control")
        self.assertEqual(self.runtime_tokens.consumed_count(), 0, "preview must not persist nonce state")

        self.runtime_tokens = runtime_control.initialize_version_change_approvals(
            key_path=self.confirmation_key_path,
            db_path=self.confirmation_db_path,
            replace=True,
        )
        with patch.object(runtime_control, "schedule_current_process_termination") as restart:
            async with Client(self.core.mcp) as second_client:
                after = _catalog_snapshot(await second_client.list_tools())
                execute = _result_payload(
                    await second_client.call_tool(
                        "runtime_version_change_execute",
                        {
                            "operation": "pin-version",
                            "target_version": "v9.6.6",
                            "confirmation_token": token,
                        },
                    )
                )
                status = _result_payload(await second_client.call_tool("runtime_status", {}))
                replay = _result_payload(
                    await second_client.call_tool(
                        "runtime_version_change_execute",
                        {
                            "operation": "pin-version",
                            "target_version": "v9.6.6",
                            "confirmation_token": token,
                        },
                    )
                )
        self.assertEqual(before, after)
        self.assertTrue(execute["ok"])
        self.assertTrue(execute["version_change_applied"])
        self.assertEqual(runtime_control.read_control(self.control_path), {"selector": "v9.6.6"})
        self.assertEqual(status["runtime_selector"], "v9.6.6")
        self.assertEqual(status["mcp_command_count_expected"], 96)
        self.assertEqual(self.runtime_tokens.consumed_count(), 1)
        self.assertFalse(replay["ok"])
        self.assertTrue(replay["approval_required"])
        restart.assert_called_once_with()

    async def test_runtime_token_is_bound_to_current_selector_build_and_exact_target(self):
        runtime_control.write_control(selector="latest", path=self.control_path)
        async with Client(self.core.mcp) as client:
            preview = _result_payload(
                await client.call_tool(
                    "runtime_version_change_preview",
                    {"operation": "switch-version", "target_version": "v9.6.6"},
                )
            )
        token = preview["confirmation_token"]
        self.runtime_state["build"] = "v9.6.7-different-build"
        runtime_control.write_control(selector="v9.6.7", path=self.control_path)
        with patch.object(runtime_control, "schedule_current_process_termination") as restart:
            async with Client(self.core.mcp) as client:
                changed_state = _result_payload(
                    await client.call_tool(
                        "runtime_version_change_execute",
                        {
                            "operation": "switch-version",
                            "target_version": "v9.6.6",
                            "confirmation_token": token,
                        },
                    )
                )
        self.assertFalse(changed_state["ok"])
        self.assertFalse(changed_state["version_change_applied"])
        restart.assert_not_called()
        self.assertEqual(self.runtime_tokens.consumed_count(), 1)

    async def test_update_latest_binds_exact_release_with_one_shot_restart_ref(self):
        async with Client(self.core.mcp) as first_client:
            preview = _result_payload(
                await first_client.call_tool(
                    "runtime_version_change_preview",
                    {"operation": "update-latest"},
                )
            )
        self.assertEqual(preview["action_preview"]["target_version_ref"], "v9.6.7")
        token = preview["confirmation_token"]
        with patch.object(runtime_control, "schedule_current_process_termination"):
            async with Client(self.core.mcp) as second_client:
                execute = _result_payload(
                    await second_client.call_tool(
                        "runtime_version_change_execute",
                        {
                            "operation": "update-latest",
                            "target_version": "v9.6.7",
                            "confirmation_token": token,
                        },
                    )
                )
        self.assertTrue(execute["ok"])
        self.assertEqual(
            runtime_control.read_control(self.control_path),
            {"selector": "latest", "restart_ref_once": "v9.6.7"},
        )

    async def test_privacy_proxy_prepare_and_provision_survive_reconnect_and_backend_restart(self):
        initial_proxy = self.store.status()
        initial_provisioning = self.provisioning.public_status()
        self.assertEqual(self.privacy_tokens.consumed_count(), 0)

        async with Client(self.core.mcp) as first_client:
            first_catalog = _catalog_snapshot(await first_client.list_tools())
            prepare_preview = _result_payload(
                await first_client.call_tool(
                    "privacy_proxy_provisioning_preview",
                    {"action": "prepare_provisioning", "worker_url": "https://worker.example"},
                )
            )
        self.assertTrue(prepare_preview["ok"])
        self.assertTrue(prepare_preview["approval_required"])
        self.assertEqual(self.store.status(), initial_proxy)
        self.assertEqual(self.provisioning.public_status(), initial_provisioning)
        self.assertEqual(self.privacy_tokens.consumed_count(), 0)
        prepare_token = prepare_preview["confirmation_token"]

        self.privacy_tokens = configure_privacy_proxy_confirmations(
            self.provisioning,
            key_path=str(self.confirmation_key_path),
            db_path=str(self.confirmation_db_path),
        )
        async with Client(self.core.mcp) as second_client:
            second_catalog = _catalog_snapshot(await second_client.list_tools())
            prepared = _result_payload(
                await second_client.call_tool(
                    "privacy_proxy_provisioning_execute",
                    {
                        "action": "prepare_provisioning",
                        "worker_url": "https://worker.example",
                        "confirmation_token": prepare_token,
                    },
                )
            )
            provision_preview = _result_payload(
                await second_client.call_tool(
                    "privacy_proxy_provisioning_preview",
                    {"action": "provision"},
                )
            )
        self.assertTrue(prepared["ok"])
        self.assertTrue(prepared["privacy_proxy_provisioning"]["prepared"])
        self.assertNotIn("private_key", repr(prepared))
        self.assertNotIn("secret_value", repr(prepared))
        self.assertTrue(provision_preview["ok"])
        self.assertEqual(self.privacy_tokens.consumed_count(), 1)
        provision_token = provision_preview["confirmation_token"]

        self.privacy_tokens = configure_privacy_proxy_confirmations(
            self.provisioning,
            key_path=str(self.confirmation_key_path),
            db_path=str(self.confirmation_db_path),
        )
        with patch.object(
            self.provisioning,
            "_post_provision",
            return_value=(True, 204, ""),
        ), patch.object(
            self.provisioning,
            "_verify_health",
            return_value=(True, 200),
        ):
            async with Client(self.core.mcp) as third_client:
                third_catalog = _catalog_snapshot(await third_client.list_tools())
                provisioned = _result_payload(
                    await third_client.call_tool(
                        "privacy_proxy_provisioning_execute",
                        {"action": "provision", "confirmation_token": provision_token},
                    )
                )
                status = _result_payload(await third_client.call_tool("privacy_proxy_status", {}))
                replay_result = await third_client.call_tool(
                    "privacy_proxy_provisioning_execute",
                    {"action": "provision", "confirmation_token": provision_token},
                )
        self.assertEqual(first_catalog, second_catalog)
        self.assertEqual(second_catalog, third_catalog)
        self.assertTrue(provisioned["ok"])
        self.assertTrue(status["privacy_proxy"]["secret_configured"])
        self.assertTrue(status["privacy_proxy_provisioning"]["provisioned"])
        self.assertTrue(replay_result.is_error or self.privacy_tokens.consumed_count() == 2)
        self.assertEqual(self.privacy_tokens.consumed_count(), 2)
        for value in (prepare_preview, prepared, provision_preview, provisioned, status):
            rendered = repr(value)
            self.assertNotIn("private_key_enc", rendered)
            self.assertNotIn("pending_secret", rendered)
            self.assertNotIn("secret_value", rendered)

    async def test_privacy_proxy_token_is_bound_to_worker_and_current_state(self):
        async with Client(self.core.mcp) as first_client:
            preview = _result_payload(
                await first_client.call_tool(
                    "privacy_proxy_provisioning_preview",
                    {"action": "prepare_provisioning", "worker_url": "https://worker.example"},
                )
            )
        token = preview["confirmation_token"]
        self.store.configure(worker_url="https://changed.example")
        async with Client(self.core.mcp) as second_client:
            changed = _result_payload(
                await second_client.call_tool(
                    "privacy_proxy_provisioning_execute",
                    {
                        "action": "prepare_provisioning",
                        "worker_url": "https://worker.example",
                        "confirmation_token": token,
                    },
                )
            )
        self.assertFalse(changed["ok"])
        self.assertEqual(self.store.status()["worker_url"], "https://changed.example")
        self.assertEqual(self.privacy_tokens.consumed_count(), 1)


class ReleaseBoundaryV967Tests(unittest.TestCase):
    def test_composed_runtime_has_96_names_and_six_new_lifecycle_commands(self):
        import postmaster.runtime as runtime

        tools = asyncio.run(runtime.mcp.list_tools())
        by_name = {tool.name: tool for tool in tools}
        self.assertEqual(len(by_name), MCP_COMMAND_COUNT_V967)
        self.assertEqual(EXPECTED_V967_TOOLS - set(by_name), set())
        self.assertIn("privacy_proxy_action", by_name["set_amp_account_state"].input_schema["properties"])
        self.assertIn("confirm_version_change", by_name["build_status"].input_schema["properties"])

    def test_v967_layer_does_not_remove_or_reregister_legacy_tool_names(self):
        source = (ROOT / "src/postmaster/runtime_v967.py").read_text(encoding="utf-8")
        self.assertNotIn("remove_tool(", source)
        self.assertNotIn('name="build_status"', source)
        self.assertNotIn('name="set_amp_account_state"', source)

    def test_release_metadata_requirements_and_single_yaml_boundary(self):
        self.assertEqual((ROOT / "VERSION").read_text().strip(), "9.6.7")
        self.assertIn("## 9.6.7 - 2026-08-23", (ROOT / "CHANGELOG.md").read_text())
        self.assertEqual(_blob_sha(ROOT / "postmaster-mcp.yml"), EXPECTED_SINGLE_YAML_BLOB)
        requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        self.assertNotIn("jwt", requirements.casefold())
        self.assertNotIn("pyjwt", requirements.casefold())


if __name__ == "__main__":
    unittest.main()
