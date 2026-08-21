from __future__ import annotations

from html import escape
from typing import Any
from urllib.parse import urlencode

from starlette.requests import Request
from starlette.responses import RedirectResponse
from starlette.routing import Mount, Route

from . import webgui_v951 as v951


PROJECT_SCOPED_VIEWS = {"projects", "scheduler", "knowledge", "files"}
KNOWN_VIEWS = {
    "overview", "accounts", "mail-health", "inbox", "compose", "tracking",
    "deliveries", "suppressions", "domains", "recipients", "projects",
    "knowledge", "files", "scheduler", "security", "amp", "system", "coverage",
}


def _clean_params(request: Request, *, view: str) -> dict[str, str]:
    params = {
        key: value
        for key, value in request.query_params.items()
        if key != "flash" and str(value).strip()
    }
    if view not in PROJECT_SCOPED_VIEWS:
        params.pop("project", None)
    params["ui_view"] = view
    return params


def dashboard_url(
    request: Request,
    *,
    view: str,
    updates: dict[str, str | None] | None = None,
) -> str:
    params = _clean_params(request, view=view)
    for key, value in (updates or {}).items():
        text = "" if value is None else str(value).strip()
        if not text:
            params.pop(key, None)
        else:
            params[key] = text
    query = urlencode(params)
    return "/" + (f"?{query}" if query else "") + f"#{view}"


def _canonical_inbox_params(request: Request) -> dict[str, str]:
    account_id = (
        request.query_params.get("account_id")
        or request.query_params.get("account")
        or ""
    ).strip()
    mailbox = (request.query_params.get("mailbox") or "INBOX").strip() or "INBOX"
    subject = (
        request.query_params.get("subject")
        or request.query_params.get("mail_subject")
        or ""
    ).strip()
    text = (
        request.query_params.get("text")
        or request.query_params.get("mail_text")
        or ""
    ).strip()
    since_days = str(v951._bounded_int(request.query_params.get("since_days"), 90, 1, 3650))
    params = {
        "ui_view": "inbox",
        "inbox_search": "1",
        "mailbox": mailbox,
        "since_days": since_days,
    }
    if account_id:
        params["account_id"] = account_id
    if subject:
        params["subject"] = subject
    if text:
        params["text"] = text
    if request.query_params.get("unread_only") == "1":
        params["unread_only"] = "1"
    uid = (request.query_params.get("message_uid") or "").strip()
    if uid:
        params["message_uid"] = uid
    return params


def _inbox_url(params: dict[str, str], *, uid: str | None = None) -> str:
    values = {key: value for key, value in params.items() if str(value).strip()}
    if uid:
        values["message_uid"] = str(uid)
    else:
        values.pop("message_uid", None)
    return "/?" + urlencode(values) + "#inbox"


def render_mail_health(base: Any, request: Request) -> str:
    account_id = (
        request.query_params.get("account_id")
        or request.query_params.get("account")
        or ""
    ).strip() or None
    run = request.query_params.get("health_snapshot") == "1"
    result: Any = None
    if run:
        selector = (request.query_params.get("dkim_selector") or "").strip() or None
        # A POST refresh redirects here with health_refreshed=1. The diagnostic was already
        # refreshed by that POST, so this read reuses the existing diagnostic path without
        # introducing another state-changing HTTP action.
        result = v951._safe_call(
            base,
            base.test_email_account,
            account_id=account_id,
            refresh=False,
            dkim_selector=selector,
        )
    csrf = escape(str(base._csrf_value()), quote=True)
    account = escape(account_id or "", quote=True)
    selector = escape(request.query_params.get("dkim_selector") or "", quote=True)
    cards = ""
    if isinstance(result, dict):
        for key in ("smtp", "imap", "dns", "tls"):
            if key in result:
                cards += f'<section class="card"><h3>{escape(key.upper())}</h3>{v951._status_rows(result.get(key))}</section>'
        reliability = {key: result.get(key) for key in ("ok", "errors", "warnings") if key in result}
        cards += f'<section class="card"><h3>Reliability</h3>{v951._status_rows(reliability)}</section>'
    else:
        cards = '<section class="card"><p class="muted">Run a current snapshot or force-refresh the existing diagnostics. No historical series is fabricated.</p></section>'
    raw = v951._details(result) if result is not None else ""
    refreshed = (
        '<div class="flash">Mail Health diagnostics refreshed.</div>'
        if request.query_params.get("health_refreshed") == "1"
        else ""
    )
    snapshot_params = {"ui_view": "mail-health", "health_snapshot": "1"}
    if account_id:
        snapshot_params["account_id"] = account_id
    if request.query_params.get("dkim_selector"):
        snapshot_params["dkim_selector"] = request.query_params.get("dkim_selector") or ""
    snapshot_action = "/?" + urlencode(snapshot_params) + "#mail-health"
    return f'''
<section class="tab-panel" id="panel-mail-health" data-panel="mail-health">
<div class="v951-pagehead"><div><h2>Mail Health</h2><p>Structured current diagnostics. Capability absence is an observation, not automatically a failure.</p></div></div>
{refreshed}
<form method="get" action="/" class="v951-toolbar">
<input type="hidden" name="ui_view" value="mail-health"><input type="hidden" name="health_snapshot" value="1">
<label>Account ID <input name="account_id" value="{account}" placeholder="default"></label>
<label>DKIM selector (optional) <input name="dkim_selector" value="{selector}" placeholder="selector"></label>
<button type="submit">Read current snapshot</button>
</form>
<div class="v951-grid">{cards}</div>{raw}
<div class="notice"><strong>Truthful health semantics:</strong> no DKIM selector is not a DKIM failure; optional standards may be absent without making Postmaster unhealthy; pure TLS-handshake latency is not invented.</div>
<details class="v951-details"><summary>Force-refresh diagnostics</summary>
<form method="post" action="/dashboard/mail-health/refresh">
<input type="hidden" name="csrf" value="{csrf}"><input type="hidden" name="account_id" value="{account}"><input type="hidden" name="dkim_selector" value="{selector}"><button type="submit">Refresh diagnostics</button>
</form>
<p class="small muted">Refresh remains an authenticated POST with CSRF verification and returns to this Mail Health view.</p>
</details>
<noscript><p><a href="{escape(snapshot_action, quote=True)}">Open current Mail Health snapshot</a></p></noscript>
</section>'''


def render_inbox(base: Any, request: Request) -> str:
    params = _canonical_inbox_params(request)
    account_id = params.get("account_id") or None
    mailbox = params["mailbox"]
    search_requested = request.query_params.get("inbox_search") == "1"
    uid = params.get("message_uid", "")
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
    if uid:
        detail = v951._safe_call(
            base,
            base.get_email,
            mailbox=mailbox,
            uid=uid,
            account_id=account_id,
        )
    rows = []
    for row in results:
        row_uid = str(row.get("uid") or row.get("id") or "").strip()
        if not row_uid:
            continue
        href = _inbox_url(params, uid=row_uid)
        rows.append(
            '<tr>'
            f'<td><code>{escape(row_uid)}</code></td>'
            f'<td>{escape(str(row.get("from") or row.get("from_address") or ""))}</td>'
            f'<td>{escape(str(row.get("subject") or ""))}</td>'
            f'<td>{escape(str(row.get("date") or row.get("received_at") or ""))}</td>'
            f'<td><a href="{escape(href, quote=True)}">View</a></td>'
            '</tr>'
        )
    if search_requested:
        empty = '<tr><td colspan="5" class="muted">No messages matched this search.</td></tr>'
    else:
        empty = '<tr><td colspan="5" class="muted">Run a search to load Inbox results.</td></tr>'
    table = (
        '<div class="scroll"><table><thead><tr><th>UID</th><th>From</th><th>Subject</th><th>Date</th><th></th></tr></thead><tbody>'
        + ("".join(rows) or empty)
        + '</tbody></table></div>'
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
                diagnostics.append(
                    f'<section class="card"><h3>{escape(key.replace("_", " ").title())}</h3>{v951._status_rows(detail.get(key))}</section>'
                )
        back = _inbox_url(params)
        detail_html = (
            f'<p><a href="{escape(back, quote=True)}">← Back to results</a></p>'
            f'<div class="v951-pagehead"><div><h3>{subject}</h3><p>Mailbox {escape(mailbox)} · UID {escape(uid)}</p></div></div>'
            f'<section class="card"><pre class="v951-message">{escape(text[:20000])}</pre></section>'
            f'<div class="v951-grid'>{"".join(diagnostics)}</div>{v951._details(detail)}'
        )
    checked = " checked" if params.get("unread_only") == "1" else ""
    return f'''
<section class="tab-panel" id="panel-inbox" data-panel="inbox">
<div class="v951-pagehead"><div><h2>Inbox</h2><p>Mailbox search and message diagnostics use the existing IMAP read path; no parser semantics are changed.</p></div></div>
<form method="get" action="/dashboard/inbox/search" class="v951-toolbar">
<label>Account <input name="account_id" value="{escape(account_id or "", quote=True)}" placeholder="default"></label>
<label>Mailbox <input name="mailbox" value="{escape(mailbox, quote=True)}"></label>
<label>Subject <input name="subject" value="{escape(params.get("subject", ""), quote=True)}"></label>
<label>Text <input name="text" value="{escape(params.get("text", ""), quote=True)}"></label>
<label>Since days <input type="number" min="1" max="3650" name="since_days" value="{escape(params["since_days"], quote=True)}"></label>
<label><input type="checkbox" name="unread_only" value="1"{checked}> unread only</label>
<button type="submit">Search</button>
</form>{table}{detail_html}
</section>'''


def _rewrite_nav(body: str, request: Request) -> str:
    for view in KNOWN_VIEWS:
        old = f'href="#{view}" data-tab="{view}"'
        if old not in body:
            continue
        target = dashboard_url(request, view=view)
        body = body.replace(old, f'href="{escape(target, quote=True)}" data-tab="{view}"')
    return body


def _inject_project_filter_views(body: str) -> str:
    for view in PROJECT_SCOPED_VIEWS:
        panel = f'id="panel-{view}" data-panel="{view}"'
        panel_start = body.find(panel)
        if panel_start < 0:
            continue
        form_start = body.find('<form class="project-filter" method="get" action="/">', panel_start)
        next_panel = body.find('<section class="tab-panel"', panel_start + len(panel))
        if form_start < 0 or (next_panel >= 0 and form_start > next_panel):
            continue
        marker = '<form class="project-filter" method="get" action="/">'
        replacement = marker + f'\n<input type="hidden" name="ui_view" value="{view}">'
        body = body[:form_start] + body[form_start:].replace(marker, replacement, 1)
    return body


def augment_dashboard(body: str, base: Any, core: Any, request: Request) -> str:
    original = _ORIGINAL_AUGMENT
    body = original(body, base, core, request)
    body = _rewrite_nav(body, request)
    body = _inject_project_filter_views(body)
    return body


_ORIGINAL_AUGMENT = v951.augment_dashboard


async def mail_health_refresh(base: Any, request: Request):
    form, error = await base._verified_form(request)
    if error:
        return error
    account_id = str(form.get("account_id") or "").strip() or None
    selector = str(form.get("dkim_selector") or "").strip() or None
    v951._safe_call(
        base,
        base.test_email_account,
        account_id=account_id,
        refresh=True,
        dkim_selector=selector,
    )
    params = {
        "ui_view": "mail-health",
        "health_snapshot": "1",
        "health_refreshed": "1",
    }
    if account_id:
        params["account_id"] = account_id
    if selector:
        params["dkim_selector"] = selector
    return RedirectResponse("/?" + urlencode(params) + "#mail-health", status_code=303)


async def mail_health_get_redirect(request: Request):
    params = {"ui_view": "mail-health"}
    account_id = (
        request.query_params.get("account_id")
        or request.query_params.get("account")
        or ""
    ).strip()
    selector = (request.query_params.get("dkim_selector") or "").strip()
    if account_id:
        params["account_id"] = account_id
    if selector:
        params["dkim_selector"] = selector
    return RedirectResponse("/?" + urlencode(params) + "#mail-health", status_code=303)


async def inbox_search_redirect(request: Request):
    params = _canonical_inbox_params(request)
    return RedirectResponse(_inbox_url(params), status_code=303)


def install_webgui_v952(app: Any, base: Any, legacy_dashboard: Any) -> Any:
    # v9.5.1's installed dashboard closure resolves these module globals at request time,
    # allowing this patch layer to correct browser composition without touching mail backend.
    v951._url = dashboard_url
    v951.render_mail_health = render_mail_health
    v951.render_inbox = render_inbox
    v951.augment_dashboard = augment_dashboard

    async def dashboard_home(request: Request):
        # Browsers can submit a blank project selection as ?project=. Canonicalize it once
        # instead of allowing hash-only navigation to preserve a semantically empty filter.
        if "project" in request.query_params and not (request.query_params.get("project") or "").strip():
            view = (request.query_params.get("ui_view") or "overview").strip()
            if view not in KNOWN_VIEWS:
                view = "overview"
            return RedirectResponse(dashboard_url(request, view=view, updates={"project": None}), status_code=303)
        return await legacy_dashboard(request)

    routes = app.router.routes
    for index, route in enumerate(list(routes)):
        if isinstance(route, Route) and route.path == "/":
            routes[index] = Route("/", dashboard_home, methods=["GET"])
            break

    # The v9.5.0 refresh route was appended after the catch-all MCP Mount and therefore
    # could be shadowed. Remove all copies and re-register the browser routes before Mount.
    routes[:] = [
        route for route in routes
        if not (
            isinstance(route, Route)
            and route.path in {"/dashboard/mail-health/refresh", "/dashboard/inbox/search"}
        )
    ]
    mount_index = next((i for i, route in enumerate(routes) if isinstance(route, Mount)), len(routes))
    async def refresh_route(request: Request):
        return await mail_health_refresh(base, request)

    browser_routes = [
        Route(
            "/dashboard/mail-health/refresh",
            refresh_route,
            methods=["POST"],
        ),
        Route(
            "/dashboard/mail-health/refresh",
            mail_health_get_redirect,
            methods=["GET"],
        ),
        Route(
            "/dashboard/inbox/search",
            inbox_search_redirect,
            methods=["GET"],
        ),
    ]
    for offset, route in enumerate(browser_routes):
        routes.insert(mount_index + offset, route)
    return dashboard_home
