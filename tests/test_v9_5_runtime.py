from __future__ import annotations

import re
import unittest
from pathlib import Path

from postmaster import runtime_v950


class FakeReliabilityStore:
    def list_suppressions(self, *, active_only=True, limit=100):
        return [{
            "recipient": "person@example.net",
            "reason": "manual",
            "source": "test",
            "updated_at": "2026-08-21T00:00:00+00:00",
        }]


class StandardsPanelTests(unittest.TestCase):
    def test_all_v950_post_forms_include_existing_csrf_token(self):
        original = runtime_v950.reliability_store
        runtime_v950.reliability_store = lambda: FakeReliabilityStore()
        try:
            class Base:
                @staticmethod
                def _csrf_value():
                    return "csrf<&"

            html = runtime_v950._standards_panel(Base())
        finally:
            runtime_v950.reliability_store = original
        self.assertEqual(html.count('name="csrf"'), 3)
        self.assertEqual(html.count('value="csrf&lt;&amp;"'), 3)
        self.assertIn('/dashboard/mail-health/refresh', html)
        self.assertIn('/dashboard/suppression/suppress', html)
        self.assertIn('/dashboard/suppression/unsuppress', html)


class MCPSurfaceTests(unittest.TestCase):
    def test_v950_replaces_existing_tools_instead_of_adding_names(self):
        source = (Path(__file__).resolve().parents[1] / "src" / "postmaster" / "runtime_v950.py").read_text(encoding="utf-8")
        removed = set(re.findall(r'core\.mcp\.remove_tool\("([^"]+)"\)', source))
        added = set(re.findall(r'core\.mcp\.add_tool\([^\n]+name="([^"]+)"\)', source))
        self.assertEqual(added, removed)
        self.assertFalse(re.search(r'@(?:core\.)?mcp\.tool', source))
        for name in {
            "build_status",
            "test_email_account",
            "mailbox_status",
            "send_email",
            "reply_email",
            "follow_up_email",
            "create_draft",
            "create_reply_draft",
            "create_follow_up_draft",
        }:
            self.assertIn(name, added)


if __name__ == "__main__":
    unittest.main()
