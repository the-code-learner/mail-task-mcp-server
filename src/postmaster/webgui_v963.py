from __future__ import annotations

import json
import re
import secrets
from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser
from html import escape
from typing import Any
from urllib.parse import urlencode

from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response
from starlette.routing import Route

from . import webgui_v951 as v951
from . import webgui_v952 as v952
from . import webgui_v953 as v953
from . import webgui_v960 as v960
from .email_inventory_v963 import inventory_message
from .email_privacy_v963 import (
    attach_cache_state,
    fetch_passive_resources,
    rewrite_full_html,
    safe_email_html,
)
from .mail_thread_v963 import forward_body, forward_subject, parse_message, reply_all_plan


STYLE = r'''
/* webgui-v963-visual-restoration */
:root{--v963-accent:#4aa3ff;--v963-accent-strong:#1677ff;--v963-violet:#8b5cf6;--v963-green:#25b87a;--v963-amber:#f0a02f;--v963-red:#e45d70}
.topbar{border-bottom-color:color-mix(in srgb,var(--v963-accent) 42%,var(--border));box-shadow:0 8px 30px rgba(10,30,55,.18)}
.nav-link.active,.v960-mailbox-tabs a.active,.v960-scope-chip.active{background:linear-gradient(135deg,color-mix(in srgb,var(--v963-accent-strong) 26%,var(--surface)),color-mix(in srgb,var(--v963-violet) 17%,var(--surface)));border-color:color-mix(in srgb,var(--v963-accent) 65%,var(--border));color:var(--text);box-shadow:0 0 0 1px color-mix(in srgb,var(--v963-accent) 16%,transparent) inset}
button.primary,.btn.primary{background:linear-gradient(135deg,var(--v963-accent-strong),var(--v963-violet));border-color:transparent;color:#fff;font-weight:720}
button.ok,.badge.ok,.v963-chip.ok{border-color:color-mix(in srgb,var(--v963-green) 65%,var(--border));background:color-mix(in srgb,var(--v963-green) 16%,var(--surface));color:var(--text)}
.badge,.v963-chip{display:inline-flex;align-items:center;gap:5px;border:1px solid color-mix(in srgb,var(--v963-accent) 38%,var(--border));background:color-mix(in srgb,var(--v963-accent) 10%,var(--surface));border-radius:999px;padding:3px 8px;font-size:11px;font-weight:680}
.v963-chip.warn{border-color:color-mix(in srgb,var(--v963-amber) 58%,var(--border));background:color-mix(in srgb,var(--v963-amber) 13%,var(--surface))}.v963-chip.danger{border-color:color-mix(in srgb,var(--v963-red) 58%,var(--border));background:color-mix(in srgb,var(--v963-red) 13%,var(--surface))}
.v963-inbox-head{display:flex;gap:12px;align-items:flex-start;justify-content:space-between;flex-wrap:wrap}.v963-refresh{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.v963-refresh form{margin:0}
.v963-mail-row{cursor:pointer}.v963-mail-row.unread td{font-weight:680}.v963-unread-dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--v963-accent)}
.v963-detail{border:1px solid color-mix(in srgb,var(--v963-accent) 32%,var(--border));border-radius:14px;padding:16px;background:color-mix(in srgb,var(--surface) 96%,var(--v963-accent) 4%)}
.v963-detail-actions{display:flex;gap:8px;flex-wrap:wrap;margin:12px 0}.v963-detail-actions a{display:inline-flex;text-decoration:none}.v963-safe-email{border:1px solid var(--border);border-radius:12px;padding:16px;background:var(--surface);overflow:auto}.v963-safe-email a{color:inherit;text-decoration:underline dotted;pointer-events:none}
.v963-warning{border:1px solid color-mix(in srgb,var(--v963-amber) 55%,var(--border));background:color-mix(in srgb,var(--v963-amber) 10%,var(--surface));border-radius:12px;padding:12px;margin:12px 0}.v963-full-frame{width:100%;min-height:520px;border:1px solid var(--border);border-radius:12px;background:white}
.v963-tech summary{font-weight:720}.v963-tech-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:9px;margin:10px 0}.v963-url-table code{white-space:pre-wrap;word-break:break-all}.v963-url-table .snippet{max-width:420px;white-space:pre-wrap;word-break:break-word}
.v963-proxy-card{border-left:4px solid var(--v963-violet)}.v963-proxy-form{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}.v963-proxy-form .wide{grid-column:1/-1}.v963-proxy-form input[type=url],.v963-proxy-form input[type=password]{width:100%}
.v963-thread-compose{border-left:4px solid var(--v963-accent-strong)}.v963-thread-compose textarea,.v963-thread-compose input{width:100%}.v963-thread-compose .v951-formgrid{align-items:start}
#panel-knowledge .v962-collapsible-body form,#panel-knowledge .card.wide form{width:100%;max-width:none}#panel-knowledge .v951-formgrid{grid-template-columns:repeat(2,minmax(0,1fr));width:100%}#panel-knowledge .v951-formgrid .wide,#panel-knowledge textarea[name=content],#panel-knowledge textarea[name=markdown],#panel-knowledge textarea{grid-column:1/-1;width:100%;min-width:0}#panel-knowledge textarea{min-height:320px;resize:vertical}
#panel-projects .v962-project-card,#panel-projects .card{display:flex;flex-direction:column}#panel-projects .actions,#panel-projects .v962-project-actions{margin-top:auto;align-items:center}
@media(max-width:760px){.v963-proxy-form,#panel-knowledge .v951-formgrid{grid-template-columns:1fr}.v963-proxy-form .wide,#panel-knowledge .v951-formgrid .wide{grid-column:1}.v963-full-frame{min-height:420px}}
'''

_CSS_URL_RE = re.compile(r"(?is)url\(\s*(['\"]?)(.*?)\1\s*\)")
_CSS_IMPORT_RE = re.compile(r"(?is)@import\s+(?:url\([^)]*\)|['\"][^'\"]*['\"])[^;]*;?")
_META_BASE_RE = re.compile(r"(?is)<(?:meta|base)\b[^>]*>")
_REMOTE_ATTR_RE = re.compile(r'''(?is)\s(src|background|poster)\s*=\s*(["'])(.*?)\2''')
_LINK_HREF_RE = re.compile(r'''(?is)(<link\b[^>]*?)\shref\s*=\s*(["'])(.*?)\2''')


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _elapsed_label(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return "Mai aggiornato"
    try:
        stamp = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        seconds = max(0, int((_now() - stamp.astimezone(timezone.utc)).total_seconds()))
    except ValueError:
        return "Aggiornamento disponibile"
    if seconds < 60:
        return "Aggiornato meno di un minuto fa"
    minutes = seconds // 60
    if minutes < 60:
        return f"Aggiornato {minutes} minut{'o' if minutes == 1 else 'i'} fa"
    hours = minutes // 60
    if hours < 24:
        return f"Aggiornato {hours} or{'a' if hours == 1 else 'e'} fa"
    return f"Aggiornato {hours // 24} giorni fa"


def _selected(base: Any, request: Request) -> tuple[list[dict[str, Any]], str | None]:
    return v953._selected_account_id(base, request)


def _params(request: Request, account_id: str | None) -> dict[str, str]:
    values = {
        "ui_view": "inbox",
        "mailbox": (request.query_params.get("mailbox") or "INBOX").strip() or "INBOX",
        "subject": (request.query_params.get("subject") or "").strip(),
        "text": (request.query_params.get("text") or "").strip(),
        "since_days": str(v951._bounded_int(request.query_params.get("since_days"), 90, 1, 3650)),
        "page": str(v951._bounded_int(request.query_params.get("page"), 1, 1, 100000)),
    }
    if request.query_params.get("unread_only") == "1":
        values["unread_only"] = "1"
    if account_id:
        values["account_id"] = account_id
    return values


def _href(params: dict[str, str], **changes: Any) -> str:
    values = dict(params)
    for key, value in changes.items():
        if value is None:
            values.pop(key, None)
        else:
            values[key] = str(value)
    return "/?" + urlencode(values) + "#inbox"


def _mailbox_tabs(params: dict[str, str], catalog: list[dict[str, Any]], selected: str) -> str:
    rows = [row for row in catalog if str(row.get("role") or "") in {"received", "sent", "spam", "drafts", "trash"}] or catalog
    labels = {"received": "Inbox", "sent": "Sent", "spam": "Spam", "drafts": "Drafts", "trash": "Trash", "other": "Mailbox"}
    links: list[str] = []
    for row in rows:
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        role = str(row.get("role") or "other")
        label = name if role == "other" else labels.get(role, role.title())
        links.append(
            f'<a data-v960-fragment="inbox" class="{"active" if name == selected else ""}" '
            f'href="{escape(_href(params, mailbox=name, page=1, message_uid=None, full_html=None, full_html_intent=None, compose_mode=None), quote=True)}">{escape(label)}</a>'
        )
    return '<nav class="v960-mailbox-tabs" aria-label="Mailboxes">' + "".join(links) + "</nav>"


def _role(catalog: list[dict[str, Any]], mailbox: str) -> str:
    for row in catalog:
        if str(row.get("name") or "") == mailbox:
            return str(row.get("role") or "other")
    return "received" if mailbox.casefold() == "inbox" else "other"


def _proxy_card(base: Any) -> str:
    proxy = base.privacy_proxy_store().status()
    onboarding = base.postmaster_onboarding_state()
    configured = bool(proxy.get("configured"))
    enabled = bool(proxy.get("enabled"))
    checked = " checked" if enabled else ""
    obf = " checked" if proxy.get("tracking_obfuscation") else ""
    csrf = escape(str(base._csrf_value()), quote=True)
    status = "active" if enabled else "configured but disabled" if configured else "not configured"
    test = ""
    if proxy.get("last_test_at"):
        test = f' · test: {"OK" if proxy.get("last_test_ok") else "failed"}'
    dismiss = ""
    if onboarding.get("privacy_proxy_offer"):
        dismiss = f'''<form method="post" action="/dashboard/privacy-proxy/dismiss"><input type="hidden" name="csrf" value="{csrf}"><button type="submit">Dismiss optional setup</button></form>'''
    return f'''
<details class="card v963-proxy-card"><summary>Privacy Proxy <span class="v963-chip {"ok" if enabled else ""}">{escape(status)}</span></summary>
<p class="small muted">Optional Cloudflare Worker. The shared secret is write-only and never displayed. Full HTML uses it only after per-message confirmation and only for passive resources.</p>
<form method="post" action="/dashboard/privacy-proxy/configure" class="v963-proxy-form"><input type="hidden" name="csrf" value="{csrf}">
<label class="wide">Worker URL <input type="url" name="worker_url" value="{escape(str(proxy.get('worker_url') or ''), quote=True)}" placeholder="https://your-worker.example.workers.dev"></label>
<label class="wide">Shared secret <input type="password" name="secret" autocomplete="new-password" placeholder="Leave blank to keep current secret"></label>
<label><input type="checkbox" name="enabled" value="1"{checked}> Enable Privacy Proxy</label><label><input type="checkbox" name="tracking_obfuscation" value="1"{obf}> Tracking obfuscation</label>
<div class="wide v963-detail-actions"><button class="primary" type="submit">Save proxy settings</button></div></form>
<div class="v963-detail-actions"><form method="post" action="/dashboard/privacy-proxy/test"><input type="hidden" name="csrf" value="{csrf}"><button type="submit">Test connection</button></form>{dismiss}</div>
<p class="small muted">Secret: {"configured" if proxy.get("secret_configured") else "not configured"}{escape(test)}</p></details>'''


def _technical_details(inventory: dict[str, Any], proxy: dict[str, Any]) -> str:
    verdict = str(inventory.get("tracking_verdict") or "Nessun tracking evidente")
    score = int(inventory.get("tracking_score") or 0)
    css = "danger" if score >= 65 else "warn" if score >= 25 else "ok"
    rows: list[str] = []
    for item in inventory.get("urls") or []:
        dims = ""
        if item.get("width") or item.get("height"):
            dims = f"{item.get('width') or '?'} × {item.get('height') or '?'}"
        reasons = "; ".join(str(value) for value in item.get("tracking_reasons") or []) or "—"
        observed = "; ".join(str(value) for value in item.get("observed_evidence") or []) or "—"
        redirect = "—"
        if item.get("redirect_location"):
            redirect = f"{item.get('redirect_status') or ''} Location: {item.get('redirect_location')}"
        rows.append(
            "<tr>"
            f'<td><code>{escape(str(item.get("url") or ""))}</code></td>'
            f'<td>{escape(str(item.get("domain") or "—"))}</td>'
            f'<td>{escape(str(item.get("source_type") or "—"))}</td>'
            f'<td class="snippet">{escape(str(item.get("source_snippet") or "—"))}</td>'
            f'<td>{escape(str(item.get("anchor_text") or "—"))}</td>'
            f'<td>{escape(str(item.get("classification") or "unknown"))}</td>'
            f'<td>{int(item.get("tracking_score") or 0)}/100<br><span class="small muted">{escape(reasons)}</span></td>'
            f'<td>{escape(dims or "—")}</td>'
            f'<td>{escape(str(item.get("cache_status") or "not fetched"))}<br>{escape(str(item.get("proxy_status") or "not contacted"))}</td>'
            f'<td>{escape(redirect)}</td>'
            f'<td>{escape(observed)}<br><span class="small muted">Inference: {escape(str(item.get("inference") or "heuristic"))}</span></td>'
            "</tr>"
        )
    return f'''
<details class="v963-tech"><summary>Dettagli tecnici</summary>
<div class="v963-tech-grid"><div><span class="v963-chip {css}">{escape(verdict)}</span><p><strong>{score}/100</strong></p></div><div><strong>{int(inventory.get('url_count') or 0)}</strong><br><span class="small muted">URL inventariati staticamente</span></div><div><strong>{len(inventory.get('external_domains') or [])}</strong><br><span class="small muted">domini esterni</span></div><div><strong>{int(inventory.get('tracking_pixel_count') or 0)}</strong><br><span class="small muted">possibili tracking pixel</span></div></div>
<p class="small muted">Stima euristica, non una certezza. L'evidenza osservata è mostrata separatamente dalle inferenze. L'inventario statico non effettua GET, HEAD, DNS lookup, preview, prefetch o redirect following.</p>
<p class="small muted">Privacy Proxy: {"active" if proxy.get('enabled') else "inactive"} · Tracking obfuscation: {"active" if proxy.get('tracking_obfuscation') else "inactive"}</p>
<div class="scroll v963-url-table"><table><thead><tr><th>URL</th><th>Domain</th><th>Source</th><th>Source snippet</th><th>Anchor</th><th>Classification</th><th>Tracking</th><th>Declared size</th><th>Cache / proxy</th><th>Redirect</th><th>Observed / inferred</th></tr></thead><tbody>{''.join(rows) or '<tr><td colspan="11" class="muted">No URL found.</td></tr>'}</tbody></table></div></details>'''


def _allow_passive(row: dict[str, Any]) -> bool:
    source = str(row.get("source_type") or "").casefold()
    if source in {"img src", "source src", "video poster", "style url()", "style-block url()"}:
        return True
    if source.endswith(" background"):
        return True
    if source == "link href":
        return bool(re.search(r"(?i)\brel\s*=\s*['\"][^'\"]*stylesheet", str(row.get("source_snippet") or "")))
    return False


def _resource_map(base: Any, inventory: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for row in inventory.get("urls") or []:
        key = str(row.get("cache_key") or "")
        if not key:
            continue
        cached = base.mailbox_cache_store().get_resource(key)
        if not cached or int(cached.get("http_status") or 0) != 200 or cached.get("body") is None:
            continue
        result[str(row.get("url") or "")] = "/dashboard/inbox/resource?" + urlencode({"key": key})
    return result


def _harden_full_html(html: str) -> str:
    value = _META_BASE_RE.sub("", html or "")
    def attr(match: re.Match[str]) -> str:
        name, quote, target = match.group(1), match.group(2), match.group(3)
        target = target.strip()
        if target.startswith("/dashboard/inbox/resource?") or target.startswith("data:"):
            return f' {name}={quote}{target}{quote}'
        return ""
    value = _REMOTE_ATTR_RE.sub(attr, value)
    def link(match: re.Match[str]) -> str:
        prefix, quote, target = match.group(1), match.group(2), match.group(3)
        if target.strip().startswith("/dashboard/inbox/resource?"):
            return f'{prefix} href={quote}{target}{quote}'
        return prefix
    return _LINK_HREF_RE.sub(link, value)


def _full_html_frame(rendered: str) -> str:
    csp = "default-src 'none'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; font-src 'self' data:; media-src 'none'; connect-src 'none'; frame-src 'none'; object-src 'none'; form-action 'none'; base-uri 'none'"
    srcdoc = f'<meta http-equiv="Content-Security-Policy" content="{csp}">{rendered}'
    return f'<iframe class="v963-full-frame" sandbox="allow-same-origin" referrerpolicy="no-referrer" srcdoc="{escape(srcdoc, quote=True)}"></iframe>'


def _thread_compose(base: Any, account_id: str, mailbox: str, uid: str, mode: str, raw: bytes, body_text: str) -> str:
    message = parse_message(raw)
    csrf = escape(str(base._csrf_value()), quote=True)
    settings = base.account_store().settings(account_id)
    plan = reply_all_plan(message, settings)
    to_display = ", ".join(plan["to"])
    cc_value = ", ".join(plan["cc"]) if mode == "reply_all" else ""
    if mode == "forward":
        title = "Forward"
        subject = forward_subject(str(message.get("Subject", "") or ""))
        body = forward_body(message, body_text)
        recipient = '<label>To <input name="to" required placeholder="recipient@example.com"></label><label>Cc <input name="cc"></label>'
        attachments = sum(1 for part in message.walk() if part.get_filename() or part.get_content_disposition() == "attachment")
        attachment_ui = f'<label class="wide"><input type="checkbox" name="include_attachments" value="1" checked> Reuse {attachments} original attachment(s) via existing source_mailbox/source_uid semantics</label>' if attachments else ""
    else:
        title = "Reply to all" if mode == "reply_all" else "Reply"
        subject = str(plan["subject"])
        body = ""
        recipient = f'<div><span class="small muted">To</span><br>{escape(to_display)}</div><label>Cc <input name="cc" value="{escape(cc_value, quote=True)}"></label>'
        attachment_ui = ""
    return f'''
<details class="card v963-thread-compose" open><summary>{escape(title)} · Compose / draft</summary>
<form method="post" action="/dashboard/inbox/draft"><input type="hidden" name="csrf" value="{csrf}"><input type="hidden" name="account_id" value="{escape(account_id, quote=True)}"><input type="hidden" name="mailbox" value="{escape(mailbox, quote=True)}"><input type="hidden" name="uid" value="{escape(uid, quote=True)}"><input type="hidden" name="draft_mode" value="{escape(mode, quote=True)}">
<div class="v951-formgrid">{recipient}<label class="wide">Subject <input name="subject" value="{escape(subject, quote=True)}" {"readonly" if mode != "forward" else ""}></label><label class="wide">Message <textarea name="body" rows="10" required>{escape(body)}</textarea></label>{attachment_ui}</div>
<p class="small muted">This action creates a draft only. It never sends automatically. Reply threading uses Reply-To/From, In-Reply-To and References from the selected message; Reply-all never reads Bcc.</p><div class="v963-detail-actions"><button class="primary" type="submit">Save draft</button></div></form></details>'''


def _detail(base: Any, params: dict[str, str], account_id: str, mailbox: str, role: str, uid: str, request: Request) -> str:
    client = base.mail_client(account_id)
    detail = base.mailbox_cache_synchronizer().ensure_body(client, account_id=account_id, mailbox=mailbox, uid=uid)
    raw = base.mailbox_cache_store().raw_message(account_id, mailbox, uid)
    if not raw:
        return '<div class="flash danger">Message body unavailable.</div>'
    message = BytesParser(policy=policy.default).parsebytes(raw)
    body_html = str(detail.get("body_html") or "")
    body_text = str(detail.get("body") or "")
    inventory = attach_cache_state(inventory_message(body_html, body_text), base.mailbox_cache_store(), account_id=account_id, mailbox=mailbox, uid=uid)
    proxy = base.privacy_proxy_store().status()
    safe = safe_email_html(body_html) if body_html else f'<pre>{escape(body_text[:100000])}</pre>'
    close = _href(params, message_uid=None, compose_mode=None, full_html=None, full_html_intent=None)
    action_base = {**params, "message_uid": uid}
    buttons = []
    if role != "sent":
        buttons.extend([
            f'<a data-v960-fragment="inbox" href="{escape(_href(action_base, compose_mode="reply"), quote=True)}"><button type="button">Reply</button></a>',
            f'<a data-v960-fragment="inbox" href="{escape(_href(action_base, compose_mode="reply_all"), quote=True)}"><button type="button">Reply to all</button></a>',
        ])
    buttons.append(f'<a data-v960-fragment="inbox" href="{escape(_href(action_base, compose_mode="forward"), quote=True)}"><button type="button">Forward</button></a>')
    full_intent = request.query_params.get("full_html_intent") == "1"
    full_render = request.query_params.get("full_html") == "1"
    buttons.append(f'<a data-v960-fragment="inbox" href="{escape(_href(action_base, full_html_intent=1, full_html=None, compose_mode=None), quote=True)}"><button type="button">Visualizza HTML completo</button></a>')
    warning = ""
    if full_intent and not full_render:
        w = inventory.get("warning") or {}
        csrf = escape(str(base._csrf_value()), quote=True)
        disabled = "" if proxy.get("enabled") else " disabled"
        proxy_note = "" if proxy.get("enabled") else '<p class="small muted">Privacy Proxy is not active: no remote resource will be fetched.</p>'
        warning = f'''<div class="v963-warning"><strong>Prima conferma: nessuna risorsa è stata caricata.</strong><p>{int(w.get('remote_images') or 0)} immagini remote · {int(w.get('possible_tracking_pixels') or 0)} possibili tracking pixel · {int(w.get('external_domains') or 0)} domini esterni.</p>{proxy_note}<form method="post" action="/dashboard/inbox/full-html"><input type="hidden" name="csrf" value="{csrf}"><input type="hidden" name="account_id" value="{escape(account_id, quote=True)}"><input type="hidden" name="mailbox" value="{escape(mailbox, quote=True)}"><input type="hidden" name="uid" value="{escape(uid, quote=True)}"><button class="primary" type="submit"{disabled}>Conferma e carica HTML completo</button></form></div>'''
    body_view = f'<div class="v963-safe-email">{safe}</div>'
    mode_label = '<span class="v963-chip ok">Email sicura · default</span>'
    if full_render:
        rendered = rewrite_full_html(body_html, _resource_map(base, inventory))
        rendered = _harden_full_html(rendered)
        body_view = _full_html_frame(rendered)
        mode_label = '<span class="v963-chip warn">HTML completo · passive resources via local cache</span>'
    compose = ""
    compose_mode = (request.query_params.get("compose_mode") or "").strip()
    if compose_mode in {"reply", "reply_all", "forward"}:
        compose = _thread_compose(base, account_id, mailbox, uid, compose_mode, raw, body_text)
    counterpart = str(detail.get("to") if role == "sent" else detail.get("from") or "")
    return f'''<div class="v963-detail"><div class="v963-inbox-head"><div><h3>{escape(str(detail.get('subject') or 'Message'))}</h3><p class="small muted">{escape('To' if role == 'sent' else 'From')}: {escape(counterpart)} · {escape(str(detail.get('date') or ''))}</p></div><a data-v960-fragment="inbox" href="{escape(close, quote=True)}">Close</a></div><div class="v963-detail-actions">{''.join(buttons)}</div>{mode_label}{warning}{body_view}{_technical_details(inventory, proxy)}{compose}</div>'''


def render_inbox_v963(base: Any, request: Request) -> str:
    accounts, account_id = _selected(base, request)
    params = _params(request, account_id)
    flash = escape(str(request.query_params.get("v963_result") or ""))
    if not account_id:
        return f'''<section class="tab-panel" id="panel-inbox" data-panel="inbox"><div class="v951-pagehead"><div><h2>Inbox</h2><p>Fresh installation: configure an email account before mailbox sync. No email is sent by onboarding.</p></div></div>{_proxy_card(base)}</section>'''
    store = base.mailbox_cache_store()
    catalog = store.list_mailboxes(account_id)
    if not catalog:
        try:
            account = base.account_store().get_account(account_id)
            inbox = str(account.get("inbox_mailbox") or "INBOX")
        except Exception:
            inbox = "INBOX"
        catalog = [{"name": inbox, "role": "received", "flags": [], "last_sync_at": ""}]
    available = [str(row.get("name") or "") for row in catalog if row.get("name")]
    mailbox = params.get("mailbox") or "INBOX"
    if mailbox not in available:
        mailbox = next((str(row.get("name")) for row in catalog if row.get("role") == "received"), available[0])
        params["mailbox"] = mailbox
    role = _role(catalog, mailbox)
    page = int(params["page"])
    query = store.query_messages(
        account_id=account_id,
        mailbox=mailbox,
        page=page,
        page_size=25,
        subject=params.get("subject", ""),
        text=params.get("text", ""),
        unread_only=params.get("unread_only") == "1",
        since_days=int(params["since_days"]),
    )
    rows: list[str] = []
    uid = (request.query_params.get("message_uid") or "").strip()
    for row in query["messages"]:
        row_uid = str(row.get("uid") or "")
        href = _href(params, message_uid=row_uid, compose_mode=None, full_html=None, full_html_intent=None)
        counterpart = str(row.get("to") if role == "sent" else row.get("from") or "")
        css = "v963-mail-row" + (" unread" if row.get("seen") is False else "")
        dot = '<span class="v963-unread-dot" title="Unread"></span>' if row.get("seen") is False else ""
        rows.append(f'<tr class="{css}" data-v960-href="{escape(href, quote=True)}"><td>{dot}</td><td>{escape(counterpart)}</td><td>{escape(str(row.get("subject") or ""))}</td><td>{escape(str(row.get("date") or ""))}</td></tr>')
        if uid and row_uid == uid:
            try:
                detail = _detail(base, params, account_id, mailbox, role, uid, request)
            except Exception as exc:
                detail = f'<div class="flash danger">Could not open message: {escape(type(exc).__name__ + ": " + str(exc))}</div>'
            rows.append(f'<tr class="v960-inline-detail"><td colspan="4">{detail}</td></tr>')
    state = store.state(account_id, mailbox)
    csrf = escape(str(base._csrf_value()), quote=True)
    previous = f'<a data-v960-fragment="inbox" href="{escape(_href(params, page=page-1, message_uid=None), quote=True)}">← Previous</a>' if page > 1 else ""
    next_link = f'<a data-v960-fragment="inbox" href="{escape(_href(params, page=page+1, message_uid=None), quote=True)}">Next →</a>' if page * 25 < int(query["total"]) else ""
    checked = " checked" if params.get("unread_only") == "1" else ""
    table = '<div class="scroll"><table class="v960-mail-table"><thead><tr><th></th><th>' + ("To" if role == "sent" else "From") + '</th><th>Subject</th><th>Date</th></tr></thead><tbody>' + ("".join(rows) or '<tr><td colspan="4" class="muted">No cached messages matched. Use Aggiorna for an explicit incremental sync.</td></tr>') + '</tbody></table></div>'
    return f'''
<section class="tab-panel" id="panel-inbox" data-panel="inbox"><div class="v963-inbox-head"><div><h2>Inbox</h2><p>Cache-first mailbox view · IMAP remains source of truth · Safe Email is the default.</p></div><div class="v963-refresh"><span class="v963-chip">{escape(_elapsed_label(str(state.get('last_sync_at') or '')))}</span><form method="post" action="/dashboard/inbox/refresh"><input type="hidden" name="csrf" value="{csrf}"><input type="hidden" name="account_id" value="{escape(account_id, quote=True)}"><input type="hidden" name="mailbox" value="{escape(mailbox, quote=True)}"><button class="primary" type="submit">Aggiorna</button></form></div></div>
{f'<div class="flash">{flash}</div>' if flash else ''}{_mailbox_tabs(params, catalog, mailbox)}
<form method="get" action="/" class="v951-toolbar" data-v960-fragment="inbox"><input type="hidden" name="ui_view" value="inbox"><input type="hidden" name="page" value="1"><label>Account {v953._account_select(accounts, account_id)}</label><label>Mailbox {v953._mailbox_select(available, mailbox)}</label><label>Subject <input name="subject" value="{escape(params.get('subject',''), quote=True)}"></label><label>Text <input name="text" value="{escape(params.get('text',''), quote=True)}"></label><label>Since days <input type="number" min="1" max="3650" name="since_days" value="{escape(params['since_days'], quote=True)}"></label><label><input type="checkbox" name="unread_only" value="1"{checked}> unread only</label><button type="submit">Filter cache</button></form>
{table}<div class="v960-pagination"><span>{previous}</span><span class="small muted">Page {page} · {int(query['total'])} cached result(s)</span><span>{next_link}</span></div>{_proxy_card(base)}
</section>'''


async def refresh_inbox(base: Any, request: Request):
    form, error = await base._verified_form(request)
    if error:
        return error
    account_id = str(form.get("account_id") or "").strip()
    mailbox = str(form.get("mailbox") or "INBOX").strip() or "INBOX"
    if not account_id:
        return RedirectResponse("/?ui_view=inbox&v963_result=Account+required#inbox", status_code=303)
    client = base.mail_client(account_id)
    catalog = base.mailbox_cache_store().list_mailboxes(account_id)
    row = next((item for item in catalog if str(item.get("name") or "") == mailbox), None)
    try:
        if row:
            result = base.mailbox_cache_synchronizer().sync_mailbox(client, account_id=account_id, mailbox=mailbox, role=str(row.get("role") or "other"))
        else:
            result = base.mailbox_cache_synchronizer().sync_account(client, account_id=account_id)
        text = "Inbox cache updated" if result.get("ok") else "Sync completed with errors"
    except Exception as exc:
        text = f"Sync failed: {type(exc).__name__}"
    return RedirectResponse("/?" + urlencode({"ui_view": "inbox", "account_id": account_id, "mailbox": mailbox, "v963_result": text}) + "#inbox", status_code=303)


async def confirm_full_html(base: Any, request: Request):
    form, error = await base._verified_form(request)
    if error:
        return error
    account_id = str(form.get("account_id") or "").strip(); mailbox = str(form.get("mailbox") or "").strip(); uid = str(form.get("uid") or "").strip()
    if not account_id or not mailbox or not uid:
        return RedirectResponse("/?ui_view=inbox&v963_result=Invalid+message#inbox", status_code=303)
    proxy_status = base.privacy_proxy_store().status()
    if not proxy_status.get("enabled"):
        return RedirectResponse("/?" + urlencode({"ui_view":"inbox","account_id":account_id,"mailbox":mailbox,"message_uid":uid,"v963_result":"Privacy Proxy is not active"}) + "#inbox", status_code=303)
    detail = base.mailbox_cache_synchronizer().ensure_body(base.mail_client(account_id), account_id=account_id, mailbox=mailbox, uid=uid)
    raw_inventory = inventory_message(str(detail.get("body_html") or ""), str(detail.get("body") or ""))
    filtered = dict(raw_inventory); filtered["urls"] = []
    for raw in raw_inventory.get("urls") or []:
        row = dict(raw); row["passive_resource"] = _allow_passive(row); filtered["urls"].append(row)
    fetch_passive_resources(filtered, cache=base.mailbox_cache_store(), proxy=base.privacy_proxy_client(), account_id=account_id, mailbox=mailbox, uid=uid)
    return RedirectResponse("/?" + urlencode({"ui_view":"inbox","account_id":account_id,"mailbox":mailbox,"message_uid":uid,"full_html":"1"}) + "#inbox", status_code=303)


async def save_thread_draft(base: Any, request: Request):
    form, error = await base._verified_form(request)
    if error:
        return error
    account_id = str(form.get("account_id") or "").strip(); mailbox = str(form.get("mailbox") or "").strip(); uid = str(form.get("uid") or "").strip(); mode = str(form.get("draft_mode") or "").strip()
    body = str(form.get("body") or ""); cc = v960._split_addresses(form.get("cc"))
    try:
        detail = base.mailbox_cache_synchronizer().ensure_body(base.mail_client(account_id), account_id=account_id, mailbox=mailbox, uid=uid)
        raw = base.mailbox_cache_store().raw_message(account_id, mailbox, uid)
        if not raw:
            raise ValueError("cached MIME unavailable")
        message = parse_message(raw)
        if mode in {"reply", "reply_all"}:
            if mode == "reply_all":
                plan = reply_all_plan(message, base.account_store().settings(account_id))
                cc = list(dict.fromkeys(plan["cc"] + cc))
            result = base.create_reply_draft(mailbox=mailbox, uid=uid, body=body, cc=cc or None, account_id=account_id)
        elif mode == "forward":
            to = v960._split_addresses(form.get("to"))
            if not to:
                raise ValueError("Forward recipient required")
            specs: list[dict[str, Any]] = []
            if form.get("include_attachments"):
                index = 0
                for part in message.walk():
                    if part.get_filename() or part.get_content_disposition() == "attachment":
                        specs.append({"source_mailbox": mailbox, "source_uid": uid, "index": index})
                        index += 1
            result = base.create_draft(to=to, cc=cc or None, subject=str(form.get("subject") or forward_subject(str(message.get("Subject", "") or ""))), body=body, attachments=specs or None, account_id=account_id)
        else:
            raise ValueError("Unsupported draft mode")
        ok = isinstance(result, dict) and result.get("ok") is not False
        text = "Draft saved" if ok else "Draft failed: " + str(result.get("error") if isinstance(result, dict) else result)[:160]
    except Exception as exc:
        text = f"Draft failed: {type(exc).__name__}: {exc}"[:180]
    return RedirectResponse("/?" + urlencode({"ui_view":"inbox","account_id":account_id,"mailbox":mailbox,"message_uid":uid,"v963_result":text}) + "#inbox", status_code=303)


async def configure_proxy(base: Any, request: Request):
    form, error = await base._verified_form(request)
    if error:
        return error
    try:
        base.privacy_proxy_store().configure(worker_url=str(form.get("worker_url") or "").strip(), secret=str(form.get("secret") or "").strip() or None, enabled=bool(form.get("enabled")), tracking_obfuscation=bool(form.get("tracking_obfuscation")))
        text = "Privacy Proxy settings saved"
    except Exception as exc:
        text = f"Proxy settings failed: {type(exc).__name__}: {exc}"[:180]
    return RedirectResponse("/?" + urlencode({"ui_view":"inbox","v963_result":text}) + "#inbox", status_code=303)


async def test_proxy(base: Any, request: Request):
    form, error = await base._verified_form(request)
    if error:
        return error
    result = base.privacy_proxy_client().test_connection()
    text = "Privacy Proxy connection OK" if result.get("ok") else "Privacy Proxy connection failed"
    return RedirectResponse("/?" + urlencode({"ui_view":"inbox","v963_result":text}) + "#inbox", status_code=303)


async def dismiss_proxy(base: Any, request: Request):
    form, error = await base._verified_form(request)
    if error:
        return error
    base.privacy_proxy_store().set_onboarding("privacy_proxy_offer", "dismissed")
    return RedirectResponse("/?ui_view=inbox&v963_result=Optional+proxy+setup+dismissed#inbox", status_code=303)


def resource_response(base: Any, request: Request):
    key = str(request.query_params.get("key") or "")
    if not key or len(key) > 2048:
        return Response(status_code=404)
    item = base.mailbox_cache_store().get_resource(key)
    if not item or int(item.get("http_status") or 0) != 200 or item.get("body") is None:
        return Response(status_code=404)
    body = bytes(item.get("body") or b"")
    content_type = str(item.get("content_type") or "application/octet-stream").split(";", 1)[0].strip().casefold()
    if content_type == "text/css":
        text = body.decode("utf-8", errors="replace")
        text = _CSS_IMPORT_RE.sub("", text)
        text = _CSS_URL_RE.sub('url("")', text)
        body = text.encode("utf-8")
    return Response(body, media_type=content_type, headers={"Cache-Control":"private, max-age=86400", "X-Content-Type-Options":"nosniff", "Content-Security-Policy":"default-src 'none'", "Referrer-Policy":"no-referrer"})


def install_webgui_v963(app: Any, base: Any) -> None:
    """Install v9.6.3 presentation/cache renderer while preserving the v9.6.2 lazy shell."""
    from . import webgui_v962 as v962
    if "webgui-v963-visual-restoration" not in v962.BASE_STYLE:
        v962.BASE_STYLE += STYLE
    v960.render_inbox = lambda proxied_base, request: render_inbox_v963(proxied_base, request)
    v951.render_inbox = v960.render_inbox
    routes = [
        ("/dashboard/inbox/refresh", refresh_inbox, ["POST"], "v963_inbox_refresh"),
        ("/dashboard/inbox/full-html", confirm_full_html, ["POST"], "v963_full_html"),
        ("/dashboard/inbox/draft", save_thread_draft, ["POST"], "v963_inbox_draft"),
        ("/dashboard/privacy-proxy/configure", configure_proxy, ["POST"], "v963_proxy_configure"),
        ("/dashboard/privacy-proxy/test", test_proxy, ["POST"], "v963_proxy_test"),
        ("/dashboard/privacy-proxy/dismiss", dismiss_proxy, ["POST"], "v963_proxy_dismiss"),
        ("/dashboard/inbox/resource", resource_response, ["GET"], "v963_cached_resource"),
    ]
    existing = {getattr(route, "path", "") for route in app.router.routes}
    for path, fn, methods, name in routes:
        if path in existing:
            continue
        async def endpoint(request: Request, _fn=fn):
            result = _fn(base, request)
            if hasattr(result, "__await__"):
                result = await result
            return result
        app.router.routes.insert(0, Route(path, endpoint, methods=methods, name=name))


__all__ = ["install_webgui_v963", "render_inbox_v963", "STYLE", "_allow_passive", "_harden_full_html"]
