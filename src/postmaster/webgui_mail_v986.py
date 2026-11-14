"""v9.8.6 real Inbox/Compose UX over the existing cache and SMTP pipelines.

No new mail send handler, IMAP fetch, security bypass or default file attachments.
A single editor maps its output back into the original body/body_html/body_amp
fields; stored file choices remain opt-in through attachment_file_ids.
"""
from __future__ import annotations

from html import escape, unescape
import re
from urllib.parse import parse_qsl, urlencode
from typing import Any
from starlette.requests import Request


_SOURCE_BEGIN = '<label class="wide">Plain text <textarea name="body" rows="8"></textarea></label>'
_SOURCE_HTML = '<label class="wide">HTML (optional) <textarea name="body_html" rows="6" placeholder="&lt;p&gt;HTML alternative&lt;/p&gt;"></textarea></label>'
_SOURCE_AMP = '<label class="wide">AMP (optional, account capability required) <textarea name="body_amp" rows="5" placeholder="AMP-for-Email document"></textarea></label>'

EDITOR = """<div class="wide v986-composer-editor" data-v986-editor>
<div class="v986-composer-editor-head">
<label>Message format <select data-v986-format aria-label="Message format">
<option value="plain">Plain text</option><option value="html">HTML</option>
<option value="amp">AMP for Email</option></select></label>
<div class="v986-composer-modes" role="group" aria-label="Editor mode">
<button type="button" data-v986-editor-mode="visual" aria-pressed="true" class="active">Visual</button>
<button type="button" data-v986-editor-mode="code" aria-pressed="false">Code</button></div></div>
<div class="v986-rich-toolbar" role="toolbar" aria-label="Text formatting">
<button type="button" data-v986-rich-command="bold" title="Bold"><b>B</b></button>
<button type="button" data-v986-rich-command="italic" title="Italic"><i>I</i></button>
<button type="button" data-v986-rich-command="underline" title="Underline"><u>U</u></button>
<button type="button" data-v986-rich-command="insertUnorderedList" title="Bullet list">• List</button>
<button type="button" data-v986-rich-command="createLink" title="Insert URL">Link</button>
</div>
<div class="v986-rich-surface" data-v986-rich contenteditable="true" role="textbox"
 aria-label="Visual email editor" aria-multiline="true" spellcheck="true"
 data-placeholder="Write your message…"></div>
<textarea data-v986-code class="v986-code-surface" rows="12" hidden
 aria-label="Email source code"></textarea>
<div class="v986-compose-original" hidden aria-hidden="true">
<textarea name="body"></textarea><textarea name="body_html"></textarea>
<textarea name="body_amp"></textarea></div>
<p class="v986-editor-hint">HTML and AMP are optional formats. Visual editing supports basic safe formatting.
Use Code for advanced HTML or AMP markup; AMP requires an enabled, capable sender account.</p>
</div>"""


def modern_compose_markup(html: str) -> str:
    """Replace only the redundant three body inputs; preserve every send field."""
    source = _SOURCE_BEGIN + "\n" + _SOURCE_HTML + "\n" + _SOURCE_AMP
    if source not in html:
        # The original composer might have different line breaks; do not risk
        # a partially transformed form if its schema has changed.
        pieces = (_SOURCE_BEGIN, _SOURCE_HTML, _SOURCE_AMP)
        if not all(part in html for part in pieces):
            return html
        start = html.index(_SOURCE_BEGIN)
        finish = html.index(_SOURCE_AMP, start) + len(_SOURCE_AMP)
        html = html[:start] + EDITOR + html[finish:]
    else:
        html = html.replace(source, EDITOR, 1)
    return html.replace(
        '<summary>Compose inside Inbox</summary>',
        '<summary>Compose email</summary>', 1
    )


_SELECT_PATTERN = re.compile(
    r'<label>(?P<label>Account|Folder|Mailbox)\s*'
    r'<select name="(?P<name>account_id|mailbox)">'
    r'(?P<options>.*?)</select></label>',
    re.S,
)
_SELECTED_PATTERN = re.compile(r'<option value="([^"]*)" selected(?:\s|>)')
_FILTER_PATTERN = re.compile(
    r'(<form method="get" action="/" class="v951-toolbar" '
    r'data-v960-fragment="inbox">.*?</form>)',
    re.S,
)
_SELECTED_MAILBOX = re.compile(
    r'<nav class="v960-mailbox-tabs".*?'
    r'<a[^>]*\bclass="active"[^>]*>([^<]+)</a>',
    re.S,
)


_MAIL_ROW = re.compile(
    r'<tr class="(?P<classes>v963-mail-row[^"]*)" data-v960-href="(?P<href>[^"]*)">'
    r'\s*(?P<body>(?:<td>.*?</td>\s*){4})</tr>', re.S,
)
_MAIL_CELL = re.compile(r'<td>(.*?)</td>', re.S)


def _mail_message_card(match: re.Match[str]) -> str:
    """Upgrade cached message rows without changing their links or reader actions."""
    cells = _MAIL_CELL.findall(match.group("body"))
    if len(cells) != 4:
        return match.group(0)
    sender = unescape(re.sub(r"<[^>]*>", "", cells[1])).strip()
    name, sep, address = sender.rpartition(" <")
    if sep and address.endswith(">"):
        address = address[:-1]
    else:
        name, address = sender, ""
    name = name.strip().strip('"') or address or "Unknown sender"
    initials = "".join(part[0] for part in name.split()[:2] if part)[:2].upper() or "?"
    subject_html = cells[2].strip()
    snippet_match = re.search(r'<small class="v989-snippet-source">(.*?)</small>', subject_html, re.S)
    snippet = snippet_match.group(1) if snippet_match else ""
    account_match = re.search(r'<small class="v989-account-origin">(.*?)</small>', subject_html, re.S)
    account_origin = account_match.group(1) if account_match else ""
    subject = re.sub(r'<small class="v989-(?:snippet-source|account-origin)">.*?</small>',
                     "", subject_html, flags=re.S).strip() or "(No subject)"
    unread = " unread" in " " + match.group("classes")
    return (
        '<tr class="' + match.group("classes") + ' v987-message" data-v960-href="'
        + match.group("href") + '" aria-label="Open email">'
        + '<td class="v987-indicator">' + cells[0] + '</td>'
        + '<td class="v987-identity"><span class="v987-avatar" aria-hidden="true">'
        + escape(initials) + '</span><span class="v987-sender">'
        + '<strong>' + escape(name) + '</strong>'
        + ('<small>' + escape(address) + '</small>' if address else '')
        + '</span></td>'
        + '<td class="v987-subject"><span>' + subject + '</span>'
        + ('<small class="v989-preview">' + snippet + '</small>' if snippet else '')
        + ('<small class="v989-account-origin">' + account_origin + '</small>' if account_origin else '')
        + ('<small class="v987-unread-label">Unread message</small>' if unread else '')
        + '</td>'
        + '<td class="v987-date" data-v987-date="' + escape(
            unescape(re.sub(r"<[^>]*>", "", cells[3])), quote=True
        ) + '">' + cells[3] + '</td></tr>'
    )


def modern_inbox_markup(html: str, *, selected_account: str = "", query_string: str = "") -> str:
    if 'id="panel-inbox"' not in html:
        return html
    role = _SELECTED_MAILBOX.search(html)
    if role:
        heading = role.group(1)
        if heading in {"Inbox", "Sent", "Drafts", "Spam", "Trash"}:
            html = html.replace("<h2>Inbox</h2>", "<h2>" + heading + "</h2>", 1)

    def hidden_select(m: re.Match[str]) -> str:
        match = _SELECTED_PATTERN.search(m.group("options"))
        if not match:
            return m.group(0)  # Never lose a select if no valid default exists.
        return (
            '<input type="hidden" name="' + escape(m.group("name"),quote=True)
            + '" value="' + match.group(1) + '">'
        )
    # The header already has an account selector and the sidebar has mailbox
    # navigation. Keep their selected values in GET filters, without duplicates.
    # Sender selection remains available only when no global account was chosen.
    if selected_account:
        def sender_with_global_account(match: re.Match[str]) -> str:
            choices = match.group(1)
            selected = _SELECTED_PATTERN.search(choices)
            if not selected or selected.group(1) != escape(selected_account,quote=True):
                return match.group(0)
            return (
                '<input type="hidden" name="account_id" value="'
                + escape(selected_account,quote=True) + '">'
                '<div class="v986-sender-context">Sending with the email account selected '
                'in the global header.</div>'
            )
        html = re.sub(
            r'<label>Sender account <select name="account_id">(.*?)</select></label>',
            sender_with_global_account,html,count=1,flags=re.S,
        )
    def collapsible(m: re.Match[str]) -> str:
        form = _SELECT_PATTERN.sub(hidden_select, m.group(1))
        return (
            '<details class="v986-mail-filters">'
            '<summary>Advanced mailbox filters</summary>' + form + '</details>'
        )
    # The shell provides the account selector, folder navigation and email search.
    # No second account/mailbox/search toolbar in the mail client.
    html = _FILTER_PATTERN.sub("", html, count=1)
    html = html.replace('>Aggiorna</button>', '>Refresh</button>')
    html = html.replace("Use Aggiorna for an explicit incremental sync.", "Use Refresh for an explicit incremental sync.")
    html = re.sub(
        r'(<div class="v963-refresh"><span class="v963-chip">)[^<]*(</span>)',
        r'\1Cached mailbox\2',html,count=1,
    )
    html = html.replace(
        '<div class="v963-inbox-head">',
        '<div class="v963-inbox-head v986-inbox-head">',1,
    )
    html = html.replace(
        '<table class="v960-mail-table">',
        '<table class="v960-mail-table v986-mail-list">',1,
    )
    # Preserve the original table, row navigation and inline safe reader while
    # displaying a modern mail-list layout (no extra IMAP calls or guessed snippets).
    html = _MAIL_ROW.sub(_mail_message_card, html)
    return beta_mailbox_layout(html, query_string=query_string)


def beta_mailbox_layout(html: str, *, query_string: str = "") -> str:
    """Real split view: the safe reader is a sibling of the message list, not a table row.

    This structure is generated server-side so it is correct before any JS runs,
    including Sent messages, reply actions and the technical details inspector.
    """
    opening = '<div class="scroll"><table class="v960-mail-table v986-mail-list">'
    reader = '<div class="v989-reader-source">'
    pagination = '<div class="v960-pagination">'
    if opening not in html or reader not in html or pagination not in html:
        return html  # Never break an unexpected legacy Inbox renderer.
    # The legacy server renders a real, CSRF-verified IMAP sync form only for
    # a specific account. Move it to the message-list header where the user
    # expects the mailbox Refresh action. All accounts intentionally has no
    # such form and remains cache-only (no implicit multi-account sync).
    sync_match = re.search(
        r'<form method="post" action="/dashboard/inbox/refresh">.*?</form>',
        html, flags=re.S,
    )
    sync_form = ""
    if sync_match:
        sync_form = sync_match.group(0).replace(
            '<button class="primary" type="submit">Refresh</button>',
            '<button class="primary v98910-sync" type="submit" '
            'aria-label="Fetch new messages from the selected email account">'
            '↻ Refresh mail</button>',
        )
        sync_form = sync_form.replace(
            '<button class="primary" type="submit">Aggiorna</button>',
            '<button class="primary v98910-sync" type="submit" '
            'aria-label="Fetch new messages from the selected email account">'
            '↻ Refresh mail</button>',
        )
        html = html[:sync_match.start()] + html[sync_match.end():]
    params = dict(parse_qsl(query_string, keep_blank_values=False))
    for key in ("message_uid", "full_html", "full_html_intent", "compose_mode"):
        params.pop(key, None)
    params["ui_view"] = "inbox"
    active_unread = params.get("unread_only") == "1"
    all_params = dict(params)
    all_params.pop("unread_only", None)
    unread_params = dict(all_params, unread_only="1")
    all_url = "/?" + urlencode(all_params) + "#inbox"
    unread_url = "/?" + urlencode(unread_params) + "#inbox"
    tabs = (
        '<nav class="v989-list-filters" aria-label="Message list filters">'
        '<a data-v960-fragment="inbox" class="' + ("" if active_unread else "active")
        + '" href="' + escape(all_url, quote=True) + '">All</a>'
        '<a data-v960-fragment="inbox" class="' + ("active" if active_unread else "")
        + '" href="' + escape(unread_url, quote=True) + '">Unread</a></nav>'
    )
    begin = (
        '<div class="v989-mail-workspace" data-v989-mail-workspace>'
        '<div class="v989-mail-stream">'
        '<div class="v989-stream-header"><div><strong>Messages</strong>'
        '<span class="v989-stream-subtitle">Select a message to read</span></div>'
        '<div class="v98910-stream-actions"><span class="v989-stream-count"></span>'
        + sync_form + '</div></div>'
        + tabs +
        '<div class="scroll v989-list-scroll">'
        '<table class="v960-mail-table v986-mail-list">'
    )
    # The original table closes before the separately rendered reader-source;
    # no regex parsing, nested table extraction, or DOM reparenting is required.
    html = html.replace(opening, begin, 1)
    boundary = '</table></div>' + reader
    if boundary not in html:
        return html  # Reader and message list must be contiguous siblings.
    html = html.replace(
        boundary,
        '</table></div><div class="v989-scroll-status" aria-live="polite"></div>'
        '</div><section class="v989-reading-pane" aria-label="Message reading pane">'
        + reader, 1,
    )
    html = html.replace(
        '</div>' + pagination,
        '</div><div class="v989-reader-empty">'
        '<span class="v989-reader-envelope" aria-hidden="true">✉</span>'
        '<h3>Your messages</h3><p>Select a message on the left to read it here.</p>'
        '<p>Remote images remain blocked unless explicitly approved.</p>'
        '</div></section></div>' + pagination, 1,
    )
    html = html.replace(
        "Cache-first mailbox view · IMAP remains source of truth · Safe Email is the default.",
        "Your mail, organized in one place.", 1,
    )
    return html


MAIL_STYLE = r"""
/* Inbox v5: one account selector globally, provider folders in the sidebar. */
#panel-inbox .v986-inbox-head{display:flex;justify-content:space-between;align-items:center;gap:12px;
 flex-wrap:wrap;border:0!important;padding:8px 0 15px!important}
#panel-inbox .v986-inbox-head h2{font-size:22px!important;letter-spacing:-.5px;margin:0}
#panel-inbox .v986-inbox-head p{font-size:11px!important;color:var(--v970-muted)}
#panel-inbox .v963-refresh{display:flex;align-items:center;gap:10px}
#panel-inbox .v963-refresh button{padding:8px 13px!important;border-radius:8px!important}
#panel-inbox .v960-mailbox-tabs{display:none!important}
#panel-inbox .v986-mail-filters{margin:10px 0 17px;padding:10px 13px;border:1px solid var(--v970-line);
 border-radius:9px;background:var(--v970-surface-2)}
#panel-inbox .v986-mail-filters summary{font-size:12px;font-weight:750;cursor:pointer}
#panel-inbox .v986-mail-filters form{padding:12px 0 2px!important;background:transparent!important;
 box-shadow:none!important;border:0!important;display:flex;flex-wrap:wrap;gap:10px}
#panel-inbox .v986-mail-filters input[type=hidden]{display:none}
#panel-inbox .v986-mail-filters form label{display:flex;flex-direction:column;gap:4px;min-width:140px}
#panel-inbox .v986-mail-filters label:has(input[type=checkbox]){flex-direction:row;align-items:center;min-width:auto}
#panel-inbox .scroll:has(.v986-mail-list){border:1px solid var(--v970-line);border-radius:10px;overflow-x:auto}
#panel-inbox table.v986-mail-list{display:block!important;width:100%;border:0!important;border-collapse:separate!important}
#panel-inbox .v986-mail-list thead{display:none!important}
#panel-inbox .v986-mail-list tbody{display:block!important}
#panel-inbox .v986-mail-list tr.v963-mail-row{display:grid!important;
 grid-template-columns:12px minmax(210px,0.9fr) minmax(260px,1.8fr) 112px;
 gap:12px;align-items:center;padding:0 18px!important;min-height:78px;
 background:var(--v970-surface);border-bottom:1px solid var(--v970-line)!important;
 cursor:pointer;transition:background .12s ease}
#panel-inbox .v986-mail-list tr.v963-mail-row:hover,
#panel-inbox .v986-mail-list tr.v963-mail-row:focus{background:var(--v970-accent-soft)!important;outline:0}
#panel-inbox .v986-mail-list tr.v963-mail-row.unread{background:var(--v970-surface-2)}
#panel-inbox .v986-mail-list tr.v963-mail-row td{display:block!important;min-width:0;
 border:0!important;padding:13px 0!important;overflow:hidden;text-overflow:ellipsis;
 white-space:nowrap;font-size:12px}
#panel-inbox .v987-message .v987-identity{display:flex!important;align-items:center;gap:11px}
#panel-inbox .v987-avatar{width:37px;height:37px;flex:0 0 37px;border-radius:50%;
 background:var(--v970-accent-soft);color:var(--v970-accent);display:grid;
 place-items:center;font-size:12px;letter-spacing:.3px;font-weight:800}
#panel-inbox .v987-sender{min-width:0;display:flex;flex-direction:column;gap:3px;
 overflow:hidden;text-overflow:ellipsis}
#panel-inbox .v987-sender strong{overflow:hidden;text-overflow:ellipsis;
 font-size:13px;font-weight:650}
#panel-inbox .v987-sender small{color:var(--v970-muted);font-size:11px;
 overflow:hidden;text-overflow:ellipsis}
#panel-inbox .v987-message.unread .v987-sender strong,
#panel-inbox .v987-message.unread .v987-subject>span{font-weight:850}
#panel-inbox .v987-subject>span{display:block;overflow:hidden;text-overflow:ellipsis;
 font-size:13px;color:var(--v970-text);font-weight:550}
#panel-inbox .v987-unread-label{font-size:10px;color:var(--v970-accent);font-weight:700;
 display:block;margin-top:5px}
#panel-inbox .v987-date{text-align:right!important;color:var(--v970-muted);
 font-size:11px!important;font-variant-numeric:tabular-nums}
#panel-inbox .v987-message.unread .v987-date{color:var(--v970-text);font-weight:700}
#panel-inbox .v987-indicator .v963-unread-dot{box-shadow:0 0 0 3px var(--v970-accent-soft)}
#panel-inbox .v986-mail-list tr.v960-inline-detail{display:block!important;padding:15px;border-bottom:1px solid var(--v970-line)}
#panel-inbox .v986-mail-list tr.v960-inline-detail td{display:block!important;width:100%;padding:0!important}
#panel-inbox .v986-mail-list tr:not(.v963-mail-row):not(.v960-inline-detail){display:block!important;padding:14px}
#panel-inbox .v986-mail-list tr:not(.v963-mail-row):not(.v960-inline-detail) td{display:block}
#panel-inbox .v960-pagination{display:flex;align-items:center;justify-content:space-between;gap:10px;
 padding:13px 4px;font-size:11px}
#panel-inbox .v963-proxy-card{margin-top:20px!important}
#panel-inbox details.v971-floating-compose{border:1px solid var(--v970-line)!important;border-radius:11px;
 margin:15px 0!important;box-shadow:none!important;background:var(--v970-surface)}
/* The sidebar Compose action is canonical; never show a second floating action. */
#panel-inbox details.v971-floating-compose:not([open]){display:none!important}
#panel-inbox details.v971-floating-compose[open]{position:relative!important;inset:auto!important;
 right:auto!important;bottom:auto!important;width:100%!important;max-height:none!important;overflow:visible!important;
 z-index:auto!important;box-shadow:none!important}
#panel-inbox details.v971-floating-compose>summary{font-size:14px;font-weight:800;padding:11px!important;
 border-radius:9px;background:var(--v970-surface-2)}
#panel-inbox .v971-floating-compose form{padding:10px 12px 15px}
#panel-inbox .v971-floating-compose .v951-formgrid{display:grid;
 grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}
#panel-inbox .v971-floating-compose .v951-formgrid .wide{grid-column:1/-1}
#panel-inbox .v971-floating-compose .v951-formgrid>label{font-size:11px;display:flex;
 flex-direction:column;gap:5px}
#panel-inbox .v986-sender-context{padding:9px 12px;font-size:11px;color:var(--v970-muted);
 border:1px solid var(--v970-line);border-radius:8px;background:var(--v970-surface-2)}
#panel-inbox .v971-floating-compose .v951-formgrid>label input,
#panel-inbox .v971-floating-compose .v951-formgrid>label select{
 min-height:36px!important;border:1px solid var(--v970-line)}
.v986-composer-editor{display:flex;flex-direction:column;gap:9px;grid-column:1/-1;
 border:1px solid var(--v970-line);border-radius:10px;background:var(--v970-surface);padding:12px}
.v986-composer-editor-head{display:flex;align-items:end;justify-content:space-between;gap:10px;flex-wrap:wrap}
.v986-composer-editor-head>label{display:flex;flex-direction:column;gap:4px;font-size:11px;font-weight:700}
.v986-composer-editor-head select{min-width:180px;padding:7px;border-radius:7px}
.v986-composer-modes{display:flex;gap:3px;border:1px solid var(--v970-line);border-radius:8px;padding:3px}
.v986-composer-modes button{padding:6px 12px;background:transparent;border:0!important;border-radius:6px!important;
 color:var(--v970-muted)}
.v986-composer-modes button.active{background:var(--v970-accent-soft)!important;color:var(--v970-accent)!important;font-weight:800}
.v986-rich-toolbar{display:flex;gap:5px;border-top:1px solid var(--v970-line);padding-top:9px}
.v986-rich-toolbar button{border:1px solid var(--v970-line);padding:5px 8px!important;min-width:34px;background:var(--v970-surface-2)}
.v986-rich-toolbar[hidden]{display:none!important}
.v986-rich-surface,.v986-code-surface{min-height:175px;width:100%;padding:12px;
 border:1px solid var(--v970-line);border-radius:9px;background:var(--v970-surface);
 color:var(--v970-text);font-size:13px;line-height:1.6;outline:0;overflow:auto}
.v986-rich-surface:focus,.v986-code-surface:focus{border-color:var(--v970-accent)}
.v986-rich-surface:empty:before{content:attr(data-placeholder);color:var(--v970-muted);pointer-events:none}
.v986-code-surface{font-family:ui-monospace,SFMono-Regular,monospace;font-size:12px}
.v986-code-surface[hidden],.v986-rich-surface[hidden]{display:none!important}
.v986-editor-hint{font-size:10px;color:var(--v970-muted);margin:1px 0}
.v986-composer-editor .v986-compose-original{display:none!important}
.v986-attachments{grid-column:1/-1;padding:9px 13px;border:1px solid var(--v970-line);
 border-radius:9px;background:var(--v970-surface-2)}
.v986-attachments details>summary{cursor:pointer;font-size:12px;font-weight:750}
.v986-file-choices{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:5px;margin:10px 0}
.v986-file-choices label{display:flex;align-items:center;gap:8px;font-size:11px}
.v986-file-choices label input{flex:0 0 auto}
.v986-attachment-info{color:var(--v970-muted);font-size:10px}
#panel-inbox .v971-floating-compose .v971-file-picker{display:none!important}
#panel-inbox .v971-floating-compose .v971-file-picker>option:checked{display:block}
@media(max-width:890px){
 #panel-inbox .v986-mail-list tr.v963-mail-row{grid-template-columns:10px minmax(170px,1fr) minmax(160px,1.3fr) 85px;gap:8px}
 #panel-inbox .v971-floating-compose .v951-formgrid{grid-template-columns:1fr}
}
@media(max-width:560px){
 #panel-inbox .v986-mail-list tr.v963-mail-row{grid-template-columns:8px minmax(0,1fr) 80px;
  grid-template-rows:36px 28px;padding:4px 12px!important;gap:0 8px;min-height:77px}
 #panel-inbox .v987-indicator{grid-row:1/3;grid-column:1}
 #panel-inbox .v987-identity{grid-row:1;grid-column:2;gap:7px!important}
 #panel-inbox .v987-avatar{width:27px;height:27px;flex-basis:27px;font-size:10px}
 #panel-inbox .v987-sender small{display:none}
 #panel-inbox .v987-subject{grid-row:2;grid-column:2/4;padding:0!important;padding-left:35px!important}
 #panel-inbox .v987-date{grid-row:1;grid-column:3}
 #panel-inbox .v987-unread-label{display:none}

}
"""

BETA_MAIL_STYLE = r"""
/* Beta vertical mail client: folder sidebar is already provided by the shell. */
#panel-inbox .v989-mail-workspace{display:grid;grid-template-columns:minmax(340px,380px) minmax(0,1fr);
 min-height:540px;height:calc(100dvh - 224px);border:1px solid var(--v970-line);
 border-radius:14px;overflow:hidden;background:var(--v970-surface);margin-top:12px}
#panel-inbox .v960-pagination[hidden]{display:none!important}
#panel-inbox .v989-scroll-status{flex-shrink:0;min-height:25px;
 font-size:11px;text-align:center;color:var(--v970-muted);padding:5px 8px}
#panel-inbox .v989-reader-source{min-width:0;max-width:100%;overflow-wrap:anywhere}
#panel-inbox .v989-reader-source:empty{display:none}
#panel-inbox .v989-reading-pane:has(.v963-detail) .v989-reader-empty,
#panel-inbox .v989-reading-pane:has(.flash) .v989-reader-empty{display:none}
#panel-inbox .v989-reader-source .v963-tech{margin-top:18px;border-top:1px solid var(--v970-line);
 padding:12px 0;max-width:100%;min-width:0}
#panel-inbox .v989-reader-source .v963-tech summary{font-size:12px;cursor:pointer}
#panel-inbox .v989-reader-source .v963-tech .v963-tech-grid{grid-template-columns:repeat(2,minmax(0,1fr))}
#panel-inbox .v989-reader-source .v963-url-table{max-height:300px;max-width:100%;
 overflow-x:auto!important;overflow-y:auto!important;display:block}
#panel-inbox .v989-reader-source .v963-url-table table{width:max-content;min-width:760px;
 max-width:none;table-layout:auto}
#panel-inbox .v989-reader-source .v963-url-table th,
#panel-inbox .v989-reader-source .v963-url-table td{max-width:195px;min-width:90px;
 overflow-wrap:anywhere;word-break:break-word;vertical-align:top;white-space:normal}
#panel-inbox .v989-reader-source .v963-safe-email{overflow-x:auto;max-width:100%;
 overflow-wrap:anywhere;font-size:13px;line-height:1.65}
#panel-inbox .v989-reader-source .v963-safe-email pre{white-space:pre-wrap;
 overflow-wrap:anywhere;font-family:inherit;margin:0;line-height:1.65}
#panel-inbox .v989-reader-source .v963-inbox-head h3{font-size:18px;line-height:1.35}
#panel-inbox .v989-reader-source .v963-inbox-head p{font-size:12px;line-height:1.5}
/* Override the legacy mobile reader's fixed full-screen mode in every folder. */
@media(max-width:820px){
 #panel-inbox:has(.v989-mail-workspace)>.v963-inbox-head,
 #panel-inbox:has(.v989-mail-workspace)>.v960-pagination{display:flex!important}
 #panel-inbox:has(.v989-mail-workspace) .v989-mail-stream{display:flex}
 #panel-inbox:has(.v989-mail-workspace) .v989-list-scroll{display:block;max-width:100%!important}
 #panel-inbox .v989-reading-pane .v963-detail{position:static!important;inset:auto!important;
  z-index:auto!important;max-width:100%!important;width:100%!important;
  max-height:none!important;overflow:visible!important;background:transparent!important}
 #panel-inbox .v989-reading-pane .v963-detail-actions{position:static!important;top:auto!important}
 #panel-inbox:has(.v989-mail-workspace)>.v963-proxy-card{display:none}
}
#panel-inbox .v989-mail-stream{min-width:0;display:flex;flex-direction:column;
 border-right:1px solid var(--v970-line);overflow:hidden}
#panel-inbox .v989-stream-header{display:flex;justify-content:space-between;align-items:center;gap:8px;
 padding:17px 16px 14px;border-bottom:1px solid var(--v970-line)}
#panel-inbox .v989-stream-header strong{font-size:16px;font-weight:800}
#panel-inbox .v989-stream-subtitle{display:block;font-size:11px;color:var(--v970-muted);margin-top:4px}
#panel-inbox .v989-stream-count{font-size:11px;color:var(--v970-muted)}
#panel-inbox .v989-list-filters{display:flex;gap:5px;padding:8px 14px;
 border-bottom:1px solid var(--v970-line)}
#panel-inbox .v989-list-filters a{display:inline-flex;align-items:center;
 padding:6px 11px;border-radius:16px;font-size:12px;text-decoration:none;
 color:var(--v970-muted);border:1px solid transparent}
#panel-inbox .v989-list-filters a.active{background:var(--v970-accent-soft);
 color:var(--v970-accent);font-weight:750}
#panel-inbox .v989-list-filters a:hover{border-color:var(--v970-line)}
#panel-inbox .v962-perfline{display:none}
#panel-inbox .v989-list-scroll{border:0!important;border-radius:0!important;min-height:0;
 flex:1;overflow-y:auto!important;overflow-x:hidden!important}
#panel-inbox .v989-list-scroll table.v986-mail-list{display:block!important;
 width:100%!important;min-width:0!important;max-width:100%!important;
 border:0!important;table-layout:auto!important}
#panel-inbox .v989-list-scroll table.v986-mail-list thead{display:none!important}
#panel-inbox .v989-list-scroll .v986-mail-list tbody{display:block!important;
 width:100%!important;min-width:0!important}
#panel-inbox .v989-list-scroll .v986-mail-list tr.v987-message{display:grid!important;
 grid-template-columns:8px 36px minmax(0,1fr) 66px!important;grid-template-rows:25px auto;
 min-height:90px!important;gap:0 7px!important;padding:13px 13px 12px!important;
 align-items:start;border-bottom:1px solid var(--v970-line)!important}
#panel-inbox .v989-list-scroll .v987-message td{padding:0!important;
 width:auto!important;min-width:0!important;max-width:100%!important;
 line-height:1.45;box-sizing:border-box}
#panel-inbox .v989-list-scroll .v987-indicator{grid-column:1;grid-row:1/3;padding-top:5px!important}
#panel-inbox .v989-list-scroll .v987-identity{grid-column:2/4;grid-row:1;display:flex!important;
 align-items:center;gap:9px!important;padding:0!important;min-width:0}
#panel-inbox .v989-list-scroll .v987-avatar{width:32px;height:32px;flex-basis:32px;border-radius:50%;
 font-size:11px;margin-top:0}
#panel-inbox .v989-list-scroll .v987-sender{display:block;min-width:0}
#panel-inbox .v989-list-scroll .v987-sender strong{display:block;font-size:13px;
 white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#panel-inbox .v989-list-scroll .v987-sender small{display:none}
#panel-inbox .v989-list-scroll .v987-subject{grid-column:2/5;grid-row:2;
 padding:4px 0 0 4px!important;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#panel-inbox .v989-list-scroll .v987-subject>span{font-size:12px;color:var(--v970-muted);
 display:block;overflow:hidden;text-overflow:ellipsis}
#panel-inbox .v989-list-scroll .v989-preview{display:block;color:var(--v970-muted);
 font-size:11px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;margin-top:4px}
#panel-inbox .v989-list-scroll .v987-message.unread .v987-subject>span{color:var(--v970-text)}
#panel-inbox .v989-list-scroll .v987-date{grid-column:4;grid-row:1;padding-top:4px!important;
 font-size:10px!important;text-align:right!important;white-space:nowrap}
#panel-inbox .v989-list-scroll .v987-message.v989-selected{background:var(--v970-accent-soft)!important;
 box-shadow:inset 3px 0 var(--v970-accent)}
#panel-inbox .v989-list-scroll .v987-unread-label{display:none}
#panel-inbox .v989-reading-pane{min-width:0;min-height:0;max-width:100%;overflow:auto;position:relative;
 background:var(--v970-surface)}
#panel-inbox .v989-reader-empty{max-width:340px;margin:clamp(80px,15vh,170px) auto;
 text-align:center;padding:18px;color:var(--v970-muted)}
#panel-inbox .v989-reader-empty h3{color:var(--v970-text);font-size:18px;margin:16px 0 8px}
#panel-inbox .v989-reader-empty p{font-size:12px;line-height:1.7}
#panel-inbox .v989-reader-envelope{font-size:43px;display:inline-grid;place-items:center;
 width:84px;height:84px;background:var(--v970-accent-soft);color:var(--v970-accent);
 border-radius:22px}
#panel-inbox .v989-reading-pane:has(.v963-detail)>.v989-reader-empty{display:none}
#panel-inbox .v989-reading-pane .v963-detail{position:static!important;inset:auto!important;
 z-index:auto!important;margin:0!important;min-height:100%;width:100%!important;
 overflow:visible!important;padding:25px!important;border:0!important;
 border-radius:0!important;box-shadow:none!important;background:transparent!important}
#panel-inbox .v989-reading-pane .v963-detail>.v963-inbox-head{display:flex!important;
 flex-wrap:wrap!important}
#panel-inbox .v989-reading-pane .v963-detail-actions{position:static!important;
 background:transparent!important;flex-wrap:wrap}
#panel-inbox .v989-reading-pane .v963-safe-email{min-height:190px}
#panel-inbox .v989-reading-pane .v960-reader{max-width:100%!important;overflow-wrap:anywhere}
#panel-inbox .v989-mail-filters{margin:9px 0}
#panel-inbox:has(.v989-mail-workspace)>.v960-pagination{margin:8px 0}
@media(max-width:1000px){
 #panel-inbox .v989-mail-workspace{grid-template-columns:minmax(260px,320px) minmax(0,1fr);height:70vh}
 #panel-inbox .v989-reading-pane .v963-detail{padding:15px!important}
}
#panel-inbox.v989-folder-loading .v989-mail-workspace{position:relative;pointer-events:none}
#panel-inbox.v989-folder-loading .v989-mail-workspace::after{
 content:"Loading mailbox…";position:absolute;inset:0;z-index:8;
 display:flex;align-items:center;justify-content:center;font-size:14px;font-weight:700;
 color:var(--v970-text);background:color-mix(in srgb,var(--v970-surface) 86%,transparent);
 backdrop-filter:blur(2px)
}
/* Beta.5: explicit rows override the old table and 25px grid tracks.
   Sender, subject, and snippet never occupy the same line. */
@media(min-width:761px){
 #panel-inbox .v989-mail-workspace{grid-template-columns:minmax(405px,38%) minmax(0,1fr)!important;
  height:calc(100dvh - 225px)!important;min-height:490px}
}
#panel-inbox .v989-list-scroll .v986-mail-list tr.v987-message{
 display:grid!important;grid-template-columns:6px 36px minmax(0,1fr) 62px!important;
 grid-template-rows:36px 22px minmax(18px,auto)!important;
 column-gap:9px!important;row-gap:2px!important;
 min-height:105px!important;padding:13px 15px!important;align-items:start!important;
 align-content:start!important;box-sizing:border-box!important;
}
#panel-inbox .v989-list-scroll .v987-indicator{grid-column:1!important;grid-row:1/4!important;
 padding-top:9px!important}
#panel-inbox .v989-list-scroll .v987-identity{grid-column:2/4!important;grid-row:1!important;
 display:flex!important;align-items:center!important;align-self:start!important;
 min-height:35px!important;gap:9px!important;overflow:hidden!important}
#panel-inbox .v989-list-scroll .v987-avatar{flex:0 0 34px!important;
 width:34px!important;height:34px!important;line-height:34px!important}
#panel-inbox .v989-list-scroll .v987-sender{display:flex!important;align-items:flex-start!important;
 justify-content:center!important;min-width:0!important;max-width:100%!important;
 height:34px!important;overflow:hidden!important}
#panel-inbox .v989-list-scroll .v987-sender strong{display:block!important;
 width:100%!important;max-width:100%!important;line-height:34px!important;
 white-space:nowrap!important;text-overflow:ellipsis!important;overflow:hidden!important}
#panel-inbox .v989-list-scroll .v987-subject{grid-column:2/5!important;grid-row:2/4!important;
 padding:0!important;margin:0!important;margin-left:0!important;align-self:start!important;
 display:block!important;white-space:normal!important;overflow:hidden!important;
 max-width:100%!important;height:auto!important}
#panel-inbox .v989-list-scroll .v987-subject>span{display:block!important;
 line-height:20px!important;height:20px!important;font-size:12px!important;
 white-space:nowrap!important;text-overflow:ellipsis!important;overflow:hidden!important}
#panel-inbox .v989-list-scroll .v989-preview{display:block!important;
 line-height:19px!important;height:19px!important;margin:2px 0 0!important;
 white-space:nowrap!important;overflow:hidden!important;text-overflow:ellipsis!important}
#panel-inbox .v989-list-scroll .v987-date{grid-column:4!important;grid-row:1!important;
 align-self:start!important;justify-self:end!important;line-height:34px!important;
 padding:0!important;margin:0!important;white-space:nowrap!important}
#panel-inbox .v989-list-scroll{overscroll-behavior:contain!important;scrollbar-gutter:stable}
#panel-inbox .v989-scroll-status{min-height:38px!important;padding:6px 12px!important;
 display:flex!important;align-items:center;justify-content:center;gap:10px}
#panel-inbox .v989-scroll-status button{font-size:11px;padding:4px 10px}
@media(min-width:761px) and (max-width:1150px){
 #panel-inbox .v989-mail-workspace{grid-template-columns:minmax(340px,42%) minmax(0,1fr)!important}
}
@media(max-width:760px){
 #panel-inbox .v989-mail-workspace{display:block;height:auto;min-height:65vh;overflow:visible}
 #panel-inbox .v989-mail-stream{border:0;min-height:60vh}
 #panel-inbox .v989-list-scroll{max-height:70vh;min-width:0!important}
 #panel-inbox .v989-reading-pane{display:none;min-height:60vh}
 #panel-inbox .v989-mail-workspace:has(.v963-detail) .v989-mail-stream{display:none!important}
 #panel-inbox .v989-mail-workspace:has(.v963-detail) .v989-reading-pane{display:block!important}
 #panel-inbox .v989-mail-workspace:has(.v963-detail){height:auto}
 #panel-inbox .v989-reading-pane .v963-detail{padding:16px!important}
}
#panel-inbox .v989-list-scroll .v989-account-origin{
 display:block!important;overflow:hidden!important;text-overflow:ellipsis!important;
 white-space:nowrap!important;font-size:10px!important;line-height:16px!important;
 margin-top:2px!important;color:var(--v970-accent)!important;font-weight:600
}
#panel-inbox .v989-list-scroll .v987-message:has(.v989-account-origin){
 min-height:113px!important
}
/* Beta.10: explicit per-account IMAP refresh in mailbox list heading.
   No refresh form is rendered for All accounts (cache-only aggregate view). */
#panel-inbox .v98910-stream-actions{display:flex;align-items:center;
 justify-content:flex-end;gap:9px;flex-wrap:wrap;min-width:0}
#panel-inbox .v98910-stream-actions form{margin:0!important;display:flex;align-items:center}
#panel-inbox .v98910-stream-actions .v98910-sync{display:inline-flex!important;
 align-items:center;justify-content:center;white-space:nowrap;
 padding:7px 11px!important;font-size:12px!important;min-height:32px;
 border-radius:8px!important;cursor:pointer!important}
#panel-inbox .v98910-stream-actions .v98910-sync:disabled{opacity:.62;cursor:progress!important}
@media(max-width:760px){
 #panel-inbox .v98910-stream-actions{flex-wrap:wrap}
 #panel-inbox .v98910-stream-actions .v98910-sync{padding:6px 8px!important;font-size:11px!important}
}
/* Beta.6: remove CSS-grid table-row layout entirely. Grid positioning in
   browser table fragments and competing inherited td rules produced overlapping
   sender/subject text despite the earlier fixed grid tracks. Every card now
   follows normal vertical flex flow; absolutely positioned avatar and date
   cannot change subject line placement. */
#panel-inbox .v989-list-scroll .v986-mail-list tr.v987-message{
 display:flex!important;flex-flow:column nowrap!important;
 position:relative!important;justify-content:flex-start!important;
 align-items:stretch!important;align-content:initial!important;
 gap:0!important;grid-template-columns:none!important;
 grid-template-rows:none!important;min-height:94px!important;
 padding:14px 75px 13px 62px!important;box-sizing:border-box!important;
 height:auto!important;overflow:hidden!important}
#panel-inbox .v989-list-scroll .v987-message td.v987-indicator{
 position:absolute!important;top:24px!important;left:10px!important;
 width:7px!important;height:8px!important;padding:0!important}
#panel-inbox .v989-list-scroll .v987-message td.v987-identity{
 display:block!important;position:static!important;order:0!important;
 width:100%!important;min-height:21px!important;height:auto!important;
 padding:0!important;margin:0 0 3px!important;line-height:20px!important;
 overflow:hidden!important}
#panel-inbox .v989-list-scroll .v987-identity .v987-avatar{
 position:absolute!important;top:15px!important;left:24px!important;
 width:32px!important;height:32px!important;line-height:normal!important;
 display:grid!important;place-items:center!important;margin:0!important}
#panel-inbox .v989-list-scroll .v987-identity .v987-sender{
 display:block!important;position:static!important;
 height:auto!important;min-height:20px!important;
 width:100%!important;max-width:100%!important;
 overflow:hidden!important;line-height:20px!important}
#panel-inbox .v989-list-scroll .v987-identity .v987-sender strong{
 display:block!important;line-height:20px!important;height:20px!important;
 padding:0!important;margin:0!important;font-size:13px!important;
 font-weight:750!important;white-space:nowrap!important;overflow:hidden!important;
 text-overflow:ellipsis!important}
#panel-inbox .v989-list-scroll .v987-message td.v987-subject{
 position:static!important;order:1!important;display:block!important;
 width:100%!important;max-width:100%!important;min-height:20px!important;
 height:auto!important;margin:0!important;padding:0!important;
 overflow:hidden!important;line-height:19px!important}
#panel-inbox .v989-list-scroll .v987-subject>span{
 display:block!important;min-height:19px!important;height:19px!important;
 line-height:19px!important;padding:0!important;margin:0!important;
 white-space:nowrap!important;overflow:hidden!important;text-overflow:ellipsis!important}
#panel-inbox .v989-list-scroll .v987-subject>.v989-preview{
 display:block!important;min-height:19px!important;height:19px!important;
 line-height:19px!important;margin:1px 0 0!important;
 white-space:nowrap!important;overflow:hidden!important;text-overflow:ellipsis!important}
#panel-inbox .v989-list-scroll .v987-message td.v987-date{
 position:absolute!important;top:14px!important;right:15px!important;
 width:57px!important;height:20px!important;line-height:20px!important;
 text-align:right!important;white-space:nowrap!important;
 padding:0!important;margin:0!important}
@media(max-width:760px){
 #panel-inbox .v989-list-scroll .v986-mail-list tr.v987-message{
  min-height:94px!important;padding:13px 67px 13px 58px!important}
 #panel-inbox .v989-list-scroll .v987-identity .v987-avatar{
  left:20px!important;top:14px!important}
 #panel-inbox .v989-list-scroll .v987-message td.v987-date{right:10px!important}
}

"""

MAIL_SCRIPT = r"""<script id="v986-mail-composer">
(() => {
 'use strict';
 const BAD = new Set(['SCRIPT','STYLE','LINK','META','BASE','IFRAME','OBJECT','EMBED','FORM','IMG','SVG','MATH','VIDEO','AUDIO','INPUT','BUTTON']);
 const GOOD = new Set(['P','DIV','BR','B','STRONG','I','EM','U','S','UL','OL','LI','A','BLOCKQUOTE','H1','H2','H3','SPAN']);
 const states = new WeakMap();
 const escapeText = txt => String(txt).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;');
 function safeHTML(source) {
   const doc = new DOMParser().parseFromString('<div>'+String(source || '')+'</div>','text/html');
   const wrapper = doc.body.firstElementChild;
   if(!wrapper)return '';
   function visit(node){
     for(const child of Array.from(node.children)) {
       if(BAD.has(child.tagName)){child.remove();continue;}
       if(!GOOD.has(child.tagName)){
         const text=doc.createTextNode(child.textContent||'');
         child.replaceWith(text);continue;
       }
       for(const attr of Array.from(child.attributes)){
         if(child.tagName==='A' && attr.name.toLowerCase()==='href') {
           const href=attr.value.trim();
           if(/^(https?:|mailto:)/i.test(href))continue;
         }
         child.removeAttribute(attr.name);
       }
       visit(child);
     }
   }
   visit(wrapper);
   return wrapper.innerHTML;
 }
 function getBody(text) {
   const doc=new DOMParser().parseFromString(String(text||''),'text/html');
   return doc.body?.innerHTML || '';
 }
 function plain(text) {
   const doc=new DOMParser().parseFromString('<div>'+String(text||'')+'</div>','text/html');
   return (doc.body?.textContent||'').trim();
 }
 function ampTemplate(content){
   return '<!doctype html>\n<html amp4email>\n<head>\n<meta charset="utf-8">\n'
     +'<script async src="https://cdn.ampproject.org/v0.js"><\/script>\n'
     +'<style amp4email-boilerplate>body{visibility:hidden}</style>\n'
     +'<\/head>\n<body>\n'+content+'\n<\/body>\n<\/html>';
 }
 function setupEditor(editor){
   if(states.has(editor))return;
   const form=editor.closest('form[data-v960-send]');
   if(!form)return;
   const visual=editor.querySelector('[data-v986-rich]');
   const code=editor.querySelector('[data-v986-code]');
   const picker=editor.querySelector('[data-v986-format]');
   const body=form.querySelector('textarea[name="body"]');
   const html=form.querySelector('textarea[name="body_html"]');
   const amp=form.querySelector('textarea[name="body_amp"]');
   if(!visual||!code||!picker||!body||!html||!amp)return;
   const drafts={plain:body.value||'',html:html.value||'',amp:amp.value||''};
   let format='plain',mode='visual',visualDirty=false;
   const read=()=>mode==='code'?code.value:
     format==='plain'?visual.innerText:visual.innerHTML;
   function save(){
     if(mode==='code'){
       // Code is the authoritative source until the user actually edits Visual.
       drafts[format]=code.value;
     }else if(visualDirty){
       const value=read();
       drafts[format]=format==='plain'?value:
         format==='html'?safeHTML(value):ampTemplate(safeHTML(value));
     }
     const value=drafts[format];
     body.value=format==='plain'?value:
       plain(format==='amp'?getBody(value):value);
     html.value=format==='html'?value:'';
     amp.value=format==='amp'?value:'';
   }
   function show(){
     const value=drafts[format] || (format==='plain'?'':format==='html'?safeHTML(escapeText(drafts.plain).replaceAll('\n','<br>')):
       ampTemplate(safeHTML(escapeText(drafts.plain).replaceAll('\n','<br>'))));
     drafts[format]=value;
     if(mode==='visual'){
       visual.innerHTML=format==='plain'?safeHTML(escapeText(value).replaceAll('\n','<br>')):
         safeHTML(format==='amp'?getBody(value):value);
     }else{
       code.value=value;
     }
     visualDirty=false;
     visual.hidden=mode!=='visual';
     code.hidden=mode!=='code';
     const tools=editor.querySelector('.v986-rich-toolbar');
     if(tools)tools.hidden=mode!=='visual'||format==='plain';
     editor.querySelectorAll('[data-v986-editor-mode]').forEach(btn=>{
       const active=btn.dataset.v986EditorMode===mode;
       btn.classList.toggle('active',active);
       btn.setAttribute('aria-pressed',String(active));
     });
   }
   picker.addEventListener('change',()=>{
     save();format=picker.value;show();save();
   });
   editor.querySelectorAll('[data-v986-editor-mode]').forEach(btn=>btn.addEventListener('click',()=>{
     if(btn.dataset.v986EditorMode===mode)return;
     save();mode=btn.dataset.v986EditorMode;show();save();
   }));
   visual.addEventListener('paste',ev=>{
     ev.preventDefault();
     const txt=ev.clipboardData?.getData('text/plain')||'';
     document.execCommand('insertText',false,txt);
   });
   visual.addEventListener('input',()=>{visualDirty=true;save();});
   code.addEventListener('input',save);
   editor.querySelectorAll('[data-v986-rich-command]').forEach(btn=>{
     btn.addEventListener('click',()=>{
       visual.focus();
       const cmd=btn.dataset.v986RichCommand;
       if(cmd==='createLink'){
         const val=window.prompt('Link URL (https:// or mailto:)','https://');
         if(!val||!(/^(https?:|mailto:)/i.test(val)))return;
         document.execCommand(cmd,false,val);
       }else document.execCommand(cmd,false,null);
       visualDirty=true;
       save();
     });
   });
   states.set(editor,{save});
   show();
   save();
 }
 function setupAttachments(form){
   const select=form.querySelector('select.v971-file-picker[name="attachment_file_ids"]');
   if(!select || select.dataset.v986Enhanced)return;
   select.dataset.v986Enhanced='1';
   for(const option of select.options) option.selected=false; // Explicit opt-in.
   const oldLabel=select.closest('label');
   const wrap=document.createElement('div');
   wrap.className='v986-attachments';
   const details=document.createElement('details');
   const summary=document.createElement('summary');
   const choices=document.createElement('div');
   choices.className='v986-file-choices';
   const info=document.createElement('p');
   info.className='v986-attachment-info';
   info.textContent='Files in storage are never attached unless explicitly selected here.';
   const refresh=()=>{
     const n=Array.from(select.selectedOptions).length;
     summary.textContent='Attachments · '+n+' selected';
   };
   for(const option of select.options) {
     const label=document.createElement('label');
     const checkbox=document.createElement('input');
     checkbox.type='checkbox';
     checkbox.value=option.value;
     checkbox.checked=false;
     checkbox.addEventListener('change',()=>{option.selected=checkbox.checked;refresh();});
     label.append(checkbox,document.createTextNode(option.textContent||option.value));
     choices.append(label);
   }
   if(!select.options.length)choices.textContent='No stored files are available.';
   details.append(summary,choices,info);
   wrap.append(details);
   oldLabel?.after(wrap);
   if(oldLabel)oldLabel.hidden=true;
   refresh();
 }
 let lastListPosition={key:'',top:0};
 const loadingLists=new WeakSet();
 function mailboxKey(url) {
   const u=new URL(url,location.href);
   u.searchParams.delete('message_uid');
   u.searchParams.delete('message_account_id');
   u.searchParams.delete('message_mailbox');
   u.searchParams.delete('compose_mode');
   u.searchParams.delete('full_html');
   u.searchParams.delete('full_html_intent');
   u.searchParams.delete('page');
   return u.pathname+'?'+u.searchParams.toString();
 }
 function bindMessageRow(row,scroller) {
   row.tabIndex=0;
   // Newly appended rows are not seen by the original lazy fragment's bindReader.
   // Dispatch a same-origin link click through its existing client-side router.
   const link=document.createElement('a');
   link.href=row.dataset.v960Href||'#inbox';
   link.dataset.v960Fragment='inbox';
   link.hidden=true;
   link.tabIndex=-1;
   row.append(link);
   const open=()=>{
     lastListPosition={key:mailboxKey(location.href),top:scroller.scrollTop};
     link.click();
   };
   row.onclick=event=>{
     if(!event.target.closest('a,button,input,form,select,textarea'))open();
   };
   row.onkeydown=event=>{
     if((event.key==='Enter'||event.key===' ') &&
       !event.target.closest('a,button,input,form,select,textarea')) {
       event.preventDefault();open();
     }
   };
 }
 function normalizeDate(node) {
   const date=new Date(node.dataset.v987Date);
   if(Number.isNaN(date.getTime()))return;
   const now=new Date();
   node.textContent=now.toDateString()===date.toDateString()
     ?date.toLocaleTimeString(undefined,{hour:'numeric',minute:'2-digit'})
     :date.toLocaleDateString(undefined,{day:'numeric',month:'short'});
   node.title=date.toLocaleString();
 }
 function updateListCounter(workspace,number) {
   const label=workspace.querySelector('.v989-stream-count');
   if(label)label.textContent=number+' shown';
 }
 function enhanceBetaInbox(inbox){
   const workspace=inbox.querySelector('[data-v989-mail-workspace]');
   if(!workspace || workspace.dataset.v989Bound==='1')return;
   workspace.dataset.v989Bound='1';
   const scroller=workspace.querySelector('.v989-list-scroll');
   const tbody=scroller?.querySelector('tbody');
   const pane=workspace.querySelector('.v989-reading-pane');
   const source=workspace.querySelector('.v989-reader-source');
   if(!scroller || !tbody || !pane || !source)return;
   // Reader is server-rendered as a sibling, never moved from an invalid tr.
   const selectedQuery=new URL(location.href).searchParams;
   const uid=selectedQuery.get('message_uid');
   const selectedAccount=selectedQuery.get('message_account_id');
   const selectedMailbox=selectedQuery.get('message_mailbox');
   const rows=[...tbody.querySelectorAll('tr[data-v960-href]')];
   rows.forEach(row=>{
     const rowQuery=new URL(row.dataset.v960Href,location.href).searchParams;
     const selected=Boolean(uid && rowQuery.get('message_uid')===uid &&
       (!selectedAccount || rowQuery.get('message_account_id')===selectedAccount) &&
       (!selectedMailbox || rowQuery.get('message_mailbox')===selectedMailbox));
     row.classList.toggle('v989-selected',selected);
     row.setAttribute('aria-selected',String(selected));
   });
   workspace.querySelectorAll('.v987-date[data-v987-date]').forEach(normalizeDate);
   const key=mailboxKey(location.href);
   if(lastListPosition.key===key)scroller.scrollTop=lastListPosition.top;
   updateListCounter(workspace,rows.length);
   // Real infinite scrolling through successive cached pages: never request
   // message bodies, external media, or IMAP sync; only app-owned GET fragments.
   const pagination=inbox.querySelector('.v960-pagination');
   let nextLink=[...(pagination?.querySelectorAll('a[href]')||[])]
     .find(a=>a.textContent.toLowerCase().includes('next'));
   if(pagination)pagination.hidden=true;
   const status=workspace.querySelector('.v989-scroll-status');
   const moreButton=document.createElement('button');
   moreButton.type='button';moreButton.textContent='Load more';
   moreButton.className='v989-more-button';moreButton.hidden=!nextLink;
   const statusText=document.createElement('span');
   if(status){status.replaceChildren(statusText,moreButton);}
   let complete=false;
   let inflight=false;
   function setStatus(s){statusText.textContent=s;}
   moreButton.addEventListener('click',()=>more());
   async function more(){
     if(inflight||complete||!nextLink||document.hidden)return;
     if(!workspace.isConnected)return;
     inflight=true;loadingLists.add(workspace);moreButton.disabled=true;
     setStatus('Loading more messages…');
     try{
       const u=new URL(nextLink.href,location.href);
       if(u.origin!==location.origin)throw new Error('Cross-origin page');
       u.pathname='/dashboard/view/inbox';u.searchParams.set('ui_view','inbox');
       u.searchParams.delete('message_uid');
       u.searchParams.delete('message_account_id');
       u.searchParams.delete('message_mailbox');
       u.searchParams.delete('compose_mode');
       u.searchParams.delete('full_html');u.searchParams.delete('full_html_intent');
       u.hash='';
       const response=await fetch(u.toString(),{credentials:'same-origin',cache:'no-store',
         headers:{'X-Postmaster-Fragment':'1'}});
       if(!response.ok)throw new Error('Could not load more messages');
       const box=document.createElement('div');box.innerHTML=(await response.text()).trim();
       const panel=box.firstElementChild;
       if(panel?.dataset.panel!=='inbox')throw new Error('Unexpected mailbox response');
       if(!workspace.isConnected || mailboxKey(location.href)!==key)return;
       const extra=panel.querySelector('.v989-list-scroll tbody');
       const extraRows=[...(extra?.querySelectorAll('tr[data-v960-href]')||[])];
       const composite=row=>{
         const p=new URL(row.dataset.v960Href,location.href).searchParams;
         return [p.get('message_account_id')||p.get('account_id')||'',
           p.get('message_mailbox')||p.get('mailbox')||'',p.get('message_uid')||''].join('\u001f');
       };
       const known=new Set([...tbody.querySelectorAll('tr[data-v960-href]')].map(composite));
       let added=0;
       for(const item of extraRows){
         const identity=composite(item);
         if(!identity.split('\u001f')[2]||known.has(identity))continue;
         known.add(identity);
         const row=document.importNode(item,true);
         bindMessageRow(row,scroller);
         row.querySelectorAll('.v987-date[data-v987-date]').forEach(normalizeDate);
         tbody.append(row);added++;
       }
       const morePagination=panel.querySelector('.v960-pagination');
       const found=[...(morePagination?.querySelectorAll('a[href]')||[])]
         .find(a=>a.textContent.toLowerCase().includes('next'));
       nextLink=found||null;
       complete=!nextLink||!extraRows.length;
       moreButton.hidden=complete;
       updateListCounter(workspace,tbody.querySelectorAll('tr[data-v960-href]').length);
       setStatus(complete?'All cached messages loaded':'Scroll for more, or use Load more');
     }catch(err){
       setStatus('Unable to load more messages. Scroll to retry.');
     }finally{
       inflight=false;loadingLists.delete(workspace);moreButton.disabled=false;
     }
   }
   scroller.addEventListener('scroll',()=>{
     lastListPosition={key,top:scroller.scrollTop};
     if(scroller.scrollHeight-scroller.scrollTop-scroller.clientHeight<230)more();
   },{passive:true});
   if(!nextLink)setStatus('All cached messages loaded');
   else {
     setStatus('Scroll for more, or use Load more');
     if(scroller.scrollHeight<=scroller.clientHeight+250)more();
   }
 }
 function init(){
   const inbox=document.querySelector('#panel-inbox.active');
   if(!inbox)return;
   const composer=inbox.querySelector('details.v971-floating-compose');
   if(composer){
     const heading=inbox.querySelector('.v986-inbox-head');
     if(heading && composer.previousElementSibling!==heading)heading.after(composer);
     composer.querySelectorAll('[data-v986-editor]').forEach(setupEditor);
     const form=composer.querySelector('form[data-v960-send]');
     if(form)setupAttachments(form);
   }
   enhanceBetaInbox(inbox);
 }
 document.addEventListener('submit',ev=>{
   const sync=ev.target.closest?.('form[action="/dashboard/inbox/refresh"]');
   if(sync){
     const button=sync.querySelector('button[type=submit]');
     if(button?.disabled){ev.preventDefault();return;}
     if(button){button.disabled=true;button.textContent='↻ Syncing…';}
   }
   const editor=ev.target.querySelector?.('[data-v986-editor]');
   if(editor)states.get(editor)?.save();
 },true);
 const main=document.querySelector('.v970-workspace > main');
 if(main)new MutationObserver(init).observe(main,{childList:true,subtree:false});
 document.addEventListener('click',ev=>{
   if(ev.target.closest('[data-v985-compose]'))queueMicrotask(init);
 });
 init();
})();
</script>"""


def install_mail_gui_v986(v960: Any, shell: Any) -> None:
    if getattr(v960, "_v986_mail_installed", False):
        return
    original_compose = v960._compose_panel
    original_inbox = v960.render_inbox
    def compose(*args: Any, **kwargs: Any) -> str:
        return modern_compose_markup(original_compose(*args,**kwargs))
    def inbox(base: Any, request: Request) -> str:
        selected = str(request.query_params.get("account_id") or request.query_params.get("account") or "").strip()
        return modern_inbox_markup(original_inbox(base,request),selected_account=selected,query_string=request.url.query)
    v960._compose_panel = compose
    v960.render_inbox = inbox
    setattr(v960,"_v986_mail_installed",True)
    prev=shell._styles
    if not getattr(prev,"_v986_mail_style",False):
        def styles() -> str:
            return prev().rstrip()+"\n"+MAIL_STYLE
        styles._v986_mail_style=True
        shell._styles=styles
    if 'id="v986-mail-composer"' not in shell.SCRIPT:
        shell.SCRIPT+=MAIL_SCRIPT
    if 'v989-mail-workspace' not in shell._styles():
        previous_styles = shell._styles
        def beta_styles() -> str:
            return previous_styles() + "\n" + BETA_MAIL_STYLE
        shell._styles = beta_styles
