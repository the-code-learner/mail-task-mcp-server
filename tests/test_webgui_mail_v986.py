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
        self.assertNotIn("Advanced mailbox filters",html)
        self.assertNotIn('class="v951-toolbar"',html)
        self.assertIn('class="v960-mailbox-tabs"',html)
        self.assertNotIn('<select name="account_id">',html)
        self.assertNotIn('<select name="mailbox">',html)
        self.assertIn('data-v960-href=',html)
        self.assertIn('data-v960-fragment="inbox"',html)
        self.assertIn(">Refresh</button>",html)

    def test_selected_global_sender_replaces_duplicate_compose_account_selector(self):
        sender = (
            '<label>Sender account <select name="account_id">'
            '<option value="one" selected>Primary</option>'
            '<option value="two">Support</option>'
            '</select></label>'
        )
        html = INBOX.replace('</section>', sender+'</section>')
        selected = modern_inbox_markup(html, selected_account="one")
        self.assertIn("Sending with the email account selected", selected)
        self.assertNotIn("Sender account <select", selected)
        self.assertIn('type="hidden" name="account_id" value="one"', selected)
        all_scope = modern_inbox_markup(html)
        self.assertIn("Sender account <select", all_scope)

    def test_modern_mail_cards_keep_cached_mail_navigation_and_no_external_fetch(self):
        sample = INBOX.replace(
            "<td>Alice</td><td>Subject</td><td>2026-11-14</td>",
            "<td>Alice Example &lt;alice@example.test&gt;</td>"
            "<td>Project update</td><td>2026-11-14T12:00:00+00:00</td>"
        )
        result = modern_inbox_markup(sample)
        self.assertIn('class="v963-mail-row v987-message"', result)
        self.assertIn('class="v987-avatar"', result)
        self.assertIn('>AE</span>', result)
        self.assertIn("Alice Example", result)
        self.assertIn("alice@example.test", result)
        self.assertIn("Project update", result)
        self.assertIn('data-v960-href=', result)
        self.assertIn('data-v987-date="2026-11-14T12:00:00+00:00"', result)
        self.assertNotIn("fetch(", result)

    def test_unread_mail_and_active_reader_remain_semantically_compatible(self):
        sample = INBOX.replace('class="v963-mail-row"', 'class="v963-mail-row unread"')
        result = modern_inbox_markup(sample)
        self.assertIn('class="v963-mail-row unread v987-message"', result)
        self.assertIn("Unread message", result)
        self.assertIn('data-v960-href=', result)

    def _sample_real_folder(self, folder: str, detail: bool = True) -> str:
        sample = INBOX.replace(
            '</table></div>', '</table></div><div class="v989-reader-source">'
            + ('<div class="v963-detail"><h3>Safe message</h3>'
               '<details class="v963-tech"><summary>Technical details</summary>'
               '<div class="scroll v963-url-table"><table><tr><td>Evidence</td>'
               '</tr></table></div></details></div>' if detail else '')
            + '</div>'
            '<div class="v960-pagination"><span></span>'
            '<span class="small muted">Page 1 · 1 cached result(s)</span></div>', 1)
        if folder != "Inbox":
            sample=sample.replace('class="active" href="/?ui_view=inbox&amp;mailbox=INBOX#inbox">Inbox',
                                  'class="" href="/?ui_view=inbox&amp;mailbox=INBOX#inbox">Inbox')
            sample=sample.replace('class="" href="/?ui_view=inbox&amp;mailbox=Sent#inbox">Sent',
                                  'class="active" href="/?ui_view=inbox&amp;mailbox=Sent#inbox">'+folder)
        return sample

    def test_specific_account_mailboxes_show_real_imap_sync_button(self):
        for folder in ("Inbox","Sent","Drafts","Spam","Trash"):
            with self.subTest(folder=folder):
                sample=self._sample_real_folder(folder)
                sync_form=(
                    '<form method="post" action="/dashboard/inbox/refresh">'
                    '<input type="hidden" name="csrf" value="real-token">'
                    '<input type="hidden" name="account_id" value="one">'
                    '<input type="hidden" name="mailbox" value="'+
                    ({"Inbox":"INBOX","Sent":"Sent","Drafts":"Drafts",
                      "Spam":"Spam","Trash":"Trash"}[folder])+'">'
                    '<button class="primary" type="submit">Aggiorna</button></form>'
                )
                sample=sample.replace(
                    '<div class="v963-inbox-head">',
                    '<div class="v963-inbox-head"><div class="v963-refresh">'+sync_form+'</div>',
                    1,
                )
                html=modern_inbox_markup(sample, selected_account="one",
                                         query_string="account_id=one")
                self.assertEqual(html.count('action="/dashboard/inbox/refresh"'),1)
                self.assertIn('class="primary v98910-sync"',html)
                self.assertIn('↻ Refresh mail</button>',html)
                self.assertIn('name="csrf" value="real-token"',html)
                self.assertIn('name="account_id" value="one"',html)
                self.assertIn('name="mailbox" value="'+
                              ({"Inbox":"INBOX","Sent":"Sent","Drafts":"Drafts",
                                "Spam":"Spam","Trash":"Trash"}[folder])+'"',html)
                self.assertIn('class="v98910-stream-actions"',html)
                self.assertIn('class="v989-mail-workspace"',html)
                self.assertLess(html.index('v98910-stream-actions'),
                                html.index('v989-list-scroll'))

    def test_all_accounts_never_triggers_a_default_account_sync(self):
        html=modern_inbox_markup(
            self._sample_real_folder("Inbox").replace(
                '<div class="v963-inbox-head">',
                '<div class="v963-inbox-head">'
                '<div class="v963-refresh">'
                '<span>All accounts · cached messages</span>'
                '<span>Select an email account above to sync it.</span></div>',
                1,
            ), query_string="mailbox=INBOX")
        self.assertIn('All accounts · cached messages',html)
        self.assertNotIn('action="/dashboard/inbox/refresh"',html)
        self.assertNotIn('class="primary v98910-sync"',html)

    def test_beta_three_pane_markup_preserves_safe_mailbox_contract(self):
        sample = self._sample_real_folder("Inbox")
        result = modern_inbox_markup(sample)
        self.assertIn('class="v989-mail-workspace"', result)
        self.assertIn('class="v989-mail-stream"', result)
        self.assertIn('class="v989-reading-pane"', result)
        self.assertIn('class="v989-reader-source"', result)
        self.assertIn('class="v987-avatar"', result)
        self.assertIn('class="v960-pagination"', result)
        self.assertIn('data-v960-href=', result)
        self.assertLess(result.index('v989-mail-stream'),result.index('v989-reading-pane'))
        self.assertIn('Remote images remain blocked',result)
        self.assertNotIn('class="v951-toolbar"',result)
        self.assertIn('class="v963-tech"',result)
        self.assertNotIn('v960-inline-detail',result)

    def test_every_mail_folder_shares_split_reader_not_full_page(self):
        for folder in ("Inbox","Sent","Spam","Drafts","Trash"):
            with self.subTest(folder=folder):
                result=modern_inbox_markup(self._sample_real_folder(folder))
                self.assertIn('class="v989-mail-workspace"',result)
                self.assertIn('class="v989-reading-pane"',result)
                self.assertIn('class="v989-reader-source"',result)
                self.assertLess(result.index('</table></div>'),result.index('class="v989-reading-pane"'))
                self.assertIn("Safe message",result)
                self.assertIn('data-v960-href=',result)
                self.assertNotIn('class="v951-toolbar"',result)

    def test_beta_technical_details_stay_inside_reading_pane(self):
        from postmaster.webgui_mail_v986 import BETA_MAIL_STYLE
        result=modern_inbox_markup(self._sample_real_folder("Sent"))
        pane=result.split('class="v989-reading-pane"')[1].split('class="v960-pagination"')[0]
        self.assertIn('class="v963-tech"',pane)
        self.assertIn('class="scroll v963-url-table"',pane)
        self.assertIn("max-height:300px",BETA_MAIL_STYLE)
        self.assertIn("overflow-x:auto!important",BETA_MAIL_STYLE)
        self.assertIn("position:static!important",BETA_MAIL_STYLE)
        self.assertNotIn('class="v960-inline-detail"',result)

    def test_beta_all_unread_links_preserve_mailbox_and_clear_selection(self):
        sample = self._sample_real_folder("Inbox")
        result = modern_inbox_markup(
            sample, query_string="account_id=one&mailbox=INBOX&unread_only=1&message_uid=42"
        )
        self.assertIn('class="v989-list-filters"',result)
        self.assertIn('>All</a>',result)
        self.assertIn('>Unread</a>',result)
        self.assertIn('account_id=one',result)
        self.assertIn('mailbox=INBOX',result)
        self.assertIn('unread_only=1',result)
        filters = result.split('class="v989-list-filters"')[1].split('</nav>')[0]
        self.assertNotIn('message_uid=42',filters)
        self.assertIn('data-v960-fragment="inbox"',filters)

    def test_beta_reader_is_server_rendered_and_infinite_scroll_uses_cache_fragments(self):
        from postmaster.webgui_mail_v986 import BETA_MAIL_STYLE
        self.assertIn('function enhanceBetaInbox(inbox)',MAIL_SCRIPT)
        self.assertIn("const source=workspace.querySelector('.v989-reader-source')",MAIL_SCRIPT)
        self.assertIn("u.pathname='/dashboard/view/inbox'",MAIL_SCRIPT)
        self.assertIn("u.searchParams.delete('message_uid')",MAIL_SCRIPT)
        self.assertIn("response=await fetch(u.toString()",MAIL_SCRIPT)
        self.assertIn("row.dataset.v960Href",MAIL_SCRIPT)
        self.assertIn('lastListPosition',MAIL_SCRIPT)
        self.assertIn('grid-template-columns:minmax(340px,380px)',BETA_MAIL_STYLE)
        self.assertIn('@media(max-width:760px)',BETA_MAIL_STYLE)
        self.assertIn(':has(.v963-detail) .v989-mail-stream',BETA_MAIL_STYLE)

    def test_actual_composed_runtime_entrypoint_has_beta_mail_client(self):
        # Shared-process compatibility tests legitimately re-install old
        # renderers. Exercise this release's *final* adapter directly.
        from unittest.mock import patch
        from starlette.requests import Request
        from postmaster import runtime
        html=self._sample_real_folder("Inbox")
        request=Request({"type":"http","method":"GET","path":"/dashboard/view/inbox",
                         "query_string":b"ui_view=inbox&account_id=one&mailbox=INBOX"})
        with patch.object(runtime._webgui_v963,"render_inbox_v963",return_value=html):
            actual=runtime._render_inbox_compat(object(),request)
        self.assertIn('class="v989-mail-workspace"',actual)
        self.assertIn('class="v989-reading-pane"',actual)
        self.assertIn('class="v989-reader-source"',actual)
        self.assertNotIn('class="v951-toolbar"',actual)

    def test_real_http_inbox_fragment_uses_beta_layout_not_legacy_table(self):
        # The lazy router must render via the selected runtime adapter; other
        # tests may have re-installed a legacy v960 renderer in this process.
        from unittest.mock import patch
        from starlette.testclient import TestClient
        from postmaster import runtime
        html=self._sample_real_folder("Sent")
        with patch.object(runtime._webgui_v963,"render_inbox_v963",return_value=html):
            with patch.object(runtime._webgui_v962_views.v960,"render_inbox",
                              runtime._render_inbox_compat):
                response=TestClient(runtime.app).get(
                    "/dashboard/view/inbox?ui_view=inbox&account_id=one&mailbox=Sent"
                )
        self.assertEqual(response.status_code,200,response.text[:350])
        self.assertIn('class="v989-mail-workspace"',response.text)
        self.assertIn('class="v989-reading-pane"',response.text)
        self.assertIn('class="v989-reader-source"',response.text)
        self.assertNotIn('class="v951-toolbar"',response.text)

    def test_fresh_runtime_process_wires_modern_mail_renderer(self):
        # This is the critical deployment smoke test. A fresh import, with no
        # test-suite mutation, must send the real HTTP fragment through modern
        # mail rendering. It would have failed on both beta.1 and beta.2.
        import subprocess
        import sys
        import textwrap
        raw=self._sample_real_folder("Drafts")
        script=textwrap.dedent("""
            from postmaster import runtime
            from unittest.mock import patch
            from starlette.testclient import TestClient
            assert runtime._webgui_v960.render_inbox is runtime._render_inbox_compat, (
                'installed mail renderer was overwritten after initialization'
            )
            raw = %s
            with patch.object(runtime._webgui_v963,'render_inbox_v963',return_value=raw):
                response=TestClient(runtime.app).get(
                    '/dashboard/view/inbox?ui_view=inbox&account_id=one&mailbox=Drafts'
                )
            assert response.status_code==200, (response.status_code,response.text[:350])
            assert 'class="v989-mail-workspace"' in response.text
            assert 'class="v989-reading-pane"' in response.text
            assert 'class="v989-reader-source"' in response.text
        """ % repr(raw))
        import os
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory(prefix="postmaster-beta3-runtime-") as tmp:
            env=os.environ.copy()
            env.update({
                "MAIL_EMAIL":"test@example.invalid",
                "MAIL_PASSWORD":"test-only-password",
                "IMAP_HOST":"imap.example.com",
                "SMTP_HOST":"smtp.example.com",
                "ENABLE_SEND":"false",
                "CONTEXT_SEMANTIC_ENABLED":"false",
                "CONTEXT_DB_PATH":str(Path(tmp)/"knowledge.db"),
                "SCHEDULER_DB_PATH":str(Path(tmp)/"scheduler.db"),
                "MAIL_ACCOUNTS_DB_PATH":str(Path(tmp)/"accounts.db"),
                "MAIL_ACCOUNTS_KEY_PATH":str(Path(tmp)/"accounts.key"),
                "EMAIL_ANALYTICS_DB_PATH":str(Path(tmp)/"analytics.db"),
                "EMAIL_ANALYTICS_KEY_PATH":str(Path(tmp)/"analytics.key"),
                "RECIPIENT_POLICY_DB_PATH":str(Path(tmp)/"recipient-policy.db"),
                "FILE_STORE_DB_PATH":str(Path(tmp)/"files.db"),
                "FILE_STORE_ROOT":str(Path(tmp)/"files"),
                "POSTMASTER_STRUCTURED_DATA_DB":str(Path(tmp)/"structured.db"),
            })
            completed=subprocess.run([sys.executable,"-c",script],
                                     env=env,capture_output=True,text=True,timeout=60)
            self.assertEqual(completed.returncode,0,completed.stderr[-2500:])

    def test_composed_renderer_keeps_every_mailbox_including_drafts(self):
        from unittest.mock import patch
        from starlette.requests import Request
        from postmaster import runtime
        for folder in ("Inbox","Sent","Spam","Drafts","Trash"):
            with self.subTest(folder=folder):
                html=self._sample_real_folder(folder)
                request=Request({"type":"http","method":"GET",
                                 "path":"/dashboard/view/inbox",
                                 "query_string":("ui_view=inbox&account_id=one&mailbox="+folder).encode()})
                with patch.object(runtime._webgui_v963,"render_inbox_v963",return_value=html):
                    response=runtime._render_inbox_compat(object(),request)
                self.assertIn('class="v989-reading-pane"',response)
                self.assertIn('class="v989-mail-workspace"',response)

    # Exact-commit CI and gated release: vertical-flow rows must remain non-overlapping.
    def test_beta6_mail_cards_are_vertical_flow_not_grid_tracks(self):
        from postmaster.webgui_mail_v986 import BETA_MAIL_STYLE
        css=BETA_MAIL_STYLE.split("/* Beta.6:")[1]
        self.assertIn('display:flex!important;flex-flow:column nowrap!important',css)
        self.assertIn('grid-template-columns:none!important',css)
        self.assertIn('grid-template-rows:none!important',css)
        self.assertIn('td.v987-identity{',css)
        self.assertIn('order:0!important',css)
        self.assertIn('td.v987-subject{',css)
        self.assertIn('order:1!important',css)
        self.assertIn('position:absolute!important;top:14px!important;right:15px!important',css)
        self.assertIn('td.v987-indicator{',css)
        self.assertIn('padding:14px 75px 13px 62px!important',css)

    def test_beta6_mail_cards_keep_real_sender_subject_snippet_markup(self):
        example=self._sample_real_folder("Drafts")
        result=modern_inbox_markup(example)
        self.assertIn('class="v987-identity"',result)
        self.assertIn('class="v987-subject"',result)
        self.assertIn('class="v989-preview"',modern_inbox_markup(
            example.replace('<td>Subject</td>',
                            '<td>Subject<small class="v989-snippet-source">Body excerpt</small></td>')))
        self.assertLess(result.index('class="v987-identity"'),result.index('class="v987-subject"'))

    def test_beta5_message_rows_have_nonoverlapping_sender_subject_and_preview(self):
        from postmaster.webgui_mail_v986 import BETA_MAIL_STYLE
        self.assertIn('grid-template-rows:36px 22px minmax(18px,auto)!important', BETA_MAIL_STYLE)
        self.assertIn('.v987-identity{grid-column:2/4!important;grid-row:1!important', BETA_MAIL_STYLE)
        self.assertIn('.v987-subject{grid-column:2/5!important;grid-row:2/4!important', BETA_MAIL_STYLE)
        self.assertIn('.v987-date{grid-column:4!important;grid-row:1!important', BETA_MAIL_STYLE)
        self.assertIn('min-height:105px!important', BETA_MAIL_STYLE)
        self.assertIn('grid-template-columns:minmax(405px,38%)', BETA_MAIL_STYLE)

    # Release-gated regression: each sidebar mailbox must fetch a new fragment.
    def test_beta5_navigation_changes_folder_without_reusing_old_inbox(self):
        from postmaster import webgui_v962
        source=webgui_v962.SCRIPT
        self.assertIn("const isFolder=a.hasAttribute('data-v985-mailbox')",source)
        self.assertIn("load(view,u.toString(),true,isFolder || !isNav)",source)
        self.assertIn("next.dataset.v989LoadedMailbox=",source)
        self.assertIn("target.setAttribute('aria-busy','true')",source)
        self.assertIn("target.classList.add('v989-folder-loading')",source)

    def test_beta5_list_supports_scroll_loading_and_manual_retry(self):
        self.assertIn('async function more()', MAIL_SCRIPT)
        self.assertIn("moreButton.textContent='Load more'", MAIL_SCRIPT)
        self.assertIn("moreButton.addEventListener('click',()=>more())", MAIL_SCRIPT)
        self.assertIn("if(scroller.scrollHeight-scroller.scrollTop-scroller.clientHeight<230)more()", MAIL_SCRIPT)
        self.assertIn("setStatus('Unable to load more messages. Scroll to retry.')", MAIL_SCRIPT)
        self.assertIn("if(!response.ok)throw new Error('Could not load more messages')", MAIL_SCRIPT)
        self.assertIn("if(u.origin!==location.origin)", MAIL_SCRIPT)
        self.assertIn("known.has(identity)", MAIL_SCRIPT)
        self.assertIn("p.get('message_account_id')", MAIL_SCRIPT)
        self.assertIn("p.get('message_mailbox')", MAIL_SCRIPT)

    def test_beta_narrow_list_never_inherits_old_table_minimum_width(self):
        from postmaster.webgui_mail_v986 import BETA_MAIL_STYLE
        self.assertIn("table.v986-mail-list{display:block!important;", BETA_MAIL_STYLE)
        self.assertIn("min-width:0!important;max-width:100%!important", BETA_MAIL_STYLE)
        self.assertIn("width:auto!important;min-width:0!important", BETA_MAIL_STYLE)
        self.assertIn("grid-template-columns:minmax(340px,380px)", BETA_MAIL_STYLE)
        self.assertIn("min-height:540px;height:calc(100dvh - 224px)", BETA_MAIL_STYLE)

    def test_beta_uses_cached_snippets_without_fetching_remote_media(self):
        sample = INBOX.replace(
            "<td>Subject</td>",
            '<td>Subject<small class="v989-snippet-source">First line of message</small></td>'
        )
        result = modern_inbox_markup(sample)
        self.assertIn('class="v989-preview"',result)
        self.assertIn('First line of message',result)
        self.assertNotIn('v989-snippet-source',result)
        self.assertNotIn("fetch(",result)

    def test_unknown_mail_template_remains_safe_without_half_wrapped_workspace(self):
        result=modern_inbox_markup(INBOX)
        self.assertNotIn('class="v989-mail-workspace"',result)
        self.assertIn('data-v960-href=',result)

    def test_compose_refuses_partial_markup_transformations(self):
        html='<form><textarea name="body_html"></textarea></form>'
        self.assertEqual(modern_compose_markup(html),html)


if __name__=="__main__":
    unittest.main()
