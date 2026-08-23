from __future__ import annotations

import asyncio
import inspect
import unittest
from pathlib import Path
from types import SimpleNamespace

from mcp.server import MCPServer
from starlette.responses import HTMLResponse

from postmaster.runtime_v964 import install_runtime_v964
from postmaster.webgui_release_identity import install_webgui_release_identity, project_release_version
from postmaster.webgui_visual_restoration import (
    RESTORED_STYLE,
    install_webgui_visual_restoration,
)


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

    def test_release_identity_reads_same_local_version_metadata_as_runtime(self):
        version_path = Path(__file__).resolve().parents[1] / "VERSION"
        self.assertEqual(project_release_version(), version_path.read_text(encoding="utf-8").strip())

    def test_runtime_applies_visual_restore_then_release_identity_after_v964_overlay(self):
        runtime_path = Path(__file__).resolve().parents[1] / "src" / "postmaster" / "runtime.py"
        source = runtime_path.read_text(encoding="utf-8")
        v964_index = source.index("install_webgui_v964(app, _base)")
        visual_index = source.index("install_webgui_visual_restoration(_webgui_v962)")
        identity_index = source.index("install_webgui_release_identity(_webgui_v962, project_release_version())")
        self.assertLess(v964_index, visual_index)
        self.assertLess(visual_index, identity_index)
        self.assertIn("from . import webgui_v962 as _webgui_v962", source)


class WebGuiVisualRestorationTests(unittest.TestCase):
    def test_pre_v962_grouped_sidebar_and_color_contract_are_forward_ported(self):
        fake = SimpleNamespace(BASE_STYLE="")
        install_webgui_visual_restoration(fake)
        nav = fake._nav()

        self.assertIn('class="v962-nav"', nav)
        self.assertIn("Operate", nav)
        self.assertIn("Organize", nav)
        self.assertIn("Control", nav)
        self.assertIn("Domain controls", nav)
        self.assertIn("Recipient controls", nav)
        self.assertIn('class="v962-ico"', nav)
        self.assertIn("⌂", nav)
        self.assertIn("⚡", nav)
        self.assertEqual(nav.count("data-v962-nav="), 18)
        self.assertIn("WebGUI v9.6.2 · lazy fragments", nav)

        style = fake.BASE_STYLE
        self.assertIn("webgui-pre-v962-color-restoration", style)
        self.assertIn("--surface:var(--card)", style)
        self.assertIn("--border:var(--line)", style)
        self.assertIn(".shell>main{margin-left:0", style)
        self.assertIn("project-color-0", style)
        self.assertIn("project-color-7", style)
        self.assertIn("#64b5f6", style)
        self.assertIn("#90a4ae", style)
        self.assertIn("linear-gradient", style)

    def test_visual_install_is_idempotent_and_does_not_touch_lazy_script(self):
        from postmaster import webgui_v962 as v962

        lazy_script = v962.SCRIPT
        before = v962.BASE_STYLE.count("webgui-pre-v962-color-restoration")
        install_webgui_visual_restoration(v962)
        install_webgui_visual_restoration(v962)
        after = v962.BASE_STYLE.count("webgui-pre-v962-color-restoration")

        self.assertEqual(v962.SCRIPT, lazy_script)
        self.assertEqual(after, max(before, 1))
        self.assertIn('id="v962-lazy-dashboard"', v962.SCRIPT)
        self.assertIn("/dashboard/view/", v962.SCRIPT)
        self.assertIn("new AbortController()", v962.SCRIPT)
        self.assertIn("generations.get(view) !== generation", v962.SCRIPT)
        self.assertIn("target.classList.contains('active')", v962.SCRIPT)

    def test_restoration_css_is_presentation_only(self):
        self.assertNotIn("Route(", RESTORED_STYLE)
        self.assertNotIn("send_email", RESTORED_STYLE)
        self.assertNotIn("privacy_proxy", RESTORED_STYLE)


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

    def test_composed_runtime_exports_v960_v963_v964_schema_through_real_registry(self):
        import postmaster.runtime as runtime

        tools = asyncio.run(runtime.mcp.list_tools())
        by_name = {tool.name: tool for tool in tools}
        self.assertEqual(len(by_name), 96)

        get_email = by_name["get_email"].input_schema["properties"]
        self.assertTrue(
            {"mailbox", "uid", "account_id", "inspection", "content_mode", "acknowledge_unsanitized_content_risk"}
            <= set(get_email)
        )

        proxy_admin = by_name["set_amp_account_state"].input_schema["properties"]
        self.assertTrue(
            {
                "privacy_proxy_worker_url", "privacy_proxy_secret", "privacy_proxy_enabled",
                "tracking_obfuscation", "high_noise_decoy_enabled", "privacy_proxy_test",
                "privacy_proxy_dismiss_offer",
            }
            <= set(proxy_admin)
        )

        build_properties = by_name["build_status"].input_schema["properties"]
        self.assertTrue(
            {"operation", "target_version", "force_refresh", "confirm_version_change"}
            <= set(build_properties)
        )
        for name in ("send_email", "reply_email", "follow_up_email"):
            self.assertIn(
                "confirm_suppressed_recipients",
                by_name[name].input_schema["properties"],
            )

    def test_composed_runtime_keeps_v963_renderer_v964_send_route_and_lazy_shell(self):
        import postmaster.runtime as runtime
        from postmaster import webgui_v962 as v962
        from postmaster import webgui_v963 as v963

        response = v962._shell(SimpleNamespace(query_params={}))
        html = response.body.decode("utf-8")
        self.assertIn('id="v962-lazy-dashboard"', html)
        self.assertIn("/dashboard/view/", html)
        self.assertIn("Operate", html)
        self.assertIn("Organize", html)
        self.assertIn("Control", html)
        self.assertIn(f"WebGUI v{project_release_version().removeprefix('v')} · lazy fragments", html)

        # Other tests legitimately re-install the v9.6.1 renderer in this shared process.
        # Verify production composition from runtime source instead of a contaminated module global:
        # v9.6.2 dispatches through v960.render_inbox and runtime's last assignment points it at
        # the current v9.6.3 release-contract renderer after every functional overlay is installed.
        runtime_source = Path(runtime.__file__).read_text(encoding="utf-8")
        views_source = (
            Path(runtime.__file__).resolve().parent / "webgui_v962_views.py"
        ).read_text(encoding="utf-8")
        renderer_index = runtime_source.rindex(
            "_webgui_v963.v960.render_inbox = _webgui_v963.render_inbox_v963"
        )
        self.assertGreater(renderer_index, runtime_source.index("install_webgui_v964(app, _base)"))
        self.assertIn("return v960.render_inbox(proxy, request)", views_source)
        self.assertTrue(callable(v963.render_inbox_v963))

        route_by_path = {
            getattr(route, "path", ""): route
            for route in runtime.app.router.routes
            if getattr(route, "path", "")
        }
        self.assertIn("/dashboard/inbox/refresh", route_by_path)
        self.assertIn("/dashboard/inbox/full-html", route_by_path)
        self.assertIn("/dashboard/inbox/draft", route_by_path)
        self.assertIn("/dashboard/privacy-proxy/configure", route_by_path)
        self.assertIn("/dashboard/privacy-proxy/test", route_by_path)
        self.assertIn("/dashboard/inbox/resource", route_by_path)
        self.assertEqual(route_by_path["/dashboard/compose/send"].name, "v964_compose_send")


if __name__ == "__main__":
    unittest.main()
