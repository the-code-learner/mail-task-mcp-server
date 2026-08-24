from __future__ import annotations

import inspect
from types import SimpleNamespace
import unittest

from starlette.responses import HTMLResponse

from postmaster.webgui_release_identity import install_webgui_release_identity
from postmaster.webgui_v962_views import VIEWS
from postmaster.webgui_v970 import (
    ENTERPRISE_SCRIPT,
    ENTERPRISE_STYLE,
    NAV_GROUPS,
    VIEW_LABELS,
    enterprise_nav,
    install_webgui_v970,
)


def _fake_shell(_request):
    return HTMLResponse(
        '<!doctype html><html><head></head><body><div class="shell">'
        '<nav>legacy</nav><main>'
        '<form method="post" action="/dashboard/compose/send">'
        '<input type="hidden" name="csrf" value="fixed-token">'
        '<input name="subject" value="Existing payload">'
        '<button type="submit" name="compose_action" value="send">Send</button>'
        '</form></main></div><script id="existing-lifecycle">window.existing=true;</script>'
        '</body></html>',
        headers={"Cache-Control": "private, no-store", "X-Postmaster-WebGUI": "9.6.2-lazy"},
    )


def _fake_v962():
    return SimpleNamespace(
        BASE_STYLE="/* baseline */",
        SCRIPT='<script id="baseline-script">window.baseline=true;</script>',
        _nav=lambda: '<nav>legacy</nav>',
        _shell=_fake_shell,
    )


class WebGuiV970Tests(unittest.TestCase):
    def test_installer_has_no_app_or_backend_registry_parameter(self):
        self.assertEqual(list(inspect.signature(install_webgui_v970).parameters), ["v962"])

    def test_navigation_preserves_every_existing_lazy_view(self):
        covered = {
            view
            for _heading, links in NAV_GROUPS
            for view, _label, _code in links
        }
        self.assertEqual(covered, set(VIEWS))
        self.assertEqual(set(VIEW_LABELS), set(VIEWS))
        nav = enterprise_nav()
        for view in VIEWS:
            self.assertIn(f'data-v962-nav="{view}"', nav)
            self.assertIn(f'data-v970-nav="{view}"', nav)

    def test_shell_overlay_preserves_existing_form_contract_and_script(self):
        v962 = _fake_v962()
        install_webgui_v970(v962)
        response = v962._shell(object())
        html = response.body.decode("utf-8")
        self.assertIn('method="post" action="/dashboard/compose/send"', html)
        self.assertIn('name="csrf" value="fixed-token"', html)
        self.assertIn('name="subject" value="Existing payload"', html)
        self.assertIn('name="compose_action" value="send"', html)
        self.assertIn('id="existing-lifecycle"', html)
        self.assertIn('class="v970-workspace"', html)
        self.assertIn('class="v970-contextbar"', html)
        self.assertEqual(response.headers["x-postmaster-webgui-design"], "v9.7.0-enterprise-refresh")
        self.assertEqual(response.headers["cache-control"], "private, no-store")

    def test_install_is_idempotent_for_style_and_script(self):
        v962 = _fake_v962()
        install_webgui_v970(v962)
        install_webgui_v970(v962)
        self.assertEqual(v962.BASE_STYLE.count("webgui-v970-enterprise-operational-refresh"), 1)
        self.assertEqual(v962.SCRIPT.count('id="v970-enterprise-shell"'), 1)

    def test_release_identity_still_uses_local_release_version(self):
        v962 = _fake_v962()
        install_webgui_v970(v962)
        install_webgui_release_identity(v962, "9.7.0")
        self.assertIn("WebGUI v9.7.0 · lazy fragments", v962._nav())
        response = v962._shell(object())
        self.assertEqual(response.headers["x-postmaster-webgui"], "9.7.0-lazy")

    def test_responsive_accessibility_and_theme_contracts_are_present(self):
        for token in (
            "@media(max-width:1279px)",
            "@media(max-width:767px)",
            "@media(max-width:430px)",
            "prefers-reduced-motion:reduce",
            "prefers-color-scheme:light",
            'data-v970-theme="light"',
            ":focus-visible",
            "v970-mobile-nav",
            "task-calendar-grid",
            "#panel-inbox:has(.v963-detail)",
        ):
            self.assertIn(token, ENTERPRISE_STYLE)
        self.assertIn("MutationObserver", ENTERPRISE_SCRIPT)
        self.assertIn("aria-expanded", ENTERPRISE_SCRIPT)

    def test_mobile_navigation_keeps_existing_lazy_contract(self):
        nav = enterprise_nav()
        self.assertIn('aria-label="Mobile primary navigation"', nav)
        self.assertIn("data-v970-more", nav)
        self.assertIn('aria-controls="v970-more-sheet"', nav)
        for view in ("overview", "inbox", "compose", "scheduler"):
            self.assertIn(f'data-v962-nav="{view}"', nav)


if __name__ == "__main__":
    unittest.main()
