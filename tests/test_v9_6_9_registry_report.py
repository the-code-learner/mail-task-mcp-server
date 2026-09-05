from __future__ import annotations

import asyncio
import json
import unittest


class RegistryReportV980Tests(unittest.TestCase):
    def test_final_composed_registry_count_names_and_fetch_schema(self):
        import postmaster.runtime as runtime

        tools = asyncio.run(runtime.mcp.list_tools())
        by_name = {tool.name: tool for tool in tools}
        names = sorted(by_name)
        self.assertEqual(len(names), 118)
        self.assertIn("fetch_email_remote_content", by_name)
        self.assertIn("db_status", by_name)
        self.assertIn("db_link_memory", by_name)

        fetch_schema = by_name["fetch_email_remote_content"].input_schema
        properties = fetch_schema["properties"]
        self.assertTrue(
            {
                "mailbox",
                "uid",
                "account_id",
                "authorize_remote_fetch",
                "refresh",
                "cache_only",
            }
            <= set(properties)
        )
        self.assertNotIn("confirmation_token", properties)

        runtime_execute = by_name["runtime_version_change_execute"].input_schema[
            "properties"
        ]
        proxy_execute = by_name["privacy_proxy_provisioning_execute"].input_schema[
            "properties"
        ]
        self.assertIn("preview_id", runtime_execute)
        self.assertIn("preview_id", proxy_execute)
        self.assertNotIn("confirmation_token", runtime_execute)
        self.assertNotIn("confirmation_token", proxy_execute)

        print("MCP_COMMAND_COUNT_V980_ACTUAL=" + str(len(names)))
        print("MCP_COMMAND_NAMES_V980_ACTUAL=" + json.dumps(names, separators=(",", ":")))
        print(
            "MCP_FETCH_REMOTE_SCHEMA_V980_ACTUAL="
            + json.dumps(fetch_schema, sort_keys=True, separators=(",", ":"))
        )


if __name__ == "__main__":
    unittest.main()
