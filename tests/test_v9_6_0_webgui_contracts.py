from __future__ import annotations

import inspect
import re
import unittest

from postmaster import webgui_v960


class _ComposeBase:
    @staticmethod
    def _csrf_value():
        return "csrf-test"


class WebGuiReleaseContractsV960Tests(unittest.TestCase):
    def test_reader_requests_inspection_full_and_safe_content_mode(self):
        source = inspect.getsource(webgui_v960.render_inbox)
        self.assertIn('inspection="full"', source)
        self.assertIn('content_mode="safe"', source)
        detail_source = inspect.getsource(webgui_v960._detail_html)
        self.assertNotIn("body_html_safe", detail_source)
        self.assertIn('detail.get("body_html")', detail_source)

    def test_compose_form_has_stable_server_idempotency_key_for_double_submit(self):
        html = webgui_v960._compose_panel(_ComposeBase(), [], None)
        values = re.findall(r'name="idempotency_key" value="([^"]+)"', html)
        self.assertEqual(len(values), 1)
        self.assertTrue(values[0].startswith("webgui-"))
        self.assertIn('data-v960-send="1"', html)
        self.assertIn("idempotency_key", inspect.getsource(webgui_v960.compose_send))
        self.assertIn("form.querySelectorAll('button[type=\"submit\"]')", webgui_v960.SCRIPT)


if __name__ == "__main__":
    unittest.main()
