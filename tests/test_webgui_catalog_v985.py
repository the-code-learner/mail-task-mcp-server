from __future__ import annotations

import unittest
from types import SimpleNamespace

from postmaster.webgui_catalog_v985 import (
    ORDER, CATALOG_SCRIPT, render_catalog_v985, install_catalog_v985
)


class McpCatalogV985Tests(unittest.TestCase):
    def test_live_snapshot_preserves_names_and_searchable_ui(self):
        data=[
            ("send_email","Send a message"),
            ("list_tracking_network_events","Network telemetry with raw IP"),
            ("list_contacts","Global address book"),
            ("db_create_table","Structured DB access"),
        ]
        html=render_catalog_v985(None,None,data)
        self.assertIn('data-panel="coverage"',html)
        self.assertIn("4 MCP commands available",html)
        for name,_ in data:
            self.assertIn(name,html)
        self.assertIn("Email &amp; calendar",html)
        self.assertIn("Tracking &amp; analytics",html)
        self.assertIn("Contacts &amp; authorization",html)
        self.assertIn("Structured Data",html)
        self.assertIn("data-v985-tool-search",html)
        self.assertIn("MCP Catalog",html)
        self.assertIn("input",CATALOG_SCRIPT)

    def test_installer_uses_actual_registered_tools(self):
        data=[
            SimpleNamespace(name="send_email",description="Send"),
            SimpleNamespace(name="list_contacts",description="Contacts")
        ]
        core=SimpleNamespace(mcp=SimpleNamespace(
            _tool_manager=SimpleNamespace(list_tools=lambda: data)
        ))
        original=lambda: "/* existing */"
        v962=SimpleNamespace(_styles=original,SCRIPT="/* existing JS */")
        v951=SimpleNamespace(render_coverage=lambda *_:"legacy")
        install_catalog_v985(v951,v962,core)
        html=v951.render_coverage(None,None)
        self.assertIn("2 MCP commands available",html)
        self.assertIn("send_email",html)
        self.assertIn("list_contacts",html)
        self.assertIn('id="v985-mcp-catalog-filter"',v962.SCRIPT)
        install_catalog_v985(v951,v962,core)
        self.assertEqual(v962.SCRIPT.count('id="v985-mcp-catalog-filter"'),1)
        self.assertEqual(v962._styles().count(".v985-catalog .v985-catalog-head{"),1)

    def test_missing_registry_is_not_misrepresented_as_success(self):
        with self.assertRaises(RuntimeError):
            install_catalog_v985(
                SimpleNamespace(),SimpleNamespace(),
                SimpleNamespace(mcp=SimpleNamespace())
            )


if __name__=="__main__":
    unittest.main()
