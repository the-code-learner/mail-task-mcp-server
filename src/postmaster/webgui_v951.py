from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from html import escape
from typing import Any, Iterable
from urllib.parse import urlencode

from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse
from starlette.routing import Mount, Route


WINDOWS: dict[str, timedelta | None] = {
    "1d": timedelta(days=1),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
    "90d": timedelta(days=90),
    "all": None,
}
WINDOW_LABELS = {"1d": "1D", "7d": "7D", "30d": "30D", "90d": "90D", "all": "All time"}

MCP_COVERAGE: dict[str, tuple[str, ...]] = {
    "Accounts / mail": (
        "list_email_accounts", "test_email_account", "mailbox_status", "list_mailboxes",
        "search_emails", "get_email", "list_known_contacts", "list_email_attachments",
        "get_email_attachment", "read_email_attachment", "move_email", "mark_not_spam",
        "mark_as_spam", "set_email_seen",
    ),
    "Compose": (
        "send_email", "reply_email", "follow_up_email", "create_draft",
        "create_reply_draft", "create_follow_up_draft",
    ),
    "Tracking / reliability": (
        "tracking_status", "list_tracking_campaigns", "get_tracking_campaign",
        "list_tracking_deliveries", "list_open_events", "get_tracking_summary",
        "list_tracking_links", "list_tracking_events",
    ),
    "Security / policy": (
        "email_security_status", "recipient_authorization_status", "list_authorized_recipients",
        "list_authorized_domains", "authorize_domain", "revoke_domain",
        "authorize_recipient", "revoke_recipient",
    ),
    "AMP": ("amp_account_status", "set_amp_account_state", "validate_amp_email"),
    "Knowledge": (
        "knowledge_status", "create_memory", "get_memory", "update_memory", "delete_memory",
        "list_memories", "create_skill", "get_skill", "update_skill", "delete_skill",
        "list_skills", "search_knowledge", "get_project_context", "get_knowledge_history",
        "restore_knowledge_revision", "get_knowledge_audit", "export_knowledge",
        "import_knowledge", "reindex_knowledge",
    ),
    "Files": (
        "file_store_status", "save_file", "save_uploaded_file", "save_uploaded_files",
        "save_text_file", "list_files", "get_file_info", "read_text_file", "get_file_base64",
        "update_file_metadata", "delete_stored_file", "get_stored_file_resource",
    ),
    "Scheduler": (
        "scheduler_status", "create_owner", "list_owners", "create_project", "list_projects",
        "create_execution_profile", "list_execution_profiles", "preview_schedule", "create_job",
        "list_jobs", "list_due_jobs", "get_job", "update_job", "pause_job", "resume_job",
        "approve_job", "complete_job", "delete_job", "get_job_history",
    ),
    "System": ("build_status",),
}


def parse_window(value: str | None, default: str = "30d") -> str:
    key = str(value or "").strip().lower()
    return key if key in WINDOWS else default


def window_cutoff(window: str, *, now: datetime | None = None) -> datetime | None:
    delta = WINDOWS[parse_window(window)]
    if delta is None:
        return None
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc) - delta


def parse_timestamp(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def filter_chronological_rows(
    rows: Iterable[dict[str, Any]],
    window: str,
    timestamp_fields: Iterable[str],
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    cutoff = window_cutoff(window, now=now)
    values = list(rows)
    if cutoff is None:
        return values
    result: list[dict[str, Any]] = []
    fields = tuple(timestamp_fields)
    for row in values:
        stamp = next((parse_timestamp(row.get(field)) for field in fields if parse_timestamp(row.get(field))), None)
        if stamp is not None and stamp >= cutoff:
            result.append(row)
    return result


def _safe_call(base: Any, fn: Any, *args: Any, **kwargs: Any) -> Any:
    try:
        return base._safe_call(fn, *args, **kwargs)
    except Exception:
        return {"ok": False, "error": "unavailable"}


def _list_result(value: Any, *keys: str) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [row for row in value if isinstance(row, dict)]
    if isinstance(value, dict):
        for key in keys:
            rows = value.get(key)
            if isinstance(rows, list):
                return [row for row in rows if isinstance(row, dict)]
    return []


def _selected_account(request: Request) -> str | None:
    return (request.query_params.get("account") or "").strip() or None


def _bounded_int(value: Any, default: int, low: int, high: int) -> int:
    try:
        parsed = int(str(value or default))
    except (TypeError, ValueError):
        parsed = default
    return max(low, min(parsed, high))


def _window_for(request: Request, scope: str) -> str:
    return parse_window(request.query_params.get(f"{scope}_window"))


def _url(request: Request, *, view: str, updates: dict[str, str | None] | None = None) -> str:
    params = {key: value for key, value in request.query_params.items() if key != "flash"}
    params["ui_view"] = view
    for key, value in (updates or {}).items():
        if value in {None, ""}:
            params.pop(key, None)
        else:
            params[key] = str(value)
    query = urlencode(params)
    return "/" + (f"?{query}" if query else "") + f"#{view}"


def range_bar(request: Request, scope: str, view: str) -> str:
    selected = _window_for(request, scope)
    links = []
    for key, label in WINDOW_LABELS.items():
        active = " active" if key == selected else ""
        href = _url(request, view=view, updates={f"{scope}_window": key})
        links.append(f'<a class="v951-range{active}" href="{escape(href, quote=True)}">{label}</a>')
    return '<div class="v951-rangebar" aria-label="Historical range">' + "".join(links) + "</div>"


def _metric(label: str, value: Any, note: str = "") -> str:
    return (
        '<div class="v951-metric">'
        f'<span>{escape(label)}</span><strong>{escape(str(value))}</strong>'
        f'<small>{escape(note)}</small>'
        '</div>'
    )


def _details(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, indent=2, default=str)
    return (
        '<details class="v951-details"><summary>Raw / Details</summary>'
        f'<pre>{escape(raw)}</pre></details>'
    )


def _status_rows(value: Any, *, depth: int = 0) -> str:
    if not isinstance(value, dict):
        return f'<p class="small muted">{escape(str(value or "Unavailable"))}</p>'
    rows: list[str] = []
    for key, item in value.items():
        if key in {"password", "token", "tracking_token", "amp_token"}:
            continue
        if isinstance(item, (dict, list)):
            continue
        text = str(item)
        if len(text) > 180:
            text = text[:177] + "..."
        rows.append(
            '<div class="v951-kv">'
            f'<span>{escape(str(key).replace("_", " ").title())}</span><b>{escape(text)}</b>'
            '</div>'
        )
    return "".join(rows) or '<p class="small muted">No scalar diagnostics available.</p>'


def _tracking_rows(base: Any, core: Any, account_id: str | None, window: str) -> dict[str, list[dict[str, Any]]]:
    analytics = base.analytics_store()
    campaigns = _list_result(_safe_call(base, analytics.list_campaigns, account_id=account_id, limit=500), "campaigns")
    deliveries = _list_result(_safe_call(base, analytics.list_deliveries, account_id=account_id, limit=1000), "deliveries")
    opens = _list_result(_safe_call(base, analytics.list_open_events, account_id=account_id, limit=1000), "events", "opens")
    try:
        link_events = _list_result(core.link_store().unified_events(account_id=account_id, limit=1000), "events")
    except Exception:
        link_events = []
    return {
        "campaigns": filter_chronological_rows(campaigns, window, ("created_at",)),
        "deliveries": filter_chronological_rows(deliveries, window, ("sent_at", "created_at")),
        "opens": filter_chronological_rows(opens, window, ("opened_at", "observed_at")),
        "links": filter_chronological_rows(link_events, window, ("observed_at", "opened_at")),
        "all_deliveries": deliveries,
    }


def render_overview(base: Any, core: Any, request: Request) -> str:
    window = _window_for(request, "dashboard")
    account_id = _selected_account(request)
    rows = _tracking_rows(base, core, account_id, window)
    accounts = base.account_store().list_accounts()
    jobs = _list_result(_safe_call(base, base.scheduler().list_jobs, limit=1000), "jobs")
    files_status = _safe_call(base, base.file_store().status)
    knowledge_status = _safe_call(base, base.context_engine().status)
    status = _safe_call(base, base.build_status)
    file_count = files_status.get("files", 0) if isinstance(files_status, dict) else 0
    embedded = knowledge_status.get("embedded_chunks", knowledge_status.get("chunks", "—")) if isinstance(knowledge_status, dict) else "—"
    content = (
        '<div class="v951-pagehead"><div><h2>Operations Dashboard</h2>'
        '<p>Chronological activity is range-filtered; inventories and runtime state remain point-in-time.</p></div>'
        + range_bar(request, "dashboard", "overview") + '</div>'
        '<div class="v951-metrics">'
        + _metric("Runtime", status.get("version", "unknown") if isinstance(status, dict) else "unknown", "snapshot")
        + _metric("Accounts", len(accounts), "current inventory")
        + _metric("Deliveries", len(rows["deliveries"]), WINDOW_LABELS[window])
        + _metric("Open events", len(rows["opens"]), "telemetry")
        + _metric("Link events", sum(1 for row in rows["links"] if str(row.get("event_type") or "") == "link"), "observed")
        + _metric("Tasks", len(jobs), "registry snapshot")
        + _metric("Files", file_count, "current inventory")
        + _metric("Knowledge chunks", embedded, "current inventory")
        + '</div>'
        '<div class="notice"><strong>Semantic guardrails:</strong> tracking alone does not imply newsletter. '
        'Open/pixel telemetry is observed activity, not proof of human reading.</div>'
    )
    return content


def render_tracking_summary(base: Any, core: Any, request: Request) -> str:
    window = _window_for(request, "tracking")
    rows = _tracking_rows(base, core, _selected_account(request), window)
    link_events = [row for row in rows["links"] if str(row.get("event_type") or "") == "link"]
    return (
        '<div class="v951-pagehead"><div><h2>Tracking activity</h2>'
        '<p>Observed events filtered by real persisted timestamps; raw events remain authoritative.</p></div>'
        + range_bar(request, "tracking", "tracking") + '</div>'
        '<div class="v951-metrics">'
        + _metric("Campaigns", len(rows["campaigns"]), WINDOW_LABELS[window])
        + _metric("Deliveries", len(rows["deliveries"]), WINDOW_LABELS[window])
        + _metric("Open events", len(rows["opens"]), "observed telemetry")
        + _metric("Link events", len(link_events), "observed telemetry")
        + '</div>'
        '<div class="notice">Open telemetry is not proof of human reading. Provider/scanner classification is query-time interpretation. '
        'Unique click remains <code>delivery_id + link_id + client_fingerprint</code>; tracking alone does not imply newsletter.</div>'
    )


def render_mail_health(base: Any, request: Request) -> str:
    account_id = _selected_account(request)
    run = request.query_params.get("health_snapshot") == "1"
    result: Any = None
    if run:
        selector = (request.query_params.get("dkim_selector") or "").strip() or None
        result = _safe_call(base, base.test_email_account, account_id=account_id, refresh=False, dkim_selector=selector)
    csrf = escape(str(base._csrf_value()), quote=True)
    account = escape(account_id or "", quote=True)
    selector = escape(request.query_params.get("dkim_selector") or "", quote=True)
    cards = ""
    if isinstance(result, dict):
        for key in ("smtp", "imap", "dns", "tls"):
            if key in result:
                cards += f'<section class="card"><h3>{escape(key.upper())}</h3>{_status_rows(result.get(key))}</section>'
        reliability = {key: result.get(key) for key in ("ok", "errors", "warnings") if key in result}
        cards += f'<section class="card"><h3>Reliability</h3>{_status_rows(reliability)}</section>'
    else:
        cards = '<section class="card"><p class="muted">Run a read-only snapshot to render current SMTP, IMAP, DNS and TLS diagnostics. No historical series is fabricated.</p></section>'
    raw = _details(result) if result is not None else ""
    return f'''
<section class="tab-panel" id="panel-mail-health" data-panel="mail-health">
<div class="v951-pagehead"><div><h2>Mail Health</h2><p>Structured current diagnostics. Capability absence is an observation, not automatically a failure.</p></div></div>
<form method="get" action="/" class="v951-toolbar">
<input type="hidden" name="ui_view" value="mail-health"><input type="hidden" name="health_snapshot" value="1">
<label>Account ID <input name="account" value="{account}" placeholder="default"></label>
<label>DKIM selector (optional) <input name="dkim_selector" value="{selector}" placeholder="selector"></label>
<button type="submit">Read current snapshot</button>
</form>
<div class="v951-grid">{cards}</div>{raw}
<div class="notice"><strong>Truthful health semantics:</strong> no DKIM selector is not a DKIM failure; optional standards may be absent without making Postmaster unhealthy; pure TLS-handshake latency is not invented.</div>
<details class="v951-details"><summary>Force-refresh troubleshooting route</summary>
<form method="post" action="/dashboard/mail-health/refresh"><input type="hidden" name="csrf" value="{csrf}"><input type="hidden" name="account_id" value="{account}"><input type="hidden" name="dkim_selector" value="{selector}"><button type="submit">Refresh and open raw diagnostics</button></form>
</details>
</section>'''


def render_inbox(base: Any, request: Request) -> str:
    account_id = _selected_account(request)
    mailbox = (request.query_params.get("mailbox") or "INBOX").strip() or "INBOX"
    search_requested = request.query_params.get("inbox_search") == "1"
    uid = (request.query_params.get("message_uid") or "").strip()
    results: list[dict[str, Any]] = []
    detail: Any = None
    if search_requested:
        result = _safe_call(
            base, base.search_emails,
            mailbox=mailbox,
            subject=(request.query_params.get("mail_subject") or "").strip() or None,
            text=(request.query_params.get("mail_text") or "").strip() or None,
            unread_only=request.query_params.get("unread_only") == "1",
            since_days=_bounded_int(request.query_params.get("since_days"), 90, 1, 3650),
            limit=50,
            account_id=account_id,
        )
        results = _list_result(result, "emails", "results", "messages")
    if uid:
        detail = _safe_call(base, base.get_email, mailbox=mailbox, uid=uid, account_id=account_id)
    rows = []
    for row in results:
        row_uid = str(row.get("uid") or row.get("id") or "")
        href = _url(request, view="inbox", updates={"message_uid": row_uid, "mailbox": mailbox})
        rows.append(
            '<tr>'
            f'<td><a href="{escape(href, quote=True)}"><code>{escape(row_uid)}</code></a></td>'
            f'<td>{escape(str(row.get("from") or row.get("from_address") or ""))}</td>'
            f'<td>{escape(str(row.get("subject") or ""))}</td>'
            f'<td>{escape(str(row.get("date") or row.get("received_at") or ""))}</td>'
            '</tr>'
        )
    table = (
        '<div class="scroll"><table><thead><tr><th>UID</th><th>From</th><th>Subject</th><th>Date</th></tr></thead><tbody>'
        + ("".join(rows) or '<tr><td colspan="4" class="muted">No search results loaded.</td></tr>')
        + '</tbody></table></div>'
    )
    detail_html = ""
    if isinstance(detail, dict):
        subject = escape(str(detail.get("subject") or "Message detail"))
        text = str(detail.get("body_text") or detail.get("text") or detail.get("body") or "")
        diagnostics = []
        for key in ("authentication_results", "mime_structure", "received_chain", "auto_reply", "list_headers", "spam_headers", "tracking", "delivery_state", "conversation_state"):
            if key in detail:
                diagnostics.append(f'<section class="card"><h3>{escape(key.replace("_", " ").title())}</h3>{_status_rows(detail.get(key))}</section>')
        detail_html = (
            f'<div class="v951-pagehead"><div><h3>{subject}</h3><p>Mailbox {escape(mailbox)} · UID {escape(uid)}</p></div></div>'
            f'<section class="card"><pre class="v951-message">{escape(text[:20000])}</pre></section>'
            f'<div class="v951-grid">{"".join(diagnostics)}</div>{_details(detail)}'
        )
    return f'''
<section class="tab-panel" id="panel-inbox" data-panel="inbox">
<div class="v951-pagehead"><div><h2>Inbox</h2><p>Mailbox search and message diagnostics use the existing IMAP read path; no parser semantics are changed.</p></div></div>
<form method="get" action="/" class="v951-toolbar">
<input type="hidden" name="ui_view" value="inbox"><input type="hidden" name="inbox_search" value="1">
<label>Account <input name="account" value="{escape(account_id or "", quote=True)}" placeholder="default"></label>
<label>Mailbox <input name="mailbox" value="{escape(mailbox, quote=True)}"></label>
<label>Subject <input name="mail_subject" value="{escape(request.query_params.get("mail_subject") or "", quote=True)}"></label>
<label>Text <input name="mail_text" value="{escape(request.query_params.get("mail_text") or "", quote=True)}"></label>
<label>Since days <input type="number" min="1" max="3650" name="since_days" value="{escape(request.query_params.get("since_days") or "90", quote=True)}"></label>
<label><input type="checkbox" name="unread_only" value="1"{' checked' if request.query_params.get('unread_only') == '1' else ''}> unread only</label>
<button type="submit">Search</button>
</form>{table}{detail_html}
</section>'''


def render_compose(base: Any, request: Request) -> str:
    csrf = escape(str(base._csrf_value()), quote=True)
    flash = escape(request.query_params.get("compose_result") or "")
    banner = f'<div class="flash">{flash}</div>' if flash else ""
    return f'''
<section class="tab-panel" id="panel-compose" data-panel="compose">
<div class="v951-pagehead"><div><h2>Compose</h2><p>Existing send semantics exposed with explicit newsletter and DSN controls. Tracking alone never enables unsubscribe headers.</p></div></div>{banner}
<form method="post" action="/dashboard/compose/send" class="card v951-compose">
<input type="hidden" name="csrf" value="{csrf}">
<div class="v951-formgrid">
<label>Account ID <input name="account_id" placeholder="default"></label>
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


def render_deliveries(base: Any, core: Any, request: Request) -> str:
    from .runtime_v950 import reliability_store

    account_id = _selected_account(request)
    window = _window_for(request, "deliveries")
    rows = _tracking_rows(base, core, account_id, window)
    attempts = filter_chronological_rows(reliability_store().list_attempts(limit=1000), window, ("started_at", "completed_at"))
    delivery_rows = []
    for row in rows["deliveries"][:250]:
        delivery_rows.append(
            '<tr>'
            f'<td><code>{escape(str(row.get("id") or ""))}</code></td>'
            f'<td>{escape(str(row.get("recipient") or ""))}</td>'
            f'<td>{escape(str(row.get("delivery_state") or "submitted"))}</td>'
            f'<td>{escape(str(row.get("attempt_count") or 0))}</td>'
            f'<td>{escape(str(row.get("bounce_classification") or "—"))}</td>'
            f'<td>{escape(str(row.get("conversation_state") or "—"))}</td>'
            f'<td>{escape(str(row.get("sent_at") or ""))}</td>'
            '</tr>'
        )
    attempt_rows = []
    for row in attempts[:200]:
        attempt_rows.append(
            '<tr>'
            f'<td>{escape(str(row.get("started_at") or ""))}</td>'
            f'<td><code>{escape(str(row.get("delivery_id") or ""))}</code></td>'
            f'<td>{escape(str(row.get("recipient") or ""))}</td>'
            f'<td>{escape(str(row.get("state") or ""))}</td>'
            f'<td>{escape(str(row.get("classification") or ""))}</td>'
            '</tr>'
        )
    return f'''
<section class="tab-panel" id="panel-deliveries" data-panel="deliveries">
<div class="v951-pagehead"><div><h2>Deliveries</h2><p>Real delivery rows and retry attempts filtered only by persisted timestamps.</p></div>{range_bar(request, 'deliveries', 'deliveries')}</div>
<div class="v951-metrics">{_metric('Deliveries in range', len(rows['deliveries']), WINDOW_LABELS[window])}{_metric('Attempts in range', len(attempts), WINDOW_LABELS[window])}{_metric('Current delivery inventory', len(rows['all_deliveries']), 'snapshot')}</div>
<div class="scroll"><table><thead><tr><th>Delivery</th><th>Recipient</th><th>State</th><th>Attempts</th><th>Bounce</th><th>Conversation</th><th>Sent</th></tr></thead><tbody>{''.join(delivery_rows) or '<tr><td colspan="7" class="muted">No deliveries in selected range.</td></tr>'}</tbody></table></div>
<h3>Retry history</h3><div class="scroll"><table><thead><tr><th>Started</th><th>Delivery</th><th>Recipient</th><th>State</th><th>Classification</th></tr></thead><tbody>{''.join(attempt_rows) or '<tr><td colspan="5" class="muted">No attempts in selected range.</td></tr>'}</tbody></table></div>
<div class="notice">Ambiguous post-DATA outcomes remain delivery-uncertain and are not presented as safely retryable.</div>
</section>'''


def render_suppressions(base: Any, request: Request) -> str:
    from .runtime_v950 import reliability_store

    rows = reliability_store().list_suppressions(active_only=True, limit=500)
    csrf = escape(str(base._csrf_value()), quote=True)
    body_rows = []
    for row in rows:
        recipient = str(row.get("recipient") or "")
        body_rows.append(
            '<tr>'
            f'<td>{escape(recipient)}</td><td>{escape(str(row.get("reason") or ""))}</td>'
            f'<td>{escape(str(row.get("source") or ""))}</td><td>{escape(str(row.get("updated_at") or ""))}</td>'
            '<td><form method="post" action="/dashboard/suppression/unsuppress">'
            f'<input type="hidden" name="csrf" value="{csrf}"><input type="hidden" name="recipient" value="{escape(recipient, quote=True)}">'
            '<button type="submit">Unsuppress</button></form></td></tr>'
        )
    return f'''
<section class="tab-panel" id="panel-suppressions" data-panel="suppressions">
<div class="v951-pagehead"><div><h2>Suppressions</h2><p>Current suppression inventory is a point-in-time snapshot, not a fabricated history.</p></div></div>
<div class="v951-metrics">{_metric('Active suppressions', len(rows), 'current snapshot')}</div>
<form method="post" action="/dashboard/suppression/suppress" class="v951-toolbar"><input type="hidden" name="csrf" value="{csrf}"><label>Recipient <input name="recipient" type="email" required></label><label>Reason <select name="reason"><option value="manual">manual</option><option value="unsubscribe">unsubscribe</option></select></label><button type="submit">Suppress</button></form>
<div class="scroll"><table><thead><tr><th>Recipient</th><th>Reason</th><th>Source</th><th>Updated</th><th></th></tr></thead><tbody>{''.join(body_rows) or '<tr><td colspan="5" class="muted">No active local suppressions.</td></tr>'}</tbody></table></div>
<div class="notice">The backend persists suppression events, but v9.5.0 exposes no public read service for that event history. v9.5.1 therefore does not invent or reconstruct a historical series.</div>
</section>'''


def render_security(base: Any, request: Request) -> str:
    domains = _list_result(_safe_call(base, base.list_authorized_domains), "domains")
    recipients = _list_result(_safe_call(base, base.list_authorized_recipients), "recipients")
    domain_rows = "".join(f'<li><code>{escape(str(row.get("domain") or ""))}</code> {escape(str(row.get("note") or ""))}</li>' for row in domains)
    recipient_rows = "".join(f'<li><code>{escape(str(row.get("email_address") or row.get("recipient") or ""))}</code> {escape(str(row.get("note") or ""))}</li>' for row in recipients)
    return f'''
<section class="tab-panel" id="panel-security" data-panel="security">
<div class="v951-pagehead"><div><h2>Security &amp; Recipient Policy</h2><p>Presentation of the existing sender and recipient authorization policy; no policy semantics are changed.</p></div></div>
<div class="v951-grid"><section class="card"><h3>Authorized domains</h3><ul>{domain_rows or '<li class="muted">None configured.</li>'}</ul><p><a href="#domains">Open existing domain controls</a></p></section><section class="card"><h3>Exact authorized recipients</h3><ul>{recipient_rows or '<li class="muted">None configured.</li>'}</ul><p><a href="#recipients">Open existing recipient controls</a></p></section></div>
<div class="notice">Owner/account scoping and canonical account IDs remain enforced by the existing backend.</div>
</section>'''


def render_system(base: Any, request: Request) -> str:
    statuses = (
        ("Build status", _safe_call(base, base.build_status)),
        ("Tracking status", _safe_call(base, base.tracking_status)),
        ("Knowledge status", _safe_call(base, base.context_engine().status)),
        ("File Store status", _safe_call(base, base.file_store().status)),
        ("Scheduler status", _safe_call(base, base.scheduler().status)),
    )
    cards = "".join(f'<section class="card"><h3>{escape(label)}</h3>{_status_rows(value)}{_details(value)}</section>' for label, value in statuses)
    return f'''
<section class="tab-panel" id="panel-system" data-panel="system">
<div class="v951-pagehead"><div><h2>System</h2><p>Current runtime/store snapshots are deliberately not historical-window filtered.</p></div></div><div class="v951-grid">{cards}</div>
</section>'''


def render_coverage(base: Any, request: Request) -> str:
    total = sum(len(items) for items in MCP_COVERAGE.values())
    groups = []
    for name, tools in MCP_COVERAGE.items():
        tools_html = "".join(f'<code class="v951-tool">{escape(tool)}</code>' for tool in tools)
        groups.append(f'<section class="card"><div class="panel-title"><h3>{escape(name)}</h3><span class="badge">{len(tools)}</span></div><div class="v951-tools">{tools_html}</div></section>')
    status = _safe_call(base, base.build_status)
    new_commands = status.get("new_mail_mcp_commands", 0) if isinstance(status, dict) else "—"
    return f'''
<section class="tab-panel" id="panel-coverage" data-panel="coverage">
<div class="v951-pagehead"><div><h2>MCP Coverage</h2><p>Existing v9.5 capability surface mapped to operator views. No names are added for the redesign.</p></div></div>
<div class="v951-metrics">{_metric('Mapped MCP functions', total, 'existing surface')}{_metric('New v9.5 mail command names', new_commands, 'must remain 0')}</div>
<div class="v951-grid">{''.join(groups)}</div></section>'''


def _new_panels(base: Any, core: Any, request: Request) -> str:
    return "\n".join((
        render_mail_health(base, request),
        render_inbox(base, request),
        render_compose(base, request),
        render_deliveries(base, core, request),
        render_suppressions(base, request),
        render_security(base, request),
        render_system(base, request),
        render_coverage(base, request),
    ))


def _nav_html() -> str:
    def link(view: str, label: str, icon: str = "") -> str:
        return f'<a class="tab-link" href="#{view}" data-tab="{view}"><span class="v951-ico">{escape(icon)}</span>{escape(label)}</a>'
    return (
        '<nav class="tabs v951-nav" aria-label="Dashboard sections">'
        '<div class="v951-brand"><strong>Postmaster</strong><small>WebGUI v9.5.1</small></div>'
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


STYLE = r'''
/* webgui-v951-redesign */
:root { --v951-sidebar:238px; }
main { max-width:1600px; margin-left:var(--v951-sidebar); padding:28px 26px 60px; }
.v951-nav { position:fixed; inset:0 auto 0 0; z-index:50; width:var(--v951-sidebar); height:100vh; overflow:auto; margin:0; padding:14px 12px; display:flex; flex-direction:column; flex-wrap:nowrap; gap:3px; border:0; border-right:1px solid var(--line); background:var(--card); }
.v951-nav .tab-link { width:100%; border:0; background:transparent; border-radius:9px; padding:8px 9px; }
.v951-nav .tab-link.active { background:rgba(104,160,255,.12); box-shadow:none; }
.v951-brand { display:flex; flex-direction:column; padding:7px 9px 15px; border-bottom:1px solid var(--line); margin-bottom:4px; }
.v951-brand strong { font-size:17px; } .v951-brand small { color:var(--muted); }
.v951-nav-label { padding:12px 9px 4px; color:var(--muted); font-size:10px; font-weight:800; text-transform:uppercase; letter-spacing:.09em; }
.v951-ico { width:24px; text-align:center; }
.v951-legacy-links { margin-top:auto; border-top:1px solid var(--line); padding:10px 8px; display:grid; gap:6px; font-size:11px; }
.v951-legacy-links a { color:var(--muted); }
.v951-pagehead { display:flex; align-items:flex-start; justify-content:space-between; gap:16px; margin-bottom:14px; }
.v951-pagehead h2 { font-size:24px; margin:0 0 4px; }.v951-pagehead p { margin:0; color:var(--muted); }
.v951-rangebar { display:inline-flex; gap:3px; padding:3px; border:1px solid var(--line); border-radius:9px; background:var(--bg); }
.v951-range { padding:6px 8px; color:var(--muted); text-decoration:none; border-radius:7px; font-size:11px; font-weight:700; }
.v951-range.active { background:var(--accent); color:#07111f; }
.v951-metrics { display:grid; grid-template-columns:repeat(auto-fit,minmax(135px,1fr)); gap:10px; margin:12px 0 16px; }
.v951-metric { border:1px solid var(--line); background:var(--card); border-radius:11px; padding:13px; display:flex; flex-direction:column; gap:5px; }
.v951-metric span,.v951-metric small { color:var(--muted); font-size:11px; }.v951-metric strong { font-size:22px; }
.v951-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(300px,1fr)); gap:14px; margin:12px 0; }
.v951-kv { display:flex; justify-content:space-between; gap:12px; padding:7px 0; border-bottom:1px solid var(--line); font-size:12px; }.v951-kv span { color:var(--muted); }.v951-kv b { text-align:right; overflow-wrap:anywhere; }
.v951-details { margin:12px 0; }.v951-details summary { cursor:pointer; color:var(--accent); font-weight:700; }.v951-details pre { max-height:430px; overflow:auto; border:1px solid var(--line); border-radius:9px; padding:11px; white-space:pre-wrap; }
.v951-toolbar { display:flex; gap:9px; flex-wrap:wrap; align-items:end; margin:10px 0 14px; }.v951-toolbar label { display:flex; flex-direction:column; gap:4px; color:var(--muted); font-size:11px; }
.v951-formgrid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:10px; }.v951-formgrid .wide { grid-column:1/-1; }.v951-formgrid label { display:flex; flex-direction:column; gap:5px; color:var(--muted); font-size:11px; }
.v951-formgrid input,.v951-formgrid textarea,.v951-toolbar input,.v951-toolbar select { width:100%; min-width:150px; border:1px solid var(--line); border-radius:8px; background:var(--bg); color:var(--text); padding:8px; }
.v951-checks { display:flex; gap:14px; flex-wrap:wrap; margin:13px 0; }.v951-compose button { margin-top:12px; }.v951-message { white-space:pre-wrap; max-height:560px; overflow:auto; }
.v951-tools { display:flex; gap:6px; flex-wrap:wrap; }.v951-tool { border:1px solid var(--line); border-radius:7px; padding:5px 7px; font-size:10px; background:var(--bg); }
#panel-overview > .v951-pagehead:first-child + .v951-metrics { margin-top:0; }
#mail-standards { display:none; }
@media(max-width:820px){ :root{--v951-sidebar:0px} main{margin-left:0;padding:18px 14px 50px}.v951-nav{position:static;width:auto;height:auto;flex-direction:row;flex-wrap:wrap;border-right:0;border-bottom:1px solid var(--line)}.v951-nav-label,.v951-brand,.v951-legacy-links{width:100%}.v951-nav .tab-link{width:auto}.v951-pagehead{flex-direction:column}.v951-formgrid{grid-template-columns:1fr}.v951-formgrid .wide{grid-column:auto} }
'''


def augment_dashboard(body: str, base: Any, core: Any, request: Request) -> str:
    if "webgui-v951-redesign" in body:
        return body
    nav_start = body.find('<nav class="tabs"')
    if nav_start >= 0:
        nav_end = body.find("</nav>", nav_start)
        if nav_end >= 0:
            body = body[:nav_start] + _nav_html() + body[nav_end + len("</nav>"):]
    marker = '<section class="tab-panel" id="panel-overview" data-panel="overview">'
    if marker in body:
        body = body.replace(marker, marker + render_overview(base, core, request), 1)
    tracking_marker = '<section class="tab-panel" id="panel-tracking" data-panel="tracking">'
    if tracking_marker in body:
        body = body.replace(tracking_marker, tracking_marker + render_tracking_summary(base, core, request), 1)
    if "</style>" in body:
        body = body.replace("</style>", STYLE + "\n</style>", 1)
    if "\n<script>" in body:
        body = body.replace("\n<script>", "\n" + _new_panels(base, core, request) + "\n<script>", 1)
    allowed_old = "new Set(['overview','accounts','amp','tracking','domains','recipients','projects','knowledge','files','scheduler'])"
    allowed_new = "new Set(['overview','accounts','mail-health','inbox','compose','tracking','deliveries','suppressions','domains','recipients','projects','knowledge','files','scheduler','security','amp','system','coverage'])"
    body = body.replace(allowed_old, allowed_new, 1)
    requested = (request.query_params.get("ui_view") or "").strip()
    if requested:
        fallback = "#" + requested if requested in {
            "overview", "accounts", "mail-health", "inbox", "compose", "tracking",
            "deliveries", "suppressions", "domains", "recipients", "projects",
            "knowledge", "files", "scheduler", "security", "amp", "system", "coverage",
        } else "#overview"
        body = body.replace(
            "const raw = (window.location.hash || '#overview').slice(1);",
            f"const raw = (window.location.hash || {json.dumps(fallback)}).slice(1);",
            1,
        )
    return body


async def compose_send(base: Any, request: Request):
    form, error = await base._verified_form(request)
    if error:
        return error
    recipients = [value.strip() for value in str(form.get("to") or "").replace(";", ",").split(",") if value.strip()]
    if not recipients:
        return RedirectResponse("/?ui_view=compose&compose_result=Recipient+required#compose", status_code=303)
    result = _safe_call(
        base,
        base.send_email,
        to=recipients,
        subject=str(form.get("subject") or ""),
        body=str(form.get("body") or ""),
        account_id=str(form.get("account_id") or "").strip() or None,
        track_opens=True if form.get("track_opens") else None,
        newsletter_mode=bool(form.get("newsletter_mode")),
        unsubscribe_url=str(form.get("unsubscribe_url") or "").strip() or None,
        unsubscribe_email=str(form.get("unsubscribe_email") or "").strip() or None,
        one_click_unsubscribe=bool(form.get("one_click_unsubscribe")),
        dsn_notify_success=bool(form.get("dsn_notify_success")),
    )
    ok = isinstance(result, dict) and result.get("ok") is not False
    message = "Message sent" if ok else f"Send failed: {str(result.get('error') if isinstance(result, dict) else result)[:160]}"
    return RedirectResponse("/?" + urlencode({"ui_view": "compose", "compose_result": message}) + "#compose", status_code=303)


def install_webgui_v951(app: Any, base: Any, core: Any, legacy_dashboard: Any) -> Any:
    async def dashboard_home(request: Request):
        response = await legacy_dashboard(request)
        if "text/html" not in str(response.headers.get("content-type", "")).lower():
            return response
        try:
            body = augment_dashboard(response.body.decode("utf-8"), base, core, request)
            return HTMLResponse(
                body,
                status_code=response.status_code,
                headers={key: value for key, value in response.headers.items() if key.lower() != "content-length"},
            )
        except Exception:
            base.logger.info("Could not augment v9.5.1 WebGUI", exc_info=True)
            return response

    async def send_route(request: Request):
        return await compose_send(base, request)

    routes = app.router.routes
    for index, route in enumerate(list(routes)):
        if isinstance(route, Route) and route.path == "/":
            routes[index] = Route("/", dashboard_home, methods=["GET"])
            break
    mount_index = next((i for i, route in enumerate(routes) if isinstance(route, Mount)), len(routes))
    if not any(isinstance(route, Route) and route.path == "/dashboard/compose/send" for route in routes):
        routes.insert(mount_index, Route("/dashboard/compose/send", send_route, methods=["POST"]))
    return dashboard_home
