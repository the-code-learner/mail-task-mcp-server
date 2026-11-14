from __future__ import annotations

import unittest
from types import SimpleNamespace

from postmaster.webgui_design_v985 import (
    DESIGN_SCRIPT, DESIGN_STYLE, NAV_GROUPS_V985, contextbar,
    install_webgui_design_v985, navigation,
)
from postmaster.webgui_v962_views import VIEWS


class ApprovedDesignV985Tests(unittest.TestCase):
    def test_navigation_keeps_all_live_lazy_views_reachable(self):
        html=navigation()
        for view in VIEWS:
            self.assertIn(f'data-v962-nav="{view}"',html,view)
        for label in ("Inbox","Sent","Drafts","Spam","Tracking",
                      "Activities","Workspace","Contacts","Settings","MCP Catalog"):
            self.assertIn(label,html)
        self.assertIn('data-v985-compose',html)
        self.assertIn('data-v970-more',html)
        self.assertIn('data-v970-more-close',html)

    def test_header_has_real_mail_search_and_scope_insertion_hook(self):
        html=contextbar("9.8.5")
        self.assertIn('name="text"',html)
        self.assertIn('name="inbox_search"',html)
        self.assertIn('data-v962-nav="overview"',html)
        self.assertIn('<div class="v970-context-actions">',html)
        self.assertIn('data-v970-theme-toggle',html)
        self.assertIn('v9.8.5',html)
        self.assertNotIn("UI demo",html)

    def test_installs_as_last_visual_overlay_without_replacing_renderers(self):
        class FakeV962:
            SCRIPT="<script>existing-safe-lazy-navigation</script>"
            def __init__(self):
                self._styles=lambda: "/* original css */"
                self._nav=lambda: "<nav>original</nav>"
        fake=FakeV962()
        prior=fake.SCRIPT
        v970=SimpleNamespace(_context_bar=lambda: "Original",
                             VIEW_LABELS={})
        install_webgui_design_v985(fake,v970,"9.8.5")
        css=fake._styles()
        self.assertTrue(css.startswith("/* original css */"))
        self.assertIn("Approved v5 visual language",css)
        self.assertIn('html[data-v970-theme="dark"]',css)
        self.assertIn('data-v985-compose',fake._nav())
        self.assertIn('class="v985-topbar"',v970._context_bar())
        self.assertTrue(fake.SCRIPT.startswith(prior))
        self.assertEqual(fake.SCRIPT.count('id="v985-production-ui"'),1)
        install_webgui_design_v985(fake,v970,"9.8.5")
        self.assertEqual(fake.SCRIPT.count('id="v985-production-ui"'),1)

    def test_compose_uses_existing_inbox_details_without_second_sender(self):
        self.assertIn("details.v971-floating-compose,details#v960-compose",DESIGN_SCRIPT)
        self.assertIn('input[name="to"]',DESIGN_SCRIPT)
        self.assertNotIn("/dashboard/compose/send",DESIGN_SCRIPT)
        self.assertIn("data-v985-mailbox",DESIGN_SCRIPT)

    def test_scopebar_integration_uses_the_real_top_header_insertion_hook(self):
        from starlette.applications import Starlette
        from starlette.responses import HTMLResponse
        from postmaster.webgui_scopebar_v984 import install_scopebar_webgui_v984

        class FakeV962:
            SCRIPT=""
            def __init__(self):
                self._styles=lambda: "/* existing */"
                self._nav=lambda: "old nav"
                self._shell=lambda req: HTMLResponse(
                    '<!doctype html><html><head></head><body><div class="shell">'
                    '<header class="v970-contextbar">'
                    '<div class="v970-context-title">Old shell title</div>'
                    '<div class="v970-context-actions"></div></header>'
                    '<main>Work</main></div></body></html>'
                )
        fake=FakeV962()
        mockv970=SimpleNamespace(_context_bar=lambda:"",VIEW_LABELS={})
        install_scopebar_webgui_v984(Starlette(),object(),fake,mockv970)
        install_webgui_design_v985(fake,mockv970,"9.8.5")
        html=fake._shell(None).body.decode("utf-8")
        self.assertEqual(html.count('id="v984-scopebar"'),1)
        self.assertLess(html.index('id="v984-scopebar"'),html.index("</header>"))
        self.assertIn('data-v984-scope="owner_id"',html)

    def test_no_fake_data_or_fake_success(self):
        self.assertNotIn("example.com",DESIGN_STYLE+DESIGN_SCRIPT)
        self.assertNotIn("team-demo",DESIGN_STYLE+DESIGN_SCRIPT)


if __name__=="__main__":
    unittest.main()
