from __future__ import annotations

import hashlib
from html import escape
from typing import Any, Callable
from urllib.parse import urlencode

from starlette.background import BackgroundTask
from starlette.requests import Request
from starlette.responses import PlainTextResponse, RedirectResponse
from starlette.routing import Mount, Route

from . import runtime_control
from . import webgui_v951 as v951
from . import webgui_v952 as v952


_ACCOUNT_PALETTE = (
    "#64b5f6", "#81c784", "#ffb74d", "#ba68c8", "#4dd0e1", "#f06292",
    "#aed581", "#ffd54f", "#7986cb", "#4db6ac", "#ff8a65", "#90a4ae",
)


STYLE = r'''
/* webgui-v953-admin-ux */
.v953-account-chip { display:inline-flex;align-items:center;gap:6px;border:1px solid var(--account-color);border-radius:999px;padding:3px 8px;font-size:11px;font-weight:700; }
.v953-account-dot { width:8px;height:8px;border-radius:50%;background:var(--account-color);display:inline-block; }
.v953-account-card { border-left:4px solid var(--account-color); }
.v953-account-grid { display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:10px;margin:12px 0 16px; }
.v953-system-actions { display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px;margin:14px 0; }
.v953-system-actions .card { margin:0; }
.v953-warning { border-color:#d6a84d; }
.v953-inline { display:flex;gap:8px;align-items:end;flex-wrap:wrap; }
.v953-inline label { display:flex;flex-direction:column;gap:4px;color:var(--muted);font-size:11px; }
.v953-inline select { min-width:180px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--text);padding:8px; }
'''


def _account_rows(base: Any) -> list[dict[str, Any]]:
    result: Any = None
    if hasattr(base, "list_email_accounts"):
        result = v951._safe_call(base, base.list_email_accounts)
    if isinstance(result, dict):
        rows = result.get("accounts")
    else:
        rows = result
    if not isinstance(rows, list):
        try:
            rows = base.account_store().list_accounts(include_disabled=False)
        except Exception:
            rows = []
    return [row for row in rows if isinstance(row, dict) and row.get("enabled", True)]


def _account_id(row: dict[str, Any]) -> str:
    return str(row.get("id") or row.get("account_id") or "").strip()


def _account_label(row: dict[str, Any]) -> str:
    label = str(row.get("label") or "").strip()
    email = str(row.get("email_address") or row.get("email") or "").strip()
    if label and email and label.casefold() != email.casefold():
        return f"{label} — {email}"
    return label or email or _account_id(row) or "Mail account"


def _default_account_id(accounts: list[dict[str, Any]]) -> str | None:
    for row in accounts:
        if row.get("is_default") and _account_id(row):
            return _account_id(row)
    return _account_id(accounts[0]) if accounts else None


def _selected_account_id(base: Any, request: Request) -> tuple[list[dict[str, Any]], str | None]:
    accounts = _account_rows(base)
    requested = (
        request.query_params.get("account_id")
        or request.query_params.get("account")
        or ""
    ).strip()
    valid = {_account_id(row) for row in accounts if _account_id(row)}
    return accounts, requested if requested in valid else _default_account_id(accounts)


def _account_select(
    accounts: list[dict[str, Any]],
    selected: str | None,
    *,
    name: str = "account_id",
    include_all: bool = False,
) -> str:
    options = []
    if include_all:
        options.append(f'<option value=""{" selected" if selected is None else ""}>All accounts</option>')
    for row in accounts:
        account_id = _account_id(row)
        if not account_id:
            continue
        is_selected = " selected" if account_id == selected else ""
        default_note = " (default)" if row.get("is_default") else ""
        options.append(
            f'<option value="{escape(account_id, quote=True)}"{is_selected}>'
            f'{escape(_account_label(row) + default_note)}</option>'
        )
    if not options:
        options.append('<option value="">No enabled accounts</option>')
    return f'<select name="{escape(name, quote=True)}">' + "".join(options) + "</select>"


def _mailboxes(base: Any, account_id: str | None) -> tuple[list[str], bool]:
    if not account_id or not hasattr(base, "list_mailboxes"):
        return ["INBOX"], False
    result = v951._safe_call(base, base.list_mailboxes, account_id=account_id)
    if isinstance(result, dict):
        values = result.get("mailboxes") or result.get("results")
    else:
        values = result
    if not isinstance(values, list):
        return ["INBOX"], False
    names = []
    for value in values:
        if isinstance(value, str) and value.strip():
            names.append(value.strip())
        elif isinstance(value, dict):
            name = str(value.get("name") or value.get("mailbox") or "").strip()
            if name:
                names.append(name)
    names = list(dict.fromkeys(names))
    return (names or ["INBOX"]), bool(names)


def _mailbox_select(mailboxes: list[str], selected: str) -> str:
    options = []
    for name in mailboxes:
        mark = " selected" if name == selected else ""
        options.append(f'<option value="{escape(name, quote=True)}"{mark}>{escape(name)}</option>')
    return '<select name="mailbox">' + "".join(options) + "</select>"


def account_color_map(accounts: list[dict[str, Any]]) -> dict[str, str]:
    """Assign stable, dark-theme-safe colors; resolve palette collisions deterministically."""
    used: set[int] = set()
    result: dict[str, str] = {}
    for account_id in sorted((_account_id(row) for row in accounts if _account_id(row)), key=str.casefold):
        digest = hashlib.sha256(account_id.encode("utf-8")).digest()
        slot = int.from_bytes(digest[:2], "big") % len(_ACCOUNT_PALETTE)
        start = slot
        while slot in used and len(used) < len(_ACCOUNT_PALETTE):
            slot = (slot + 1) % len(_ACCOUNT_PALETTE)
            if slot == start:
                break
        used.add(slot)
        result[account_id] = _ACCOUNT_PALETTE[slot]
    return result


def _account_chip(row: dict[str, Any], color: str) -> str:
    return (
        f'<span class="v953-account-chip" style="--account-color:{escape(color, quote=True)}">'
        '<span class="v953-account-dot" aria-hidden="true"></span>'
        f'{escape(_account_label(row))}</span>'
    )


def render_mail_health(base: Any, request: Request) -> str:
    accounts, account_id = _selected_account_id(base, request)
    run = request.query_params.get("health_snapshot") == "1"
    result: Any = None
    selector_value = (request.query_params.get("dkim_selector") or "").strip()
    if run:
        result = v951._safe_call(
            base,
            base.test_email_account,
            account_id=account_id,
            refresh=False,
            dkim_selector=selector_value or None,
        )
    csrf = escape(str(base._csrf_value()), quote=True)
    selector = escape(selector_value, quote=True)
    cards = ""
    if isinstance(result, dict):
        for key in ("smtp", "imap", "dns", "tls"):
            if key in result:
                cards += f'<section class="card"><h3>{escape(key.upper())}</h3>{v951._status_rows(result.get(key))}</section>'
        reliability = {key: result.get(key) for key in ("ok", "errors", "warnings") if key in result}
        cards += f'<section class="card"><h3>Reliability</h3>{v951._status_rows(reliability)}</section>'
    else:
        cards = '<section class="card"><p class="muted">Read a current snapshot or force-refresh the existing diagnostics. No historical series is fabricated.</p></section>'
    raw = v951._details(result) if result is not None else ""
    refreshed = '<div class="flash">Mail Health diagnostics refreshed.</div>' if request.query_params.get("health_refreshed") == "1" else ""
    selected_value = escape(account_id or "", quote=True)
    return f'''
<section class="tab-panel" id="panel-mail-health" data-panel="mail-health">
<div class="v951-pagehead"><div><h2>Mail Health</h2><p>Structured current diagnostics for configured sender accounts.</p></div></div>
{refreshed}
<form method="get" action="/" class="v951-toolbar">
<input type="hidden" name="ui_view" value="mail-health"><input type="hidden" name="health_snapshot" value="1">
<label>Account {_account_select(accounts, account_id)}</label>
<label>DKIM selector (optional) <input name="dkim_selector" value="{selector}" placeholder="selector"></label>
<button type="submit">Read current snapshot</button>
</form>
<div class="v951-grid">{cards}</div>{raw}
<div class="notice"><strong>Truthful health semantics:</strong> no DKIM selector is not a DKIM failure; optional standards may be absent without making Postmaster unhealthy; pure TLS-handshake latency is not invented.</div>
<details class="v951-details"><summary>Force-refresh diagnostics</summary>
<form method="post" action="/dashboard/mail-health/refresh">
<input type="hidden" name="csrf" value="{csrf}"><input type="hidden" name="account_id" value="{selected_value}"><input type="hidden" name="dkim_selector" value="{selector}"><button type="submit">Refresh diagnostics</button>
</form>
<p class="small muted">Refresh remains an authenticated POST with CSRF verification and returns to this Mail Health view.</p>
</details>
</section>'''


def render_inbox(base: Any, request: Request) -> str:
    params = v952._canonical_inbox_params(request)
    accounts, account_id = _selected_account_id(base, request)
    if account_id:
        params["account_id"] = account_id
    else:
        params.pop("account_id", None)
    active = (
        request.query_params.get("ui_view") == "inbox"
        or request.query_params.get("inbox_search") == "1"
        or bool(params.get("message_uid"))
    )
    mailbox = params["mailbox"]
    mailbox_values, mailbox_live = _mailboxes(base, account_id) if active else ([mailbox], False)
    if mailbox_live and mailbox not in mailbox_values:
        mailbox = "INBOX" if "INBOX" in mailbox_values else mailbox_values[0]
        params["mailbox"] = mailbox
        params.pop("message_uid", None)
    elif mailbox not in mailbox_values:
        mailbox_values.append(mailbox)
    uid = params.get("message_uid", "")
    search_requested = active and bool(accounts)
    results: list[dict[str, Any]] = []
    detail: Any = None
    if search_requested:
        result = v951._safe_call(
            base,
            base.search_emails,
            mailbox=mailbox,
            subject=params.get("subject") or None,
            text=params.get("text") or None,
            unread_only=params.get("unread_only") == "1",
            since_days=int(params["since_days"]),
            limit=50,
            account_id=account_id,
        )
        results = v951._list_result(result, "emails", "results", "messages")
    if uid and account_id:
        detail = v951._safe_call(base, base.get_email, mailbox=mailbox, uid=uid, account_id=account_id)
    rows = []
    for row in results:
        row_uid = str(row.get("uid") or row.get("id") or "").strip()
        if not row_uid:
            continue
        href = v952._inbox_url(params, uid=row_uid)
        rows.append(
            '<tr>'
            f'<td><code>{escape(row_uid)}</code></td>'
            f'<td>{escape(str(row.get("from") or row.get("from_address") or ""))}</td>'
            f'<td>{escape(str(row.get("subject") or ""))}</td>'
            f'<td>{escape(str(row.get("date") or row.get("received_at") or ""))}</td>'
            f'<td><a href="{escape(href, quote=True)}">View</a></td>'
            '</tr>'
        )
    if not accounts:
        empty = '<tr><td colspan="5" class="muted">No enabled email accounts are configured.</td></tr>'
    elif search_requested:
        empty = '<tr><td colspan="5" class="muted">No messages matched this search.</td></tr>'
    else:
        empty = '<tr><td colspan="5" class="muted">Open Inbox to load the default account.</td></tr>'
    table = (
        '<div class="scroll"><table><thead><tr><th>UID</th><th>From</th><th>Subject</th><th>Date</th><th></th></tr></thead><tbody>'
        + ("".join(rows) or empty) + '</tbody></table></div>'
    )
    detail_html = ""
    if isinstance(detail, dict):
        subject = escape(str(detail.get("subject") or "Message detail"))
        text = str(detail.get("body_text") or detail.get("text") or detail.get("body") or "")
        diagnostics = []
        for key in (
            "authentication_results", "mime_structure", "received_chain", "auto_reply",
            "list_headers", "spam_headers", "tracking", "delivery_state", "conversation_state",
        ):
            if key in detail:
                diagnostics.append(f'<section class="card"><h3>{escape(key.replace("_", " ").title())}</h3>{v951._status_rows(detail.get(key))}</section>')
        back = v952._inbox_url(params)
        detail_html = (
            f'<p><a href="{escape(back, quote=True)}">← Back to results</a></p>'
            f'<div class="v951-pagehead"><div><h3>{subject}</h3><p>Mailbox {escape(mailbox)} · UID {escape(uid)}</p></div></div>'
            f'<section class="card"><pre class="v951-message">{escape(text[:20000])}</pre></section>'
            f'<div class="v951-grid'>{"".join(diagnostics)}</div>{v951._details(detail)}'
        )
    checked = " checked" if params.get("unread_only") == "1" else ""
    return f'''
<section class="tab-panel" id="panel-inbox" data-panel="inbox">
<div class="v951-pagehead"><div><h2>Inbox</h2><p>The selected configured account uses the existing IMAP search/read path.</p></div></div>
<form method="get" action="/dashboard/inbox/search" class="v951-toolbar">
<label>Account {_account_select(accounts, account_id)}</label>
<label>Mailbox {_mailbox_select(mailbox_values, mailbox)}</label>
<label>Subject <input name="subject" value="{escape(params.get("subject", ""), quote=True)}"></label>
<label>Text <input name="text" value="{escape(params.get("text", ""), quote=True)}"></label>
<label>Since days <input type="number" min="1" max="3650" name="since_days" value="{escape(params["since_days"], quote=True)}"></label>
<label><input type="checkbox" name="unread_only" value="1"{checked}> unread only</label>
<button type="submit">Search</button>
</form>{table}{detail_html}
</section>'''


def render_compose(base: Any, request: Request) -> str:
    accounts, account_id = _selected_account_id(base, request)
    csrf = escape(str(base._csrf_value()), quote=True)
    flash = escape(request.query_params.get("compose_result") or "")
    banner = f'<div class="flash">{flash}</div>' if flash else ""
    return f'''
<section class="tab-panel" id="panel-compose" data-panel="compose">
<div class="v951-pagehead"><div><h2>Compose</h2><p>Existing send semantics with explicit newsletter and DSN controls. Tracking alone never enables unsubscribe headers.</p></div></div>{banner}
<form method="post" action="/dashboard/compose/send" class="card v951-compose">
<input type="hidden" name="csrf" value="{csrf}">
<div class="v951-formgrid">
<label>Account {_account_select(accounts, account_id)}</label>
<label>To <input name="to" type="text" required placeholder="recipient@example.com"></label>
<label class="wide">Subject <input name="subject" required></label>
<label class="wide">Message <textarea name="body" rows="12"></textarea></label>
</div>
<div class="v951-checks">
<label><input type="checkbox" name="track_opens" value="1"> Open tracking</label>
<label><input type="checkbox" name="dsn_notify_success" value="1"> DSN success opt-in</label>
<label><input type="checkbox" name="newsletter_mode" value="1"> Newsletter mode</label>
<label><input type="checkbox" name="one_click_unsubscribe" value="1"> One-click unsubscribe</label>
</div>
<div class="v951-formgrid"><label>Unsubscribe URL <input name="unsubscribe_url" placeholder="https://example.com/unsubscribe"></label><label>Unsubscribe email <input name="unsubscribe_email" type="email" placeholder="unsubscribe@example.com"></label></div>
<div class="notice">Recipient authorization, suppression, retry/backoff, DSN, tracking and newsletter semantics remain owned by the existing backend.</div>
<button type="submit">Send using existing Postmaster send</button>
</form>
</section>'''


def render_tracking_summary(base: Any, core: Any, request: Request) -> str:
    window = v951._window_for(request, "tracking")
    accounts = _account_rows(base)
    requested = (request.query_params.get("account") or request.query_params.get("account_id") or "").strip()
    valid = {_account_id(row) for row in accounts}
    selected = requested if requested in valid else None
    rows = v951._tracking_rows(base, core, selected, window)
    link_events = [row for row in rows["links"] if str(row.get("event_type") or "") == "link"]
    account_cards = ""
    if request.query_params.get("ui_view") == "tracking":
        colors = account_color_map(accounts)
        cards = []
        for account in accounts:
            account_id = _account_id(account)
            per_account = v951._tracking_rows(base, core, account_id, window)
            per_links = [row for row in per_account["links"] if str(row.get("event_type") or "") == "link"]
            color = colors.get(account_id, _ACCOUNT_PALETTE[0])
            cards.append(
                f'<section class="card v953-account-card" style="--account-color:{escape(color, quote=True)}">'
                f'{_account_chip(account, color)}'
                f'<div class="v951-kv"><span>Deliveries</span><b>{len(per_account["deliveries"])}</b></div>'
                f'<div class="v951-kv"><span>Open events</span><b>{len(per_account["opens"])}</b></div>'
                f'<div class="v951-kv"><span>Link events</span><b>{len(per_links)}</b></div>'
                '</section>'
            )
        account_cards = '<div class="v953-account-grid">' + "".join(cards) + '</div>' if cards else ""
    return (
        '<div class="v951-pagehead"><div><h2>Tracking activity</h2>'
        '<p>Observed events filtered by persisted timestamps; account identity remains visible alongside stable color cues.</p></div>'
        + v951.range_bar(request, "tracking", "tracking") + '</div>'
        '<form method="get" action="/" class="v951-toolbar"><input type="hidden" name="ui_view" value="tracking">'
        f'<label>Account {_account_select(accounts, selected, name="account", include_all=True)}</label><button type="submit">Filter</button></form>'
        + account_cards
        + '<div class="v951-metrics">'
        + v951._metric("Campaigns", len(rows["campaigns"]), v951.WINDOW_LABELS[window])
        + v951._metric("Deliveries", len(rows["deliveries"]), v951.WINDOW_LABELS[window])
        + v951._metric("Open events", len(rows["opens"]), "observed telemetry")
        + v951._metric("Link events", len(link_events), "observed telemetry")
        + '</div>'
        '<div class="notice">Open telemetry is not proof of human reading. Provider/scanner classification is query-time interpretation. '
        'Unique click remains <code>delivery_id + link_id + client_fingerprint</code>; tracking alone does not imply newsletter.</div>'
    )


def _system_metric(label: str, value: Any, note: str = "") -> str:
    return v951._metric(label, value if value not in {None, ""} else "—", note)


def render_system(base: Any, request: Request) -> str:
    build = v951._safe_call(base, base.build_status)
    build = build if isinstance(build, dict) else {}
    current_tag = ""
    try:
        current_tag = runtime_control.current_release_tag(str(build.get("version") or ""))
    except ValueError:
        pass
    versions: list[str] = []
    release_status = "not checked"
    if request.query_params.get("ui_view") == "system":
        versions, release_status = runtime_control.stable_release_tags()
    if current_tag and current_tag not in versions:
        versions.append(current_tag)
        versions.sort(key=lambda value: runtime_control.semver_tuple(value) or (0, 0, 0), reverse=True)
    selected_version = current_tag or (versions[0] if versions else "")
    version_options = "".join(
        f'<option value="{escape(tag, quote=True)}"{" selected" if tag == selected_version else ""}>{escape(tag)}</option>'
        for tag in versions
    ) or '<option value="">Stable release list unavailable</option>'
    csrf = escape(str(base._csrf_value()), quote=True)
    flash = escape(request.query_params.get("system_result") or "")
    banner = f'<div class="flash">{flash}</div>' if flash else ""
    status_note = "stable release list: " + release_status
    primary = (
        '<div class="v951-metrics">'
        + _system_metric("Running version", build.get("version"), "application release")
        + _system_metric("Concrete build/ref", build.get("build"), "resolved source")
        + _system_metric("Requested selector", build.get("requested_version"), "effective bootstrap selector")
        + _system_metric("Latest stable", build.get("latest_version"), status_note)
        + _system_metric("Update available", build.get("update_available"), "runtime check")
        + _system_metric("Update check", build.get("update_check_status"), "status")
        + '</div>'
    )
    tracking = v951._safe_call(base, base.tracking_status)
    knowledge = v951._safe_call(base, base.context_engine().status)
    files = v951._safe_call(base, base.file_store().status)
    scheduler = v951._safe_call(base, base.scheduler().status)
    health_cards = ''.join(
        f'<section class="card"><h3>{escape(label)}</h3>{v951._status_rows(value)}</section>'
        for label, value in (
            ("Tracking", tracking), ("Knowledge", knowledge), ("File Store", files), ("Scheduler", scheduler)
        )
    )
    advanced = v951._details({
        "build": build,
        "tracking": tracking,
        "knowledge": knowledge,
        "file_store": files,
        "scheduler": scheduler,
        "runtime_control": runtime_control.read_control(),
    })
    return f'''
<section class="tab-panel" id="panel-system" data-panel="system">
<div class="v951-pagehead"><div><h2>System</h2><p>Operator runtime status and safe restart/version controls over the existing bootstrap architecture.</p></div></div>{banner}
{primary}
<div class="v951-grid">{health_cards}</div>
<div class="v953-system-actions">
<section class="card"><h3>Restart current version</h3><p class="small muted">Restart the concrete version that is running now, without using Docker or Portainer credentials.</p><form method="post" action="/dashboard/system/runtime"><input type="hidden" name="csrf" value="{csrf}"><input type="hidden" name="action" value="restart-current"><button type="submit"{' disabled' if not current_tag else ''}>Restart current</button></form></section>
<section class="card"><h3>Update to latest stable</h3><p class="small muted">Request the highest stable application SemVer release, force one update check, then restart.</p><form method="post" action="/dashboard/system/runtime"><input type="hidden" name="csrf" value="{csrf}"><input type="hidden" name="action" value="update-latest"><button type="submit">Update to latest + restart</button></form></section>
<section class="card v953-warning"><h3>Select stable version</h3><p class="small muted">Only stable application vX.Y.Z releases are listed. Downgrades can be unsafe when data migrations are not backward-compatible.</p><form method="post" action="/dashboard/system/runtime" class="v953-inline"><input type="hidden" name="csrf" value="{csrf}"><input type="hidden" name="action" value="switch-version"><label>Version <select name="version">{version_options}</select></label><label><span>Downgrade confirmation</span><span><input type="checkbox" name="confirm_downgrade" value="yes"> I understand the downgrade risk</span></label><button type="submit"{' disabled' if not versions else ''}>Switch + restart</button></form></section>
</div>
<details class="v951-details"><summary>Module/configuration controls</summary><p class="small muted">No capability flag is presented as a toggle here unless it has an existing safe persistent configuration path. Current structural capability flags remain informational.</p></details>
<details class="v951-details"><summary>Advanced diagnostics</summary>{advanced}</details>
</section>'''


def _nav_html() -> str:
    def link(view: str, label: str, icon: str = "") -> str:
        return f'<a class="tab-link" href="#{view}" data-tab="{view}"><span class="v951-ico">{escape(icon)}</span>{escape(label)}</a>'
    return (
        '<nav class="tabs v951-nav" aria-label="Dashboard sections">'
        '<div class="v951-brand"><strong>Postmaster</strong><small>Operator console</small></div>'
        '<div class="v951-nav-label">Operate</div>'
        + link("overview", "Dashboard", "⌂") + link("accounts", "Accounts", "◎") + link("mail-health", "Mail Health", "♡")
        + link("inbox", "Inbox", "▱") + link("compose", "Compose", "↗") + link("tracking", "Tracking", "⌾")
        + link("deliveries", "Deliveries", "➤") + link("suppressions", "Suppressions", "⊘")
        + '<div class="v951-nav-label">Organize</div>'
        + link("projects", "Projects", "◫") + link("scheduler", "Tasks", "☑") + link("knowledge", "Knowledge", "▤") + link("files", "Files", "▣")
        + '<div class="v951-nav-label">Control</div>'
        + link("security", "Security", "⌑") + link("amp", "AMP", "⚡") + link("system", "System", "⚙") + link("coverage", "MCP Coverage", "90")
        + '<div class="v951-legacy-links"><a href="#domains" data-tab="domains">Domain controls</a><a href="#recipients" data-tab="recipients">Recipient controls</a></div>'
        + '</nav>'
    )


def augment_dashboard(body: str, base: Any, core: Any, request: Request) -> str:
    body = v952.augment_dashboard(body, base, core, request)
    body = body.replace("v9.4.1 query-time heuristic", "query-time heuristic")
    body = body.replace("v9.4 click analytics", "click analytics")
    if "webgui-v953-admin-ux" not in body and "</style>" in body:
        body = body.replace("</style>", STYLE + "\n</style>", 1)
    return body


async def system_runtime_action(
    base: Any,
    request: Request,
    restart_callback: Callable[[], None],
):
    form, error = await base._verified_form(request)
    if error:
        return error
    action = str(form.get("action") or "").strip()
    status = v951._safe_call(base, base.build_status)
    status = status if isinstance(status, dict) else {}
    try:
        current_tag = runtime_control.current_release_tag(str(status.get("version") or ""))
    except ValueError:
        return RedirectResponse("/?" + urlencode({"ui_view": "system", "system_result": "Running version is not a stable SemVer release"}) + "#system", status_code=303)
    try:
        if action == "restart-current":
            existing = runtime_control.read_control()
            requested = str(existing.get("selector") or status.get("requested_version") or "latest")
            try:
                selector = runtime_control.canonical_selector(requested)
            except ValueError:
                selector = current_tag
            runtime_control.write_control(selector=selector, restart_ref_once=current_tag)
            message = f"Restart requested for {current_tag}"
        elif action == "update-latest":
            runtime_control.write_control(selector="latest", check_updates_once=True)
            message = "Latest stable update requested"
        elif action == "switch-version":
            selected = runtime_control.canonical_selector(str(form.get("version") or ""))
            versions, release_status = runtime_control.stable_release_tags(force=True)
            if release_status != "ok" or selected not in versions:
                raise ValueError("selected version is not a current stable application release")
            current_tuple = runtime_control.semver_tuple(current_tag)
            selected_tuple = runtime_control.semver_tuple(selected)
            if selected_tuple and current_tuple and selected_tuple < current_tuple and form.get("confirm_downgrade") != "yes":
                raise ValueError("downgrade requires explicit confirmation")
            runtime_control.write_control(selector=selected)
            message = f"Version switch requested: {selected}"
        else:
            raise ValueError("unknown runtime action")
    except (OSError, ValueError) as exc:
        return RedirectResponse("/?" + urlencode({"ui_view": "system", "system_result": f"Request rejected: {exc}"}) + "#system", status_code=303)
    return RedirectResponse(
        "/?" + urlencode({"ui_view": "system", "system_result": message}) + "#system",
        status_code=303,
        background=BackgroundTask(restart_callback),
    )


def install_webgui_v953(
    app: Any,
    base: Any,
    core: Any,
    legacy_dashboard: Any,
    *,
    restart_callback: Callable[[], None] | None = None,
) -> Any:
    v951._nav_html = _nav_html
    v951.render_mail_health = render_mail_health
    v951.render_inbox = render_inbox
    v951.render_compose = render_compose
    v951.render_tracking_summary = render_tracking_summary
    v951.render_system = render_system
    v951.augment_dashboard = augment_dashboard

    callback = restart_callback or runtime_control.terminate_current_process
    routes = app.router.routes
    routes[:] = [
        route for route in routes
        if not (isinstance(route, Route) and route.path == "/dashboard/system/runtime")
    ]
    mount_index = next((i for i, route in enumerate(routes) if isinstance(route, Mount)), len(routes))

    async def runtime_route(request: Request):
        if request.method != "POST":
            return PlainTextResponse("Method Not Allowed", status_code=405, headers={"Allow": "POST"})
        return await system_runtime_action(base, request, callback)

    routes.insert(mount_index, Route("/dashboard/system/runtime", runtime_route, methods=["GET", "POST"]))
    return legacy_dashboard
