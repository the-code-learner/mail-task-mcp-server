from __future__ import annotations

import unittest
from types import SimpleNamespace

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse
from starlette.routing import Route

from postmaster.webgui_scopebar_v984 import install_scopebar_webgui_v984


class FakeV962:
    def __init__(self):
        self._shell = lambda request: HTMLResponse(
            '<!doctype html><html><head></head><body>'
            '<header class="v970-contextbar">'
            '<div class="v970-context-actions">Theme</div></header>'
            '<main>Other views lazy</main></body></html>',
            headers={"Cache-Control":"private, no-store"},
        )


class WebGuiScopebarV984Tests(unittest.TestCase):
    def test_shell_uses_async_options_without_backend_queries(self):
        app = Starlette()
        v962 = FakeV962()
        class Base:
            def scheduler(self):
                raise AssertionError("The shell must remain query free")
            def account_store(self):
                raise AssertionError("The shell must remain query free")
        install_scopebar_webgui_v984(app, Base(), v962, SimpleNamespace())
        html = v962._shell(None).body.decode("utf-8")
        self.assertIn('id="v984-scopebar"', html)
        self.assertIn('data-v984-scope="owner_id"', html)
        self.assertIn('data-v984-scope="project_id"', html)
        self.assertIn('data-v984-scope="account_id"', html)
        self.assertIn("/dashboard/context/options", html)
        self.assertIn('[data-v970-nav="compose"]', html)
        self.assertTrue(any(isinstance(route,Route) and route.path == "/dashboard/context/options"
                            for route in app.router.routes))

    def test_installer_remains_idempotent(self):
        app = Starlette()
        v962 = FakeV962()
        install_scopebar_webgui_v984(app, object(), v962, SimpleNamespace())
        install_scopebar_webgui_v984(app, object(), v962, SimpleNamespace())
        self.assertEqual(sum(
            isinstance(route,Route) and route.path == "/dashboard/context/options"
            for route in app.router.routes
        ),1)
        self.assertEqual(v962._shell(None).body.decode("utf-8").count('id="v984-scopebar"'),1)


if __name__=="__main__":
    unittest.main()
