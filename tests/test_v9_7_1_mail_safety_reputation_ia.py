from __future__ import annotations

import inspect
import unittest
from pathlib import Path
from types import SimpleNamespace

from postmaster import webgui_mail_safety_v971 as ia


class MailSafetyReputationIaV971Tests(unittest.TestCase):
    def _fixture(self):
        def renderer(view: str):
            def render(*_args, **_kwargs):
                return f'<section class="tab-panel" id="panel-{view}" data-panel="{view}"><div>{view} backend content</div></section>'

            return render

        shell = SimpleNamespace(
            NAV=(
                ("overview", "Dashboard"),
                ("mail-health", "Mail Health"),
                ("suppressions", "Suppressions"),
                ("security", "Security"),
                ("domains", "Domains"),
                ("recipients", "Recipients"),
                ("system", "System"),
            ),
            BASE_STYLE="",
            SCRIPT=(
                "function activate(view) {\n"
                "    document.querySelectorAll('[data-v962-nav]').forEach(a => a.classList.toggle('active', a.dataset.v962Nav === view));\n"
                "  }"
            ),
        )
        views = SimpleNamespace(
            VIEWS=("overview",) + ia.SAFETY_VIEWS + ("system",),
            v960=SimpleNamespace(render_mail_health=renderer("mail-health")),
            v951=SimpleNamespace(render_security=renderer("security")),
            render_suppressions=renderer("suppressions"),
            render_domains=renderer("domains"),
            render_recipients=renderer("recipients"),
        )
        return shell, views

    def test_primary_navigation_consolidates_without_removing_compatibility_views(self):
        shell, views = self._fixture()
        before_views = views.VIEWS
        ia.install_mail_safety_ia_v971(shell, views)
        self.assertIn(("mail-health", "Mail Safety"), shell.NAV)
        self.assertNotIn(("suppressions", "Suppressions"), shell.NAV)
        self.assertNotIn(("security", "Security"), shell.NAV)
        self.assertNotIn(("domains", "Domains"), shell.NAV)
        self.assertNotIn(("recipients", "Recipients"), shell.NAV)
        self.assertEqual(views.VIEWS, before_views)
        for view in ia.SAFETY_VIEWS:
            self.assertIn(view, views.VIEWS)

    def test_shared_header_covers_required_mental_model_and_deep_links(self):
        html = ia._safety_header("domains")
        for text in (
            "Mail safety",
            "Reputation / deliverability",
            "Authorization / policy",
            "Security",
        ):
            self.assertIn(text, html)
        for view in ia.SAFETY_VIEWS:
            self.assertIn(f"ui_view={view}#{view}", html)
        self.assertIn('data-v960-fragment="domains"', html)
        self.assertIn('class="active" data-v960-fragment="domains"', html)

    def test_wrapped_views_preserve_existing_backend_content(self):
        shell, views = self._fixture()
        ia.install_mail_safety_ia_v971(shell, views)
        rendered = {
            "mail-health": views.v960.render_mail_health(None, None),
            "suppressions": views.render_suppressions(None, None),
            "domains": views.render_domains(None, None),
            "recipients": views.render_recipients(None, None),
            "security": views.v951.render_security(None, None),
        }
        for view, html in rendered.items():
            self.assertIn('data-v971-mail-safety="1"', html)
            self.assertIn(f"{view} backend content", html)
        self.assertIn("post-v9.7.0 mail safety / reputation IA", shell.BASE_STYLE)

    def test_primary_nav_stays_active_for_internal_compatibility_views(self):
        shell, views = self._fixture()
        ia.install_mail_safety_ia_v971(shell, views)
        self.assertIn("v971-mail-safety-primary", shell.SCRIPT)
        self.assertIn("'suppressions','domains','recipients','security'", shell.SCRIPT)
        self.assertIn("? 'mail-health' : view", shell.SCRIPT)

    def test_installer_is_presentation_only(self):
        source = inspect.getsource(ia)
        self.assertNotIn("postmaster-mcp.yml", source)
        self.assertNotIn("Route(", source)
        self.assertNotIn("CREATE TABLE", source)
        self.assertNotIn("ALTER TABLE", source)
        self.assertNotIn("requirements.txt", source)

    def test_runtime_installs_mail_safety_after_project_ux(self):
        source = (Path(__file__).resolve().parents[1] / "src" / "postmaster" / "runtime.py").read_text(encoding="utf-8")
        self.assertLess(
            source.index("install_projects_ux_v971(_webgui_v960"),
            source.index("install_mail_safety_ia_v971(_webgui_v962"),
        )

    def test_composed_runtime_has_one_primary_mail_safety_destination(self):
        import postmaster.runtime  # noqa: F401
        from postmaster import webgui_v962 as shell
        from postmaster import webgui_v962_views as views

        nav = dict(shell.NAV)
        self.assertEqual(nav.get("mail-health"), "Mail Safety")
        for view in ("suppressions", "domains", "recipients", "security"):
            self.assertNotIn(view, nav)
            self.assertIn(view, views.VIEWS)


if __name__ == "__main__":
    unittest.main()
