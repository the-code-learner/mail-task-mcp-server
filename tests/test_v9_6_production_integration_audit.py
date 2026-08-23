from __future__ import annotations

import asyncio
import inspect
import unittest
from pathlib import Path
from types import SimpleNamespace

from mcp.server import MCPServer
from starlette.responses import HTMLResponse

from postmaster.runtime_v964 import install_runtime_v964
from postmaster.webgui_release_identity import install_webgui_release_identity


class WebGuiReleaseIdentityTests(unittest.TestCase):
    def test_reused_lazy_shell_reports_loaded_release_in_brand_and_header(self):
        fake = SimpleNamespace()

        def base_nav() -> str:
            return '<nav><small>WebGUI v9.6.2 · lazy fragments</small></nav>'

        def base_shell(_request):
            return HTMLResponse(
                f'<html>{fake._nav()}</html>',
                headers={"X-Postmaster-WebGUI": "9.6.2-lazy"},
            )

        fake._nav = base_nav
        fake._shell = base_shell
        install_webgui_release_identity(fake, "v9.6.4")

        response = fake._shell(None)
        self.assertIn("WebGUI v9.6.4 · lazy fragments", response.body.decode("utf-8"))
        self.assertEqual(response.headers["X-Postmaster-WebGUI"], "9.6.4-lazy")
        self.assertNotIn("WebGUI v9.6.2", response.body.decode("utf-8"))

    def test_runtime_applies_release_identity_after_v964_webgui_overlay(self):
        runtime_path = Path(__file__).resolve().parents[1] / "src" / "postmaster" / "runtime.py"
        source = runtime_path.read_text(encoding="utf-8")
        v964_index = source.index("install_webgui_v964(app, _base)")
        identity_index = source.index("install_webgui_release_identity(_webgui_v962, _core._project_version())")
        self.assertLess(v964_index, identity_index)
        self.assertIn("from . import webgui_v962 as _webgui_v962", source)


class FakeRuntimeBase:
    @staticmethod
    def _safe_call(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    def mail_client(self, account_id=None):
        raise AssertionError("schema regression test must not execute mail sends")


class RealMcpCore:
    def __init__(self):
        self.mcp = MCPServer("Postmaster schema regression")
        for index in range(86):
            self.mcp.add_tool(lambda: None, name=f"placeholder_{index}")
        for name in ("build_status", "send_email", "reply_email", "follow_up_email"):
            self.mcp.add_tool(lambda: None, name=name)


class RealMcpSchemaRegressionTests(unittest.TestCase):
    def test_v964_overlay_exports_new_arguments_through_real_mcp_schema(self):
        base = FakeRuntimeBase()
        core = RealMcpCore()
        install_runtime_v964(base, core, lambda: {"ok": True, "version": "9.6.4", "build": "v9.6.4"})

        tools = asyncio.run(core.mcp.list_tools())
        by_name = {tool.name: tool for tool in tools}
        self.assertEqual(len(by_name), 90)

        build_properties = by_name["build_status"].input_schema["properties"]
        self.assertTrue(
            {"operation", "target_version", "force_refresh", "confirm_version_change"}
            <= set(build_properties)
        )
        for name in ("send_email", "reply_email", "follow_up_email"):
            properties = by_name[name].input_schema["properties"]
            self.assertIn("confirm_suppressed_recipients", properties)

        self.assertIn("confirm_version_change", inspect.signature(core.build_status).parameters)
        self.assertIn("confirm_suppressed_recipients", inspect.signature(core.send_email).parameters)


if __name__ == "__main__":
    unittest.main()
