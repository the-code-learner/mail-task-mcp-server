from __future__ import annotations

import json
import secrets
import time
from html import escape
from typing import Any
from urllib.parse import urlencode

from starlette.requests import Request
from starlette.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from starlette.routing import Mount, Route

from . import webgui_v945 as v945
from . import webgui_v951 as v951
from . import webgui_v952 as v952
from . import webgui_v953 as v953
from . import webgui_v954 as v954
from .knowledge_scopes import KnowledgeScopeStore
from .runtime_v960_knowledge import knowledge_scope_store
from .webgui_helpers import owner_options, project_options, project_rows, render_markdown_safe


STYLE = r'''
/* webgui-v960-mail-client */
.v960-mailbox-tabs{display:flex;gap:6px;flex-wrap:wrap;margin:8px 0 14px}.v960-mailbox-tabs a{border:1px solid var(--line);border-radius:999px;padding:6px 10px;text-decoration:none;color:var(--muted);font-size:11px;font-weight:750}.v960-mailbox-tabs a.active{border-color:var(--accent);color:var(--accent);background:rgba(104,160,255,.10)}
.v960-mail-table tbody tr.v960-message-row{cursor:pointer}.v960-mail-table tbody tr.v960-message-row:hover{background:rgba(127,127,127,.07)}.v960-mail-table tbody tr.v960-unread td{font-weight:800}.v960-mail-table tbody tr.v960-selected{background:rgba(104,160,255,.09)}.v960-mail-table .v960-state{width:20px;text-align:center}.v960-unread-dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--accent)}
.v960-inline-detail td{padding:0!important}.v960-detail-shell{border-top:1px solid var(--line);border-bottom:1px solid var(--line);padding:14px;background:rgba(127,127,127,.035)}.v960-detail-head{display:flex;justify-content:space-between;align-items:flex-start;gap:12px;flex-wrap:wrap}.v960-detail-tabs{display:flex;gap:5px;flex-wrap:wrap;margin:12px 0}.v960-detail-tabs button{padding:6px 9px}.v960-detail-tabs button.active{border-color:var(--accent);color:var(--accent)}.v960-detail-pane{display:none}.v960-detail-pane.active{display:block}.v960-reader{border:1px solid var(--line);border-radius:10px;padding:14px;overflow:auto;background:var(--card);line-height:1.55}.v960-reader img,.v960-reader iframe,.v960-reader style,.v960-reader script{display:none!important}.v960-links td{vertical-align:top}.v960-links code{white-space:normal;overflow-wrap:anywhere}
.v960-compose{margin:15px 0}.v960-compose summary{cursor:pointer;font-weight:800;color:var(--accent)}.v960-compose .v960-advanced{margin-top:10px}.v960-actions{display:flex;gap:8px;flex-wrap:wrap;align-items:center}.v960-form-note{font-size:11px;color:var(--muted)}
.v960-perf{display:flex;gap:6px;flex-wrap:wrap;margin:8px 0}.v960-perf span{border:1px solid var(--line);border-radius:999px;padding:3px 7px;font-size:10px;color:var(--muted)}
.v960-scope-chips{display:flex;gap:7px;flex-wrap:wrap;margin:10px 0 14px}.v960-scope-chip{border:1px solid var(--line);border-radius:999px;padding:5px 9px;text-decoration:none;color:var(--muted);font-size:11px;font-weight:750}.v960-scope-chip.active{border-color:var(--accent);color:var(--accent);background:rgba(104,160,255,.10)}.v960-scope-list{display:flex;gap:4px;flex-wrap:wrap}.v960-scope-list span{border:1px solid var(--line);border-radius:999px;padding:2px 6px;font-size:10px;color:var(--muted)}
.v960-health-columns{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:14px}.v960-health-columns>section{border:1px solid var(--line);border-radius:12px;padding:14px}.v960-health-columns h3{margin-top:0}.v960-pagination{display:flex;justify-content:space-between;align-items:center;gap:8px;margin:10px 0}.v960-pagination a{border:1px solid var(--line);border-radius:8px;padding:6px 9px;text-decoration:none}
@media(max-width:820px){.v960-mail-table th:first-child,.v960-mail-table td:first-child{display:none}.v960-detail-head{display:block}.v960-health-columns{grid-template-columns:1fr}}
'''

SCRIPT = r'''
<script id="v960-progressive-enhancement">
(() => {
  function fragmentEndpoint(view, url) {
    const u = new URL(url, window.location.href);
    u.pathname = view === 'knowledge' ? '/dashboard/knowledge/fragment' : '/dashboard/inbox/fragment';
    u.hash = '';
    return u.toString();
  }
  async function replaceFragment(view, url, push=true) {
    const target = document.querySelector('#panel-' + view);
    if (!target) { window.location.href = url; return; }
    try {
      const res = await fetch(fragmentEndpoint(view, url), {headers:{'X-Postmaster-Fragment':'1'}});
      if (!res.ok) throw new Error('fragment');
      const html = await res.text();
      const box = document.createElement('div'); box.innerHTML = html.trim();
      const next = box.firstElementChild;
      if (!next) throw new Error('fragment');
      target.replaceWith(next);
      if (push) history.pushState({v960:view}, '', url);
      activateRows(); activateForms(); activateTabs();
    } catch (_) { window.location.href = url; }
  }
  function activateRows() {
    document.querySelectorAll('#panel-inbox tr[data-v960-href]').forEach(row => {
      row.tabIndex = 0;
      const go = () => replaceFragment('inbox', row.dataset.v960Href);
      row.onclick = ev => { if (!ev.target.closest('a,button,input,form,select,textarea')) go(); };
      row.onkeydown = ev => { if ((ev.key==='Enter'||ev.key===' ') && !ev.target.closest('a,button,input,form,select,textarea')) {ev.preventDefault();go();} };
    });
  }
  function activateForms() {
    document.querySelectorAll('form[data-v960-fragment]').forEach(form => {
      form.onsubmit = ev => {
        if ((form.method || 'get').toLowerCase() !== 'get') return;
        ev.preventDefault(); const data = new FormData(form); const u = new URL(form.action || '/', window.location.origin);
        for (const [k,v] of data.entries()) if (String(v).length) u.searchParams.set(k, String(v));
        const view = form.dataset.v960Fragment; u.hash = view; replaceFragment(view, u.toString());
      };
    });
    document.querySelectorAll('a[data-v960-fragment]').forEach(a => { a.onclick = ev => { ev.preventDefault(); replaceFragment(a.dataset.v960Fragment, a.href); }; });
    document.querySelectorAll('form[data-v960-send]').forEach(form => {
      form.addEventListener('submit', () => { form.querySelectorAll('button[type="submit"]').forEach(b => { b.disabled = true; b.dataset.v960Submitting='1'; }); });
    });
  }
  function activateTabs() {
    document.querySelectorAll('.v960-detail-shell').forEach(shell => {
      shell.querySelectorAll('[data-v960-detail-tab]').forEach(button => {
        button.onclick = () => {
          const tab = button.dataset.v960DetailTab;
          shell.querySelectorAll('[data-v960-detail-tab]').forEach(x => x.classList.toggle('active', x===button));
          shell.querySelectorAll('[data-v960-detail-pane]').forEach(x => x.classList.toggle('active', x.dataset.v960DetailPane===tab));
        };
      });
    });
  }
  window.addEventListener('popstate', () => { const hash=(location.hash||'').slice(1); if (hash==='inbox'||hash==='knowledge') replaceFragment(hash, location.href, false); });
  activateRows(); activateForms(); activateTabs();
})();
</script>
'''


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000.0, 2)


def _perf_html(timings: dict[str, Any]) -> str:
    if not timings:
        return ""
    labels = {
        "imap_list": "IMAP LIST",
        "imap_search": "IMAP SEARCH",
        "imap_fetch": "IMAP FETCH",
        "imap_flags": "IMAP FLAGS",
        "inspection": "Inspection",
        "tracking_enrichment": "Tracking enrichment",
        "knowledge_query": "Knowledge query",
        "mail_health": "Mail Health",
    }
    chips = []
    for key, value in timings.items():
        if value is None:
            continue
        try:
            text = f"{float(value):.2f} ms"
        except Exception:
            text = str(value)
        chips.append(f'<span>{escape(labels.get(key, key))}: {escape(text)}</span>')
    return '<div class="v960-perf">' + "".join(chips) + '</div>' if chips else ""


def _split_addresses(value: Any) -> list[str]:
    return [part.strip() for part in str(value or "").replace(";", ",").split(",") if part.strip()]


def _mailbox_catalog(base: Any, account_id: str | None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not account_id:
        return [{"name": "INBOX", "role": "received", "flags": []}], {}
    result = v951._safe_call(
        base,
        base.list_mailboxes,
        account_id=account_id,
        include_roles=True,
        include_timings=True,
    )
    if isinstance(result, dict) and isinstance(result.get("mailbox_roles"), list):
        rows = [row for row in result["mailbox_roles"] if isinstance(row, dict)]
        return rows, dict(result.get("timings_ms") or {})
    names, _ = v953._mailboxes(base, account_id)
    return [{"name": name, "role": "received" if name.casefold() == "inbox" else "other", "flags": []} for name in names], {}


def _mailbox_tabs(params: dict[str, str], catalog: list[dict[str, Any]], selected: str) -> str:
    preferred = [row for row in catalog if str(row.get("role") or "") in {"received", "sent", "spam", "drafts", "trash"}]
    rows = preferred or catalog
    links = []
    labels = {"received": "Inbox", "sent": "Sent", "spam": "Spam", "drafts": "Drafts", "trash": "Trash", "other": "Mailbox"}
    for row in rows:
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        values = dict(params); values["mailbox"] = name; values.pop("message_uid", None); values["page"] = "1"
        href = "/?" + urlencode(values) + "#inbox"
        role = str(row.get("role") or "other")
        label = labels.get(role, role.title())
        if role == "other": label = name
        links.append(f'<a data-v960-fragment="inbox" class="{"active" if name == selected else ""}" href="{escape(href, quote=True)}">{escape(label)}</a>')
    return '<nav class="v960-mailbox-tabs" aria-label="Mailboxes">' + "".join(links) + '</nav>'


def _mailbox_role(catalog: list[dict[str, Any]], mailbox: str) -> str:
    for row in catalog:
        if str(row.get("name") or "") == mailbox:
            return str(row.get("role") or "other")
    return "other"


def _compose_panel(base: Any, accounts: list[dict[str, Any]], account_id: str | None, *, mailbox: str = "", uid: str = "", role: str = "") -> str:
    csrf = escape(str(base._csrf_value()), quote=True)
    idem = escape("webgui-" + secrets.token_urlsafe(18), quote=True)
    account_select = v953._account_select(accounts, account_id)
    thread_options = '<option value="send">New message</option>'
    if uid:
        if role == "sent":
            thread_options += '<option value="follow_up">Follow up selected Sent message</option>'
        else:
            thread_options += '<option value="reply">Reply to selected message</option>'
    return f'''
<details class="card v960-compose" id="v960-compose"><summary>Compose inside Inbox</summary>
<form method="post" action="/dashboard/compose/send" data-v960-send="1">
<input type="hidden" name="csrf" value="{csrf}"><input type="hidden" name="idempotency_key" value="{idem}"><input type="hidden" name="return_view" value="inbox">
<input type="hidden" name="thread_mailbox" value="{escape(mailbox, quote=True)}"><input type="hidden" name="thread_uid" value="{escape(uid, quote=True)}">
<div class="v951-formgrid"><label>Sender account {account_select}</label><label>Action <select name="thread_mode">{thread_options}</select></label>
<label>To <input name="to" placeholder="recipient@example.com"></label><label>Cc <input name="cc" placeholder="cc@example.com"></label><label>Bcc <input name="bcc" placeholder="bcc@example.com"></label>
<label class="wide">Subject <input name="subject" placeholder="Required for new messages"></label>
<label class="wide">Plain text <textarea name="body" rows="8"></textarea></label>
<label class="wide">HTML (optional) <textarea name="body_html" rows="6" placeholder="&lt;p&gt;HTML alternative&lt;/p&gt;"></textarea></label>
<label class="wide">AMP (optional, account capability required) <textarea name="body_amp" rows="5" placeholder="AMP-for-Email document"></textarea></label>
<label class="wide">Stored File attachment IDs <input name="attachment_file_ids" placeholder="file_id_1, file_id_2"></label></div>
<div class="v951-checks"><label><input type="checkbox" name="track_opens" value="1"> Tracking</label><label><input type="checkbox" name="newsletter_mode" value="1"> Newsletter</label><label><input type="checkbox" name="automatic_unsubscribe" value="1" checked> Automatic unsubscribe</label><label><input type="checkbox" name="one_click_unsubscribe" value="1" checked> One-Click</label></div>
<details class="v960-advanced"><summary>Advanced send controls</summary><div class="v951-formgrid"><label>Explicit unsubscribe URL <input name="unsubscribe_url"></label><label>Explicit unsubscribe email <input name="unsubscribe_email" type="email"></label><label>Campaign ID <input name="campaign_id"></label><label><input type="checkbox" name="dsn_notify_success" value="1"> DSN success opt-in</label><label><input type="checkbox" name="force_send" value="1"> Force heuristic duplicate guard override</label></div></details>
<p class="v960-form-note">The hidden idempotency key is stable for this rendered form. Double-submit cannot create a second SMTP send. Force does not override an explicit idempotency conflict.</p>
<div class="v960-actions"><button class="primary" type="submit" name="compose_action" value="send">Send</button><button type="submit" name="compose_action" value="draft">Save draft</button></div>
</form></details>'''


def _links_table(privacy: dict[str, Any]) -> str:
    rows = []
    for link in privacy.get("links", []) if isinstance(privacy, dict) else []:
        flags = []
        if link.get("has_tracking_parameters"): flags.append("tracking params")
        if link.get("redirector"): flags.append("redirector")
        if link.get("tracker_hint"): flags.append("tracker hint")
        if link.get("anchor_href_mismatch"): flags.append("text/href mismatch")
        rows.append(
            '<tr>'
            f'<td>{escape(str(link.get("visible_text") or "—"))}</td>'
            f'<td><code>{escape(str(link.get("original_url") or ""))}</code></td>'
            f'<td><code>{escape(str(link.get("canonical_destination") or ""))}</code></td>'
            f'<td>{escape(", ".join(flags) or "—")}</td></tr>'
        )
    return '<div class="scroll v960-links"><table><thead><tr><th>Anchor</th><th>Original</th><th>Canonical destination</th><th>Signals</th></tr></thead><tbody>' + ("".join(rows) or '<tr><td colspan="4" class="muted">No links found.</td></tr>') + '</tbody></table></div>'


def _detail_html(base: Any, detail: dict[str, Any], *, role: str, tracking_model: dict[str, Any] | None, close_href: str) -> str:
    privacy = detail.get("privacy_inspection") if isinstance(detail.get("privacy_inspection"), dict) else {}
    html_body = str(detail.get("body_html") or "")
    plain = str(detail.get("body") or detail.get("body_text") or "")
    reader = f'<div class="v960-reader">{html_body}</div>' if html_body else f'<pre class="v951-message">{escape(plain[:50000])}</pre>'
    privacy_view = v951._details(privacy)
    headers = detail.get("headers") or []
    header_rows = ''.join(f'<tr><td><code>{escape(str(row.get("name") or ""))}</code></td><td>{escape(str(row.get("value") or ""))}</td></tr>' for row in headers if isinstance(row, dict))
    headers_view = '<div class="scroll"><table><tbody>' + (header_rows or '<tr><td class="muted">No raw headers returned.</td></tr>') + '</tbody></table></div>'
    mime_view = v951._details(detail.get("mime") or {})
    panes = [
        ("reader", "Reader", reader),
        ("privacy", "Privacy", privacy_view),
        ("links", "Links", _links_table(privacy)),
        ("headers", "Headers", headers_view),
        ("mime", "MIME", mime_view),
    ]
    if role == "sent":
        panes.append(("tracking", "Tracking", v954._tracking_detail(tracking_model)))
    buttons = ''.join(f'<button type="button" data-v960-detail-tab="{key}" class="{"active" if index == 0 else ""}">{escape(label)}</button>' for index, (key, label, _) in enumerate(panes))
    content = ''.join(f'<div data-v960-detail-pane="{key}" class="v960-detail-pane {"active" if index == 0 else ""}">{html}</div>' for index, (key, _, html) in enumerate(panes))
    timing = _perf_html(detail.get("performance_ms") or {})
    counterpart = str(detail.get("to") if role == "sent" else detail.get("from") or "")
    return f'''<div class="v960-detail-shell"><div class="v960-detail-head"><div><h3>{escape(str(detail.get("subject") or "Message"))}</h3><p class="small muted">{escape("To" if role == "sent" else "From")}: {escape(counterpart)} · {escape(str(detail.get("date") or ""))}</p></div><a data-v960-fragment="inbox" href="{escape(close_href, quote=True)}">Close</a></div>{timing}<div class="v960-detail-tabs">{buttons}</div>{content}</div>'''


def render_inbox(base: Any, request: Request) -> str:
    params = v952._canonical_inbox_params(request)
    accounts, account_id = v953._selected_account_id(base, request)
    if account_id: params["account_id"] = account_id
    catalog, list_timings = _mailbox_catalog(base, account_id)
    mailbox = params.get("mailbox") or "INBOX"
    available = [str(row.get("name") or "") for row in catalog]
    if available and mailbox not in available:
        received = next((str(row.get("name") or "") for row in catalog if row.get("role") == "received"), available[0])
        mailbox = received; params["mailbox"] = mailbox; params.pop("message_uid", None)
    role = _mailbox_role(catalog, mailbox)
    uid = params.get("message_uid", "")
    page = max(1, v951._bounded_int(request.query_params.get("page"), 1, 1, 1000))
    page_size = 25
    search_result: Any = []
    timings = dict(list_timings)
    if account_id:
        search_result = v951._safe_call(
            base, base.search_emails,
            mailbox=mailbox,
            subject=params.get("subject") or None,
            text=params.get("text") or None,
            unread_only=params.get("unread_only") == "1",
            since_days=int(params["since_days"]),
            limit=100,
            account_id=account_id,
            include_timings=True,
        )
    results = v951._list_result(search_result, "emails", "results", "messages")
    if isinstance(search_result, dict): timings.update(search_result.get("timings_ms") or {})
    start = (page - 1) * page_size
    page_rows = results[start:start + page_size]
    if page > 1 and not page_rows and results:
        page = 1; start = 0; page_rows = results[:page_size]

    detail: Any = None
    if uid and account_id:
        detail = v951._safe_call(
            base, base.get_email,
            mailbox=mailbox,
            uid=uid,
            account_id=account_id,
            inspection="full",
            content_mode="safe",
        )
    tracking_models: dict[str, dict[str, Any]] = {}
    if role == "sent" and account_id:
        wanted = [v954._message_id(row) for row in page_rows]
        if isinstance(detail, dict): wanted.append(v954._message_id(detail))
        started = time.perf_counter()
        tracking_models = v954._build_tracking_read_model(
            base, v954._CORE, account_id=account_id,
            message_ids=wanted,
            detail_message_id=v954._message_id(detail) if isinstance(detail, dict) else "",
        )
        timings["tracking_enrichment"] = _elapsed_ms(started)

    close_values = dict(params); close_values.pop("message_uid", None); close_values["page"] = str(page)
    close_href = "/?" + urlencode(close_values) + "#inbox"
    rows = []
    for row in page_rows:
        row_uid = str(row.get("uid") or row.get("id") or "").strip()
        if not row_uid: continue
        values = dict(close_values); values["message_uid"] = row_uid
        href = "/?" + urlencode(values) + "#inbox"
        seen = row.get("seen")
        css = "v960-message-row" + (" v960-unread" if seen is False else "") + (" v960-selected" if uid == row_uid else "")
        counterpart = str(row.get("to") if role == "sent" else row.get("from") or "")
        state = '<span class="v960-unread-dot" title="Unread"></span>' if seen is False else ""
        tracking = ""
        if role == "sent":
            model = tracking_models.get(v954._message_id(row))
            label, track_css = v954._tracking_label(model)
            tracking = f'<td><span class="v954-track {track_css}">{escape(label)}</span></td>'
        rows.append(
            f'<tr class="{css}" data-v960-href="{escape(href, quote=True)}"><td class="v960-state">{state}</td>'
            f'<td>{escape(counterpart)}</td><td>{escape(str(row.get("subject") or ""))}</td><td>{escape(str(row.get("date") or ""))}</td>{tracking}</tr>'
        )
        if uid == row_uid and isinstance(detail, dict):
            model = tracking_models.get(v954._message_id(detail)) if role == "sent" else None
            colspan = 5 if role == "sent" else 4
            rows.append(f'<tr class="v960-inline-detail"><td colspan="{colspan}">{_detail_html(base, detail, role=role, tracking_model=model, close_href=close_href)}</td></tr>')
    counterpart_label = "To" if role == "sent" else "From"
    tracking_head = "<th>Tracking</th>" if role == "sent" else ""
    empty_colspan = 5 if role == "sent" else 4
    table = '<div class="scroll"><table class="v960-mail-table"><thead><tr><th></th><th>' + counterpart_label + '</th><th>Subject</th><th>Date</th>' + tracking_head + '</tr></thead><tbody>' + (''.join(rows) or f'<tr><td colspan="{empty_colspan}" class="muted">No messages matched.</td></tr>') + '</tbody></table></div>'
    previous = ""; next_link = ""
    if page > 1:
        values = dict(close_values); values["page"] = str(page - 1); previous = f'<a data-v960-fragment="inbox" href="/?{urlencode(values)}#inbox">← Previous</a>'
    if start + page_size < len(results):
        values = dict(close_values); values["page"] = str(page + 1); next_link = f'<a data-v960-fragment="inbox" href="/?{urlencode(values)}#inbox">Next →</a>'
    pagination = f'<div class="v960-pagination"><span>{previous}</span><span class="small muted">Page {page} · {len(results)} loaded</span><span>{next_link}</span></div>'
    checked = " checked" if params.get("unread_only") == "1" else ""
    return f'''
<section class="tab-panel" id="panel-inbox" data-panel="inbox"><div class="v951-pagehead"><div><h2>Inbox</h2><p>Safe mail reader · logical mailbox roles · inline privacy inspection.</p></div></div>
{_mailbox_tabs(params, catalog, mailbox)}
<form method="get" action="/" class="v951-toolbar" data-v960-fragment="inbox"><input type="hidden" name="ui_view" value="inbox"><input type="hidden" name="inbox_search" value="1"><input type="hidden" name="page" value="1">
<label>Account {v953._account_select(accounts, account_id)}</label><label>Mailbox {v953._mailbox_select(available or [mailbox], mailbox)}</label><label>Subject <input name="subject" value="{escape(params.get('subject',''), quote=True)}"></label><label>Text <input name="text" value="{escape(params.get('text',''), quote=True)}"></label><label>Since days <input type="number" min="1" max="3650" name="since_days" value="{escape(params['since_days'], quote=True)}"></label><label><input type="checkbox" name="unread_only" value="1"{checked}> unread only</label><button type="submit">Filter</button></form>
{_perf_html(timings)}{table}{pagination}{_compose_panel(base, accounts, account_id, mailbox=mailbox, uid=uid, role=role)}
</section>'''


def render_compose(base: Any, request: Request) -> str:
    accounts, account_id = v953._selected_account_id(base, request)
    flash = escape(request.query_params.get("compose_result") or "")
    return f'''<section class="tab-panel" id="panel-compose" data-panel="compose"><div class="v951-pagehead"><div><h2>Compose</h2><p>The same composer is embedded in Inbox; this compatibility view remains available.</p></div></div>{f'<div class="flash">{flash}</div>' if flash else ''}{_compose_panel(base, accounts, account_id)}</section>'''


async def compose_send(base: Any, request: Request):
    form, error = await base._verified_form(request)
    if error: return error
    mode = str(form.get("thread_mode") or "send").strip()
    action = str(form.get("compose_action") or "send").strip()
    to = _split_addresses(form.get("to")); cc = _split_addresses(form.get("cc")); bcc = _split_addresses(form.get("bcc"))
    attachments = [{"file_id": value} for value in _split_addresses(form.get("attachment_file_ids"))]
    common = {
        "body": str(form.get("body") or ""),
        "body_html": str(form.get("body_html") or "").strip() or None,
        "attachments": attachments or None,
        "account_id": str(form.get("account_id") or "").strip() or None,
    }
    mailbox = str(form.get("thread_mailbox") or "").strip(); uid = str(form.get("thread_uid") or "").strip()
    if action == "draft":
        if mode == "reply" and mailbox and uid:
            result = v951._safe_call(base, base.create_reply_draft, mailbox=mailbox, uid=uid, cc=cc or None, bcc=bcc or None, **common)
        elif mode == "follow_up" and mailbox and uid:
            result = v951._safe_call(base, base.create_follow_up_draft, mailbox=mailbox, uid=uid, cc=cc or None, bcc=bcc or None, **common)
        else:
            result = v951._safe_call(base, base.create_draft, to=to, cc=cc or None, bcc=bcc or None, subject=str(form.get("subject") or ""), body_amp=str(form.get("body_amp") or "").strip() or None, **common)
        success_text = "Draft saved"
    elif mode in {"reply", "follow_up"} and mailbox and uid:
        fn = base.reply_email if mode == "reply" else base.follow_up_email
        result = v951._safe_call(
            base, fn, mailbox=mailbox, uid=uid, cc=cc or None, bcc=bcc or None,
            track_opens=True if form.get("track_opens") else None,
            campaign_id=str(form.get("campaign_id") or "").strip() or None,
            idempotency_key=str(form.get("idempotency_key") or "").strip() or None,
            force_send=bool(form.get("force_send")), **common,
        )
        success_text = "Message sent"
    else:
        if not to:
            return RedirectResponse("/?ui_view=inbox&compose_result=Recipient+required#inbox", status_code=303)
        result = v951._safe_call(
            base, base.send_email, to=to, cc=cc or None, bcc=bcc or None,
            subject=str(form.get("subject") or ""), body_amp=str(form.get("body_amp") or "").strip() or None,
            track_opens=True if form.get("track_opens") else None,
            campaign_id=str(form.get("campaign_id") or "").strip() or None,
            newsletter_mode=bool(form.get("newsletter_mode")),
            unsubscribe_url=str(form.get("unsubscribe_url") or "").strip() or None,
            unsubscribe_email=str(form.get("unsubscribe_email") or "").strip() or None,
            one_click_unsubscribe=bool(form.get("one_click_unsubscribe")),
            automatic_unsubscribe=bool(form.get("automatic_unsubscribe")),
            dsn_notify_success=bool(form.get("dsn_notify_success")),
            idempotency_key=str(form.get("idempotency_key") or "").strip() or None,
            force_send=bool(form.get("force_send")), **common,
        )
        success_text = "Message sent"
    ok = isinstance(result, dict) and result.get("ok") is not False
    text = success_text if ok else "Operation failed: " + str(result.get("error") if isinstance(result, dict) else result)[:180]
    view = "inbox" if str(form.get("return_view") or "") == "inbox" else "compose"
    return RedirectResponse("/?" + urlencode({"ui_view": view, "compose_result": text}) + f"#{view}", status_code=303)


def render_mail_health(base: Any, request: Request) -> str:
    accounts, account_id = v953._selected_account_id(base, request)
    run = request.query_params.get("health_snapshot") == "1"
    result: Any = None; elapsed = None
    selector = (request.query_params.get("dkim_selector") or "").strip()
    if run:
        started = time.perf_counter()
        result = v951._safe_call(base, base.test_email_account, account_id=account_id, refresh=False, dkim_selector=selector or None)
        elapsed = _elapsed_ms(started)
    connectivity = {}; domain = {}
    if isinstance(result, dict):
        for key in ("imap", "smtp", "tls", "connection", "certificate"):
            if key in result: connectivity[key] = result[key]
        for key in ("dns", "mx", "spf", "dkim", "dmarc", "mta_sts", "tls_rpt", "dane", "tlsa", "bimi"):
            if key in result: domain[key] = result[key]
        if isinstance(result.get("dns"), dict): domain.update({f"dns.{k}": v for k, v in result["dns"].items()})
    csrf = escape(str(base._csrf_value()), quote=True)
    return f'''<section class="tab-panel" id="panel-mail-health" data-panel="mail-health"><div class="v951-pagehead"><div><h2>Mail Health — DNS &amp; Deliverability</h2><p>Transport/connectivity diagnostics are separate from domain authentication and transport policy. No single good/bad score is synthesized.</p></div></div>
<form method="get" action="/" class="v951-toolbar"><input type="hidden" name="ui_view" value="mail-health"><input type="hidden" name="health_snapshot" value="1"><label>Account {v953._account_select(accounts, account_id)}</label><label>DKIM selector <input name="dkim_selector" value="{escape(selector, quote=True)}"></label><button type="submit">Read current snapshot</button></form>{_perf_html({'mail_health':elapsed} if elapsed is not None else {})}
<div class="v960-health-columns"><section><h3>Account connectivity / TLS</h3>{v951._status_rows(connectivity) if connectivity else '<p class="muted">Run a snapshot to inspect IMAP/SMTP/TLS.</p>'}</section><section><h3>Domain authentication / transport policy</h3>{v951._details(domain) if domain else '<p class="muted">Run a snapshot to inspect MX, SPF, DKIM, DMARC, MTA-STS, TLS-RPT, DANE/TLSA and BIMI.</p>'}</section></div>{v951._details(result) if result is not None else ''}
<details class="v951-details"><summary>Force-refresh diagnostics</summary><form method="post" action="/dashboard/mail-health/refresh"><input type="hidden" name="csrf" value="{csrf}"><input type="hidden" name="account_id" value="{escape(account_id or '', quote=True)}"><input type="hidden" name="dkim_selector" value="{escape(selector, quote=True)}"><button type="submit">Refresh diagnostics</button></form></details></section>'''


def _scope_selection(request: Request) -> tuple[list[str], bool]:
    raw = (request.query_params.get("projects") or "").strip()
    projects = [value for value in (part.strip() for part in raw.split(",")) if value]
    return list(dict.fromkeys(projects)), request.query_params.get("scope_global") == "1"


def _scope_url(request: Request, *, selected: list[str], global_selected: bool) -> str:
    params = {k: v for k, v in request.query_params.items() if k not in {"projects", "scope_global", "view_knowledge", "edit_knowledge"}}
    params["ui_view"] = "knowledge"
    if selected: params["projects"] = ",".join(selected)
    if global_selected: params["scope_global"] = "1"
    return "/?" + urlencode(params) + "#knowledge"


def _knowledge_items(base: Any, scopes: KnowledgeScopeStore, selected: list[str], global_selected: bool) -> list[dict[str, Any]]:
    rows = base.context_engine().store.list_items(limit=1000)
    if not selected and not global_selected:
        return scopes.attach_many(rows)
    project_ids = list(selected)
    include_global = global_selected
    if global_selected and not selected:
        project_ids = [""]; include_global = False
    allowed = scopes.item_ids_for(project_ids=project_ids, include_global=include_global)
    return scopes.attach_many(row for row in rows if str(row.get("id") or "") in allowed)


def _scope_chips(request: Request, projects: list[dict[str, Any]], selected: list[str], global_selected: bool) -> str:
    all_active = not selected and not global_selected
    chips = [f'<a data-v960-fragment="knowledge" class="v960-scope-chip {"active" if all_active else ""}" href="{escape(_scope_url(request, selected=[], global_selected=False), quote=True)}">Tutti</a>']
    chips.append(f'<a data-v960-fragment="knowledge" class="v960-scope-chip {"active" if global_selected else ""}" href="{escape(_scope_url(request, selected=selected, global_selected=not global_selected), quote=True)}">Global</a>')
    for row in projects:
        pid = str(row.get("id") or "").strip()
        if not pid: continue
        next_selected = [value for value in selected if value != pid] if pid in selected else selected + [pid]
        label = str(row.get("name") or pid)
        owner = str(row.get("owner_id") or "")
        chips.append(f'<a data-v960-fragment="knowledge" class="v960-scope-chip {"active" if pid in selected else ""}" href="{escape(_scope_url(request, selected=next_selected, global_selected=global_selected), quote=True)}">{escape(owner + " / " + label)}</a>')
    return '<nav class="v960-scope-chips" aria-label="Knowledge project filters">' + ''.join(chips) + '</nav>'


def _scope_labels(item: dict[str, Any], names: dict[str, str]) -> str:
    scopes = item.get("scopes") or []
    labels = []
    for scope in scopes:
        if not isinstance(scope, dict): continue
        pid = str(scope.get("project_id") or "")
        label = names.get(pid, pid) if pid else "Global"
        if scope.get("is_primary"): label += " · primary"
        labels.append(f'<span>{escape(str(scope.get("owner_id") or ""))} / {escape(label)}</span>')
    return '<div class="v960-scope-list">' + ''.join(labels) + '</div>'


def knowledge_fragment(base: Any, request: Request) -> str:
    scopes = knowledge_scope_store(); selected, global_selected = _scope_selection(request); projects = project_rows(base)
    names = {str(row.get("id") or ""): str(row.get("name") or row.get("id") or "") for row in projects}
    started = time.perf_counter(); items = _knowledge_items(base, scopes, selected, global_selected); query_ms = _elapsed_ms(started)
    query = (request.query_params.get("knowledge_q") or "").strip(); search_results = []
    if query:
        started = time.perf_counter(); raw = v951._safe_call(base, base.context_engine().search, query, limit=100)
        candidates = raw.get("results", []) if isinstance(raw, dict) else []
        allowed_ids = {str(item.get("id") or "") for item in items}
        search_results = [row for row in candidates if str(row.get("item_id") or row.get("id") or "") in allowed_ids][:50]
        query_ms += _elapsed_ms(started)
    owners_result = v951._safe_call(base, base.scheduler().list_owners); owners = owners_result if isinstance(owners_result, list) else []
    edit_id = (request.query_params.get("edit_knowledge") or "").strip(); current = {}
    if edit_id:
        candidate = v951._safe_call(base, base.context_engine().store.get_item, edit_id)
        if isinstance(candidate, dict) and candidate.get("ok") is not False: current = scopes.attach(candidate)
    view_id = (request.query_params.get("view_knowledge") or "").strip(); view_html = ""
    if view_id:
        candidate = v951._safe_call(base, base.context_engine().store.get_item, view_id)
        if isinstance(candidate, dict) and candidate.get("ok") is not False:
            candidate = scopes.attach(candidate); view_html = f'<section class="card wide"><div class="panel-title"><div><h2>{escape(str(candidate.get("title") or ""))}</h2>{_scope_labels(candidate,names)}</div></div><div class="markdown-viewer">{render_markdown_safe(str(candidate.get("content") or ""))}</div></section>'
    rows = []
    for item in items:
        iid = str(item.get("id") or ""); values = {"ui_view":"knowledge","view_knowledge":iid}
        if selected: values["projects"] = ",".join(selected)
        if global_selected: values["scope_global"] = "1"
        view = "/?" + urlencode(values) + "#knowledge"
        edit_values = dict(values); edit_values.pop("view_knowledge",None); edit_values["edit_knowledge"] = iid
        edit = "/?" + urlencode(edit_values) + "#knowledge"
        rows.append(f'<tr><td><strong>{escape(str(item.get("title") or ""))}</strong><div class="small muted mono">{escape(iid)}</div></td><td><span class="badge">{escape(str(item.get("kind") or ""))}</span></td><td>{_scope_labels(item,names)}</td><td>{float(item.get("priority") or 0.0):.2f}</td><td class="actions"><a data-v960-fragment="knowledge" href="{escape(view, quote=True)}"><button type="button">View</button></a><a data-v960-fragment="knowledge" href="{escape(edit, quote=True)}"><button type="button">Edit</button></a></td></tr>')
    search_rows = ''.join(f'<tr><td>{escape(str(row.get("title") or ""))}</td><td>{escape(str(row.get("kind") or ""))}</td><td>{float(row.get("score") or 0.0):.5f}</td><td>{escape(str(row.get("best_chunk") or row.get("content") or "")[:500])}</td></tr>' for row in search_results)
    selected_owner = str(current.get("owner_id") or (owners[0].get("id") if owners else "")); form_project = str(current.get("project_id") or "") or None
    kind = str(current.get("kind") or "memory"); kind_control = f'<input type="hidden" name="kind" value="{escape(kind)}"><div class="mono">{escape(kind)}</div>' if current else '<select name="kind"><option value="memory">Memory</option><option value="skill">Skill</option></select>'
    hidden_filter = (f'<input type="hidden" name="projects" value="{escape(",".join(selected), quote=True)}">' if selected else '') + ('<input type="hidden" name="scope_global" value="1">' if global_selected else '')
    return f'''<section class="tab-panel" id="panel-knowledge" data-panel="knowledge"><div class="grid">{view_html}
<section class="card wide"><div class="panel-title"><div><h2>Knowledge / Skills</h2><p class="small muted">Multi-project filters are OR across real scope relations.</p></div><span class="badge">{len(items)} risultati</span></div>{_scope_chips(request,projects,selected,global_selected)}{_perf_html({'knowledge_query':query_ms})}</section>
<section class="card wide"><h2>{'Edit knowledge item' if current else 'Add memory / skill'}</h2><form method="post" action="/dashboard/knowledge/save"><input type="hidden" name="csrf" value="{escape(base._csrf_value())}"><input type="hidden" name="item_id" value="{escape(str(current.get('id') or ''))}"><div class="row"><div class="field"><label>Kind</label>{kind_control}</div><div class="field"><label>Owner</label><select name="owner_id" required>{owner_options(owners,selected_owner)}</select></div><div class="field"><label>Primary project</label><select name="project_id">{project_options(projects,form_project)}</select></div><div class="field"><label>Priority</label><input type="number" name="priority" min="0" max="1" step="0.05" value="{escape(str(current.get('priority',0.5)))}"></div></div><div class="field"><label>Title</label><input name="title" required value="{escape(str(current.get('title') or ''), quote=True)}"></div><div class="field"><label>Tags</label><input name="tags" value="{escape(', '.join(current.get('tags') or []), quote=True)}"></div><div class="field"><label>Content (Markdown)</label><textarea name="content" rows="12" required>{escape(str(current.get('content') or ''))}</textarea></div><div class="row"><label><input type="checkbox" name="always_include" value="1"{' checked' if current.get('always_include') else ''}> Always include</label><label><input type="checkbox" name="enabled" value="1"{' checked' if not current or current.get('enabled') else ''}> Enabled</label><button class="primary" type="submit">Save</button></div></form></section>
<section class="card wide"><h2>Search</h2><form method="get" action="/" data-v960-fragment="knowledge"><input type="hidden" name="ui_view" value="knowledge">{hidden_filter}<div class="row"><div class="grow"><input name="knowledge_q" value="{escape(query, quote=True)}" placeholder="Search memories and skills"></div><button type="submit">Search</button></div></form>{('<div class="scroll"><table><thead><tr><th>Item</th><th>Kind</th><th>Score</th><th>Best chunk</th></tr></thead><tbody>'+ (search_rows or '<tr><td colspan="4" class="muted">No matches</td></tr>') +'</tbody></table></div>') if query else ''}</section>
<section class="card wide"><div class="scroll"><table><thead><tr><th>Item</th><th>Kind</th><th>Scopes</th><th>Priority</th><th></th></tr></thead><tbody>{''.join(rows) or '<tr><td colspan="5" class="muted">No items in selected scopes.</td></tr>'}</tbody></table></div></section></div></section>'''


def augment_dashboard(body: str) -> str:
    if "webgui-v960-mail-client" not in body and "</style>" in body:
        body = body.replace("</style>", STYLE + "\n</style>", 1)
    if "id=\"v960-progressive-enhancement\"" not in body and "</body>" in body:
        body = body.replace("</body>", SCRIPT + "\n</body>", 1)
    body = body.replace("<strong>Postmaster</strong><small>Operator console</small>", "<strong>Postmaster</strong><small>Mail client · v9.6</small>", 1)
    return body


def install_webgui_v960(app: Any, base: Any, core: Any, legacy_dashboard: Any) -> Any:
    v951.render_inbox = render_inbox
    v951.render_compose = render_compose
    v951.render_mail_health = render_mail_health
    v951.compose_send = compose_send
    v945.knowledge_fragment = knowledge_fragment

    async def dashboard_home(request: Request):
        response = await legacy_dashboard(request)
        if "text/html" not in str(response.headers.get("content-type", "")).lower(): return response
        try:
            return HTMLResponse(augment_dashboard(response.body.decode("utf-8")), status_code=response.status_code, headers={k:v for k,v in response.headers.items() if k.lower() != "content-length"})
        except Exception:
            base.logger.info("Could not augment v9.6.0 WebGUI", exc_info=True); return response

    async def inbox_fragment(request: Request):
        return HTMLResponse(render_inbox(base, request), headers={"Cache-Control":"no-store"})

    async def knowledge_fragment_route(request: Request):
        return HTMLResponse(knowledge_fragment(base, request), headers={"Cache-Control":"no-store"})

    async def send_route(request: Request):
        if request.method != "POST": return PlainTextResponse("Method Not Allowed", status_code=405, headers={"Allow":"POST"})
        return await compose_send(base, request)

    routes = app.router.routes
    for index, route in enumerate(list(routes)):
        if isinstance(route, Route) and route.path == "/": routes[index] = Route("/", dashboard_home, methods=["GET"]); break
    routes[:] = [route for route in routes if not (isinstance(route, Route) and route.path in {"/dashboard/inbox/fragment","/dashboard/knowledge/fragment","/dashboard/compose/send"})]
    mount_index = next((i for i, route in enumerate(routes) if isinstance(route, Mount)), len(routes))
    routes.insert(mount_index, Route("/dashboard/inbox/fragment", inbox_fragment, methods=["GET"])); mount_index += 1
    routes.insert(mount_index, Route("/dashboard/knowledge/fragment", knowledge_fragment_route, methods=["GET"])); mount_index += 1
    routes.insert(mount_index, Route("/dashboard/compose/send", send_route, methods=["POST"]))
    return dashboard_home
