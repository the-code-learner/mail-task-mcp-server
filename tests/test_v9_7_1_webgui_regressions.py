from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import unittest

from postmaster.webgui_regressions_v971 import (
    REGRESSION_STYLE,
    _normalize_full_html_result,
    install_full_html_partial_success_v971,
    install_webgui_interaction_regressions_v971,
)


ROOT = Path(__file__).resolve().parents[1]


class _MapService:
    def __init__(self, mapping=None, error: Exception | None = None):
        self.mapping = dict(mapping or {})
        self.error = error
        self.map_calls = 0

    def _resource_map(self, inventory, *, account_id, mailbox, uid):
        self.map_calls += 1
        if self.error is not None:
            raise self.error
        return dict(self.mapping)


def _failed_result(**diagnostic_overrides):
    diagnostics = {
        "discovered": 1,
        "genuine_attempted": 1,
        "genuine_succeeded": 0,
        "genuine_failed": 1,
        "cache_hits": 0,
        "negative_cache_hits": 1,
        "cached_succeeded": 0,
        "cached_failed": 1,
        "decoy_attempted": 0,
        "decoy_succeeded": 0,
    }
    diagnostics.update(diagnostic_overrides)
    return {
        "ok": False,
        "render_state": "failure",
        "full_html_available": False,
        "rendered_html": "",
        "network_requests_performed": 1,
        "diagnostics": diagnostics,
    }


class WebGuiInteractionRegressionTests(unittest.TestCase):
    def test_shared_scroll_and_grid_fixes_are_final_cascade_and_idempotent(self):
        legacy = (
            ".scroll{overflow:auto;overscroll-behavior:contain}"
            ".grid,.v951-grid{display:grid;grid-template-columns:repeat(2,1fr)}"
        )
        v962 = SimpleNamespace(_styles=lambda: legacy)

        install_webgui_interaction_regressions_v971(v962)
        install_webgui_interaction_regressions_v971(v962)
        css = v962._styles()

        self.assertEqual(css.count("webgui-v971-shared-regression-fixes"), 1)
        self.assertIn(
            ".scroll{overscroll-behavior-x:contain;overscroll-behavior-y:auto}",
            css,
        )
        self.assertIn(".grid,.v951-grid{align-items:start}", css)
        self.assertGreater(
            css.rfind("overscroll-behavior-y:auto"),
            css.rfind("overscroll-behavior:contain"),
        )
        self.assertGreater(css.rfind("align-items:start"), css.rfind("display:grid"))
        self.assertNotIn("preventDefault", REGRESSION_STYLE)
        self.assertNotIn("touch-action:none", REGRESSION_STYLE)

    def test_runtime_installs_shared_service_fix_before_routes_and_css_after_v970(self):
        source = (ROOT / "src/postmaster/runtime.py").read_text(encoding="utf-8")
        service_create = source.index(
            "_passive_content_service = install_runtime_v969_pre_webgui"
        )
        partial_install = source.index(
            "install_full_html_partial_success_v971(_passive_content_service)"
        )
        route_install = source.index("install_webgui_v963(app, _base)")
        v970_install = source.index("install_webgui_v970(_webgui_v962)")
        interaction_install = source.index(
            "install_webgui_interaction_regressions_v971(_webgui_v962)"
        )
        self.assertLess(service_create, partial_install)
        self.assertLess(partial_install, route_install)
        self.assertLess(v970_install, interaction_install)


class FullHtmlPartialSuccessRegressionTests(unittest.TestCase):
    def test_all_passive_resources_can_fail_while_useful_document_is_partial(self):
        service = _MapService()
        body = '<article><h1>Hello</h1><p>Readable message.</p><img src="https://img.example/fail.png"></article>'

        result = _normalize_full_html_result(
            service,
            _failed_result(),
            body_html=body,
            inventory={"urls": []},
            account_id="acct",
            mailbox="INBOX",
            uid="7",
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["render_state"], "partial")
        self.assertTrue(result["full_html_available"])
        self.assertIn("Readable message.", result["rendered_html"])
        self.assertNotIn("img.example", result["rendered_html"])
        self.assertTrue(result["diagnostics"]["document_renderable"])
        self.assertEqual(result["diagnostics"]["isolated_render_failures"], 1)
        self.assertTrue(result["diagnostics"]["resource_map_available"])

    def test_successful_resources_render_locally_and_failed_peer_is_isolated(self):
        ok_url = "https://cdn.example/ok.png"
        failed_url = "https://cdn.example/fail.png"
        local = "/dashboard/inbox/resource?key=r_" + ("a" * 64)
        service = _MapService({ok_url: local})
        body = (
            '<div>Newsletter body<img src="' + ok_url + '">'
            '<img src="' + failed_url + '"></div>'
        )
        partial = _failed_result(
            genuine_attempted=2,
            genuine_succeeded=1,
            genuine_failed=1,
            cached_succeeded=1,
            cached_failed=1,
        )
        partial["render_state"] = "partial"
        partial["ok"] = True
        partial["full_html_available"] = True

        result = _normalize_full_html_result(
            service,
            partial,
            body_html=body,
            inventory={"urls": []},
            account_id="acct",
            mailbox="INBOX",
            uid="8",
        )

        self.assertEqual(result["render_state"], "partial")
        self.assertIn(local, result["rendered_html"])
        self.assertNotIn(failed_url, result["rendered_html"])
        self.assertIn("Newsletter body", result["rendered_html"])

    def test_unusable_document_still_falls_back_instead_of_claiming_partial(self):
        service = _MapService()
        body = '<script>secretAction()</script><img src="https://img.example/fail.png">'

        result = _normalize_full_html_result(
            service,
            _failed_result(),
            body_html=body,
            inventory={"urls": []},
            account_id="acct",
            mailbox="INBOX",
            uid="9",
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["render_state"], "failure")
        self.assertFalse(result["full_html_available"])
        self.assertEqual(result["rendered_html"], "")
        self.assertFalse(result["diagnostics"]["document_renderable"])

    def test_navigation_is_preserved_but_never_added_to_passive_fetch_map(self):
        service = _MapService()
        body = (
            '<p>Follow up</p><a href="https://example.net/action?token=visible-link">Open</a>'
            '<form action="https://example.net/submit"><button>Submit</button></form>'
        )
        result = _normalize_full_html_result(
            service,
            {
                "ok": True,
                "render_state": "success",
                "full_html_available": True,
                "diagnostics": {"cached_failed": 0, "genuine_failed": 0},
            },
            body_html=body,
            inventory={"urls": []},
            account_id="acct",
            mailbox="INBOX",
            uid="10",
        )

        self.assertEqual(service.map_calls, 1)
        self.assertEqual(result["render_state"], "success")
        self.assertIn('href="https://example.net/action?token=visible-link"', result["rendered_html"])
        self.assertNotIn("example.net/submit", result["rendered_html"])
        self.assertNotIn("<form", result["rendered_html"])

    def test_resource_map_exception_is_aggregate_safe_and_does_not_destroy_document(self):
        secret = "proxy-secret-should-never-escape"
        service = _MapService(error=RuntimeError(secret))
        result = _normalize_full_html_result(
            service,
            _failed_result(),
            body_html="<p>Still readable</p>",
            inventory={"urls": []},
            account_id="acct",
            mailbox="INBOX",
            uid="11",
        )

        self.assertEqual(result["render_state"], "partial")
        self.assertTrue(result["full_html_available"])
        self.assertFalse(result["diagnostics"]["resource_map_available"])
        self.assertNotIn(secret, repr(result))
        self.assertIn("Still readable", result["rendered_html"])

    def test_shared_service_installer_updates_fetch_and_cache_only_paths_once(self):
        body = '<main><p>Cached readable content</p><img src="https://remote.example/fail.png"></main>'

        class Cache:
            def get_message(self, account_id, mailbox, uid, include_body=False):
                self.last = (account_id, mailbox, uid, include_body)
                return {
                    "body_cached": True,
                    "body_html": body,
                    "body": "Cached readable content",
                }

        cache = Cache()

        class Service:
            def __init__(self):
                self.base = SimpleNamespace(mailbox_cache_store=lambda: cache)
                self.fetch_calls = 0
                self.cached_calls = 0

            def _resource_map(self, inventory, *, account_id, mailbox, uid):
                return {}

            def fetch_inventory(
                self,
                inventory,
                *,
                account_id,
                mailbox,
                uid,
                refresh=False,
                body_html="",
            ):
                self.fetch_calls += 1
                return _failed_result()

            def render_cached_message(self, *, account_id, mailbox, uid):
                self.cached_calls += 1
                result = _failed_result(genuine_attempted=0, genuine_failed=0)
                result["cache_only"] = True
                result["network_requests_performed"] = 0
                return result

        service = Service()
        install_full_html_partial_success_v971(service)
        first_fetch = service.fetch_inventory
        install_full_html_partial_success_v971(service)
        self.assertIs(service.fetch_inventory, first_fetch)

        remote = service.fetch_inventory(
            {"urls": []},
            account_id="acct",
            mailbox="INBOX",
            uid="12",
            body_html=body,
        )
        cached = service.render_cached_message(
            account_id="acct",
            mailbox="INBOX",
            uid="12",
        )

        self.assertEqual(service.fetch_calls, 1)
        self.assertEqual(service.cached_calls, 1)
        self.assertEqual(remote["render_state"], "partial")
        self.assertEqual(cached["render_state"], "partial")
        self.assertEqual(cached["network_requests_performed"], 0)
        self.assertTrue(cached["cache_only"])
        self.assertIn("Cached readable content", cached["rendered_html"])


if __name__ == "__main__":
    unittest.main()
