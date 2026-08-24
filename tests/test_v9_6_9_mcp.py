from __future__ import annotations

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
from postmaster.runtime_v969 import MCP_COMMAND_COUNT_V969, install_runtime_v969_mcp


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


class _Service:
    def __init__(self):
        self.fetch_calls = []
        self.cache_calls = []

    def fetch_message(self, **kwargs):
        self.fetch_calls.append(dict(kwargs))
        return {
            "ok": True,
            "render_state": "success",
            "full_html_available": True,
            "rendered_html": '<img src="/dashboard/inbox/resource?key=r_cached">',
            "cache_only": False,
            "refresh": bool(kwargs.get("refresh")),
            "network_requests_performed": 1,
            "diagnostics": {
                "passive_discovered": 1,
                "discovered": 1,
                "genuine_attempted": 1,
                "genuine_succeeded": 1,
                "genuine_failed": 0,
                "cache_hits": 0,
                "negative_cache_hits": 0,
                "cached_succeeded": 1,
                "cached_failed": 0,
                "decoy_attempted": 0,
                "decoy_succeeded": 0,
                "decoy_failed": 0,
                "excluded_action_urls": 0,
                "excluded_navigation_action": 0,
            },
        }

    def render_cached_message(self, **kwargs):
        self.cache_calls.append(dict(kwargs))
        return {
            "ok": True,
            "render_state": "success",
            "full_html_available": True,
            "rendered_html": '<img src="/dashboard/inbox/resource?key=r_cached">',
            "cache_only": True,
            "refresh": False,
            "network_requests_performed": 0,
            "diagnostics": {
                "passive_discovered": 1,
                "discovered": 1,
                "genuine_attempted": 0,
                "genuine_succeeded": 0,
                "genuine_failed": 0,
                "cache_hits": 1,
                "negative_cache_hits": 0,
                "cached_succeeded": 1,
                "cached_failed": 0,
                "decoy_attempted": 0,
                "decoy_succeeded": 0,
                "decoy_failed": 0,
                "excluded_action_urls": 0,
                "excluded_navigation_action": 0,
            },
        }


class McpV969Tests(unittest.IsolatedAsyncioTestCase):
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
        self.service = _Service()
        self.base = SimpleNamespace(passive_content_service_v969=lambda: self.service)
        self.core = SimpleNamespace(mcp=MCPServer("v9.6.9 test"))
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

    async def test_command_count_schema_explicit_fetch_and_cache_only_full_html(self):
        async with Client(self.core.mcp) as client:
            listed = await client.list_tools()
            by_name = {tool.name: tool for tool in listed.tools}
            self.assertEqual(len(by_name), 7)
            self.assertEqual(MCP_COMMAND_COUNT_V969, 97)
            self.assertIn("fetch_email_remote_content", by_name)
            fetch_props = by_name["fetch_email_remote_content"].input_schema["properties"]
            self.assertIn("authorize_remote_fetch", fetch_props)
            self.assertIn("refresh", fetch_props)
            self.assertIn("cache_only", fetch_props)

            execute_props = by_name["runtime_version_change_execute"].input_schema[
                "properties"
            ]
            self.assertIn("preview_id", execute_props)
            self.assertNotIn("confirmation_token", execute_props)
            proxy_execute_props = by_name[
                "privacy_proxy_provisioning_execute"
            ].input_schema["properties"]
            self.assertIn("preview_id", proxy_execute_props)
            self.assertNotIn("confirmation_token", proxy_execute_props)

            safe = _payload(
                await client.call_tool(
                    "fetch_email_remote_content",
                    {"mailbox": "INBOX", "uid": "7", "account_id": "acct"},
                )
            )
            self.assertTrue(safe["approval_required"])
            self.assertFalse(safe["remote_fetch_performed"])
            self.assertEqual(safe["network_requests_performed"], 0)
            self.assertEqual(self.service.fetch_calls, [])
            self.assertEqual(self.service.cache_calls, [])

            fetched = _payload(
                await client.call_tool(
                    "fetch_email_remote_content",
                    {
                        "mailbox": "INBOX",
                        "uid": "7",
                        "account_id": "acct",
                        "authorize_remote_fetch": True,
                    },
                )
            )
            self.assertTrue(fetched["ok"])
            self.assertTrue(fetched["remote_fetch_performed"])
            self.assertTrue(fetched["full_html_available"])
            self.assertIn("/dashboard/inbox/resource?", fetched["rendered_html"])
            self.assertEqual(fetched["network_requests_performed"], 1)
            self.assertFalse(fetched["navigation_action_urls_auto_fetched"])
            self.assertEqual(fetched["diagnostics"]["decoy_attempted"], 0)
            contract = fetched["cached_resource_contract"]
            self.assertEqual(contract["representation"], "postmaster-local")
            self.assertEqual(
                contract["reference_prefix"], "/dashboard/inbox/resource?key="
            )
            self.assertFalse(contract["resource_bytes_embedded"])
            self.assertTrue(contract["bounded_css_nested_resources"])
            self.assertEqual(len(self.service.fetch_calls), 1)

            cached = _payload(
                await client.call_tool(
                    "fetch_email_remote_content",
                    {
                        "mailbox": "INBOX",
                        "uid": "7",
                        "account_id": "acct",
                        "cache_only": True,
                    },
                )
            )
            self.assertTrue(cached["ok"])
            self.assertTrue(cached["cache_only"])
            self.assertFalse(cached["remote_fetch_performed"])
            self.assertEqual(cached["network_requests_performed"], 0)
            self.assertEqual(cached["rendered_html"], fetched["rendered_html"])
            self.assertEqual(cached["diagnostics"]["decoy_attempted"], 0)
            self.assertEqual(cached["cached_resource_contract"], contract)
            self.assertEqual(len(self.service.fetch_calls), 1)
            self.assertEqual(len(self.service.cache_calls), 1)

            cached_again = _payload(
                await client.call_tool(
                    "fetch_email_remote_content",
                    {
                        "mailbox": "INBOX",
                        "uid": "7",
                        "account_id": "acct",
                        "cache_only": True,
                    },
                )
            )
            self.assertTrue(cached_again["ok"])
            self.assertEqual(cached_again["network_requests_performed"], 0)
            self.assertEqual(cached_again["rendered_html"], fetched["rendered_html"])
            self.assertEqual(len(self.service.fetch_calls), 1)
            self.assertEqual(len(self.service.cache_calls), 2)

            invalid = _payload(
                await client.call_tool(
                    "fetch_email_remote_content",
                    {
                        "mailbox": "INBOX",
                        "uid": "7",
                        "account_id": "acct",
                        "cache_only": True,
                        "refresh": True,
                    },
                )
            )
            self.assertFalse(invalid["ok"])
            self.assertEqual(invalid["network_requests_performed"], 0)
            self.assertEqual(len(self.service.fetch_calls), 1)
            self.assertEqual(len(self.service.cache_calls), 2)

            refreshed = _payload(
                await client.call_tool(
                    "fetch_email_remote_content",
                    {
                        "mailbox": "INBOX",
                        "uid": "7",
                        "account_id": "acct",
                        "authorize_remote_fetch": True,
                        "refresh": True,
                    },
                )
            )
            self.assertTrue(refreshed["ok"])
            self.assertTrue(refreshed["refresh"])
            self.assertEqual(len(self.service.fetch_calls), 2)
            self.assertTrue(self.service.fetch_calls[-1]["refresh"])

    async def test_runtime_preview_execute_without_bearer_replay_and_stale_rejection(self):
        with patch.object(
            runtime_control,
            "schedule_current_process_termination",
        ) as restart:
            async with Client(self.core.mcp) as client:
                preview = _payload(
                    await client.call_tool(
                        "runtime_version_change_preview",
                        {"operation": "pin-version", "target_version": "v9.6.7"},
                    )
                )
                self.assertTrue(preview["ok"])
                self.assertIn("preview_id", preview)
                self.assertNotIn("confirmation_token", repr(preview))

                executed = _payload(
                    await client.call_tool(
                        "runtime_version_change_execute",
                        {"operation": "pin-version", "target_version": "v9.6.7"},
                    )
                )
                replay = _payload(
                    await client.call_tool(
                        "runtime_version_change_execute",
                        {"operation": "pin-version", "target_version": "v9.6.7"},
                    )
                )

                stale_preview = _payload(
                    await client.call_tool(
                        "runtime_version_change_preview",
                        {"operation": "pin-version", "target_version": "v9.6.7"},
                    )
                )
                self.state["build"] = "v9.6.8-mutated"
                stale = _payload(
                    await client.call_tool(
                        "runtime_version_change_execute",
                        {
                            "operation": "pin-version",
                            "target_version": "v9.6.7",
                            "preview_id": stale_preview["preview_id"],
                        },
                    )
                )

        self.assertTrue(executed["ok"])
        self.assertTrue(executed["version_change_applied"])
        self.assertFalse(replay["ok"])
        self.assertFalse(stale["ok"])
        self.assertEqual(restart.call_count, 1)


if __name__ == "__main__":
    unittest.main()
