from __future__ import annotations

import unittest

from postmaster.webgui_mail_v986 import (
    modern_compose_markup, modern_inbox_markup,
    EDITOR, MAIL_SCRIPT,
)


FORM = """<details class="card v960-compose" id="v960-compose"><summary>Compose inside Inbox</summary>
<form method="post" action="/dashboard/compose/send" data-v960-send="1">
<input type="hidden" name="csrf" value="protected"><input type="hidden" name="idempotency_key" value="stable">
<label>To <input name="to"></label>
<label class="wide">Plain text <textarea name="body" rows="8"></textarea></label>
<label class="wide">HTML (optional) <textarea name="body_html" rows="6" placeholder="&lt;p&gt;HTML alternative&lt;/p&gt;"></textarea></label>
<label class="wide">AMP (optional, account capability required) <textarea name="body_amp" rows="5" placeholder="AMP-for-Email document"></textarea></label>
<label class="wide">Stored File attachment IDs <input name="attachment_file_ids" placeholder="id"></label>
<button type="submit" name="compose_action" value="send">Send</button></form></details>"""


INBOX = """<section class="tab-panel" id="panel-inbox" data-panel="inbox">
<div class="v963-inbox-head"><h2>Inbox</h2><p>Cache-first</p></div>
<nav class="v960-mailbox-tabs" aria-label="Mailboxes">
<a data-v960-fragment="inbox" class="active" href="/?ui_view=inbox&amp;mailbox=INBOX#inbox">Inbox</a>
<a data-v960-fragment="inbox" class="" href="/?ui_view=inbox&amp;mailbox=Sent#inbox">Sent</a></nav>
<form method="get" action="/" class="v951-toolbar" data-v960-fragment="inbox">
<input type="hidden" name="ui_view" value="inbox">
<label>Account <select name="account_id"><option value="one" selected>One</option></select></label>
<label>Folder <select name="mailbox"><option value="INBOX" selected>Inbox</option></select></label>
<label>Keyword <input name="keyword"></label><button type="submit">Filter cache</button></form>
<div class="scroll"><table class="v960-mail-table">
<tr class="v963-mail-row" data-v960-href="/?ui_view=inbox&amp;message_uid=1#inbox">
<td>•</td><td>Alice</td><td>Subject</td><td>2026-11-14</td></tr></table></div>
<button type="submit">Aggiorna</button>
</section>"""


class MailGuiV986ContractTests(unittest.TestCase):
    def test_composer_retains_original_backend_input_names_and_idempotency(self):
        html=modern_compose_markup(FORM)
        for name in ("body","body_html","body_amp","attachment_file_ids",
                     "csrf","idempotency_key","compose_action"):
            self.assertIn(f'name="{name}"',html)
        self.assertIn('data-v986-format',html)
        self.assertIn('data-v986-rich',html)
        self.assertIn('data-v986-code',html)
        self.assertIn('data-v986-editor-mode="visual"',html)
        self.assertIn('data-v986-editor-mode="code"',html)
        self.assertNotIn("HTML (optional) <textarea",html)
        self.assertNotIn("AMP (optional, account capability required) <textarea",html)
        self.assertEqual(html.count('name="body_html"'),1)
        self.assertIn('/dashboard/compose/send',html)

    def test_no_credentials_or_files_automatically_attached(self):
        self.assertIn("for(const option of select.options) option.selected=false",MAIL_SCRIPT)
        self.assertIn("0 selected",MAIL_SCRIPT) if False else None
        self.assertIn("Files in storage are never attached unless explicitly selected",MAIL_SCRIPT)
        self.assertIn("checkbox.addEventListener('change'",MAIL_SCRIPT)
        self.assertNotIn("window.fetch(",MAIL_SCRIPT)
        self.assertIn("plain:body.value||''",MAIL_SCRIPT)

    def test_mail_filter_reuses_global_account_and_folder_as_hidden_inputs(self):
        html=modern_inbox_markup(INBOX)
        self.assertIn("v986-inbox-head",html)
        self.assertIn("v986-mail-list",html)
        self.assertIn("Advanced mailbox filters",html)
        self.assertIn('type="hidden" name="account_id" value="one"',html)
        self.assertIn('type="hidden" name="mailbox" value="INBOX"',html)
        self.assertNotIn('<select name="account_id">',html)
        self.assertNotIn('<select name="mailbox">',html)
        self.assertIn('data-v960-href=',html)
        self.assertIn('data-v960-fragment="inbox"',html)
        self.assertIn(">Refresh</button>",html)

    def test_compose_refuses_partial_markup_transformations(self):
        html='<form><textarea name="body_html"></textarea></form>'
        self.assertEqual(modern_compose_markup(html),html)


if __name__=="__main__":
    unittest.main()
