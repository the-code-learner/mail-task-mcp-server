from __future__ import annotations

import inspect
import unittest
from email.message import EmailMessage
from pathlib import Path
from types import SimpleNamespace

from starlette.datastructures import FormData, QueryParams
from starlette.requests import Request

from postmaster import webgui_mail_files_v971 as mail_ui
from postmaster.webgui_compose_confirmation_v971 import install_compose_confirmation_v971


class MailAttachmentUxV971Tests(unittest.TestCase):
    @staticmethod
    def _raw_message() -> bytes:
        msg = EmailMessage()
        msg["From"] = "sender@example.test"
        msg["To"] = "reader@example.test"
        msg["Subject"] = "Attachments"
        msg.set_content("Body")
        msg.add_attachment(b"%PDF-1.7\nmock", maintype="application", subtype="pdf", filename="report.pdf")
        msg.add_attachment("notes here", subtype="plain", filename="notes.txt")
        msg.add_attachment(b"<svg><script>bad()</script></svg>", maintype="image", subtype="svg+xml", filename="unsafe.svg")
        return msg.as_bytes()

    def test_attachment_inventory_download_and_safe_preview_policy(self):
        rows = mail_ui._attachment_records(self._raw_message())
        self.assertEqual([row["filename"] for row in rows], ["report.pdf", "notes.txt", "unsafe.svg"])
        self.assertTrue(mail_ui._preview_supported("application/pdf"))
        self.assertTrue(mail_ui._preview_supported("text/plain"))
        self.assertFalse(mail_ui._preview_supported("image/svg+xml"))
        html = mail_ui._attachments_html(
            self._raw_message(), account_id="acct", mailbox="INBOX", uid="7"
        )
        self.assertIn("report.pdf", html)
        self.assertIn("notes.txt", html)
        self.assertIn("unsafe.svg", html)
        self.assertEqual(html.count(">Download<"), 3)
        self.assertIn("Preview unavailable", html)
        self.assertIn("/dashboard/inbox/attachment/download?", html)

    def test_attachment_routes_are_cache_only_and_do_not_add_network_clients(self):
        source = inspect.getsource(mail_ui)
        self.assertIn("raw_message(account_id, mailbox, uid)", source)
        for forbidden in ("httpx", "requests.get", "urlopen", "fetch_passive_resources"):
            self.assertNotIn(forbidden, source)

    def test_filter_contract_is_account_folder_keyword_only_plus_optional_flags(self):
        request = SimpleNamespace(query_params=QueryParams("keyword=needle"))
        html = (
            '<form><label>Account A</label><label>Mailbox B</label>'
            '<label>Subject <input name="subject" value=""></label>'
            '<label>Text <input name="text" value=""></label>'
            '<label>Since days <input name="since_days"></label></form>'
        )
        rendered = mail_ui._filter_html(html, request)
        self.assertIn("<label>Folder B</label>", rendered)
        self.assertIn('name="keyword" value="needle"', rendered)
        self.assertNotIn('name="subject"', rendered)
        self.assertNotIn('<label>Text ', rendered)


class ComposeConfirmationV971Tests(unittest.TestCase):
    def test_suppression_confirmation_preserves_every_selected_attachment(self):
        class DummyV964:
            _compose_confirmation_v971_installed = False

            @staticmethod
            def _hidden(name, value):
                return f'<input type="hidden" name="{name}" value="{value}">'

            @staticmethod
            def _confirmation_page(base, form, blocked):
                _ = base, blocked
                first = form.get("attachment_file_ids")
                return __import__("starlette.responses", fromlist=["HTMLResponse"]).HTMLResponse(
                    '<form method="post" action="/dashboard/compose/send">'
                    + DummyV964._hidden("attachment_file_ids", first)
                    + '</form><a href="/?ui_view=compose#compose">Cancel</a>',
                    status_code=409,
                    headers={"Cache-Control": "no-store"},
                )

        install_compose_confirmation_v971(DummyV964)
        form = FormData(
            [
                ("attachment_file_ids", "file-a"),
                ("attachment_file_ids", "file-b"),
                ("attachment_file_ids", "file-c"),
            ]
        )
        response = DummyV964._confirmation_page(SimpleNamespace(), form, [])
        body = response.body.decode("utf-8")
        self.assertIn('value="file-a"', body)
        self.assertIn('value="file-b"', body)
        self.assertIn('value="file-c"', body)
        self.assertIn("ui_view=inbox#inbox", body)


class ComposedRuntimeMailIaV971Tests(unittest.TestCase):
    def test_runtime_installs_tracking_before_mail_files_and_keeps_compat_boundary_last(self):
        source = (Path(__file__).resolve().parents[1] / "src" / "postmaster" / "runtime.py").read_text(encoding="utf-8")
        tracking = source.index("install_tracking_webgui_v971")
        mail = source.index("install_mail_files_composer_v971(app")
        confirm = source.index("install_compose_confirmation_v971(_webgui_v964)")
        compat = source.rindex("_webgui_v963.v960.render_inbox = _webgui_v963.render_inbox_v963")
        self.assertLess(tracking, mail)
        self.assertLess(mail, confirm)
        self.assertLess(confirm, compat)

    def test_composed_runtime_hides_top_level_compose_tracking_and_preserves_routes(self):
        import postmaster.runtime as runtime
        from postmaster import webgui_v962 as v962

        nav_views = {view for view, _label in v962.NAV}
        self.assertNotIn("compose", nav_views)
        self.assertNotIn("tracking", nav_views)
        self.assertIn("inbox", nav_views)
        route_by_path = {
            getattr(route, "path", ""): route
            for route in runtime.app.router.routes
            if getattr(route, "path", "")
        }
        self.assertEqual(route_by_path["/dashboard/compose/send"].name, "v964_compose_send")
        self.assertEqual(route_by_path["/dashboard/inbox/attachment/download"].name, "v971_attachment_download")
        self.assertEqual(route_by_path["/dashboard/inbox/attachment/preview"].name, "v971_attachment_preview")

    def test_yaml_is_not_part_of_this_workstream(self):
        source = inspect.getsource(mail_ui)
        self.assertNotIn("postmaster-mcp.yml", source)


if __name__ == "__main__":
    unittest.main()
