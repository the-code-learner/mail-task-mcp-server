from __future__ import annotations

from html import escape
from typing import Any

from starlette.requests import Request

from . import webgui_v951 as v951
from . import webgui_v952 as v952
from . import webgui_v953 as v953


STYLE = r'''
/* webgui-v954-sent-tracking */
.v954-track{display:inline-flex;align-items:center;border:1px solid var(--line);border-radius:999px;padding:3px 8px;font-size:11px;font-weight:700;white-space:nowrap}
.v954-track-open{border-color:#6f9fd8}.v954-track-click{border-color:#79b98a}.v954-track-none{color:var(--muted)}
.v954-tracking{margin-top:14px}.v954-delivery{margin-top:10px}
.v954-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:8px;margin:8px 0}
.v954-metric{border:1px solid var(--line);border-radius:8px;padding:8px}.v954-metric strong{display:block;font-size:11px;color:var(--muted);margin-bottom:3px}
.v954-links td{vertical-align:top}
'''

_CORE: Any = None


def _message_id(row: dict[str, Any] | None) -> str:
    if not isinstance(row, dict):
        return ""
    return str(row.get("message_id") or row.get("Message-ID") or "").strip()


def _sent_mailbox(accounts: list[dict[str, Any]], account_id: str | None) -> str:
    for row in accounts:
        if v953._account_id(row) == (account_id or ""):
            return str(row.get("sent_mailbox") or "Sent").strip() or "Sent"
    return "Sent"


def _is_sent_mailbox(accounts: list[dict[str, Any]], account_id: str | None, mailbox: str) -> bool:
    selected = str(mailbox or "").strip().casefold()
    configured = _sent_mailbox(accounts, account_id).casefold()
    return bool(selected) and selected in {configured, "sent", "inbox.sent"}


def _safe_list(base: Any, fn: Any, *args: Any, **kwargs: Any) -> tuple[list[dict[str, Any]], bool]:
    result = v951._safe_call(base, fn, *args, **kwargs)
    if isinstance(result, dict) and result.get("ok") is False:
        return [], False
    return v951._list_result(result, "deliveries", "links", "results", "events"), True


def _tracking_label(model: dict[str, Any] | None) -> tuple[str, str]:
    if not model:
        return "Non tracciata", "v954-track-none"
    if model.get("unavailable"):
        return "Tracking non disponibile", "v954-track-none"
    deliveries = model.get("deliveries") or []
    if not deliveries:
        return "Non tracciata", "v954-track-none"
    recipients = len(deliveries)
    opened = int(model.get("opened_recipients") or 0)
    clicked = int(model.get("clicked_recipients") or 0)
    if recipients > 1:
        css = "v954-track-click" if clicked else ("v954-track-open" if opened else "v954-track-none")
        return f"Aperti {opened}/{recipients} · Click {clicked}/{recipients}", css
    if clicked:
        return "Link cliccato", "v954-track-click"
    if opened:
        return "Apertura rilevata", "v954-track-open"
    return "Nessuna attività", "v954-track-none"


def _build_tracking_read_model(
    base: Any,
    core: Any,
    *,
    account_id: str,
    message_ids: list[str],
    detail_message_id: str = "",
) -> dict[str, dict[str, Any]]:
    """Read-only Sent enrichment keyed strictly by account_id + RFC Message-ID."""
    wanted = {str(value).strip() for value in message_ids if str(value).strip()}
    if not wanted:
        return {}
    deliveries, available = _safe_list(
        base, base.list_tracking_deliveries, account_id=account_id, limit=1000
    )
    if not available:
        return {message_id: {"unavailable": True, "deliveries": []} for message_id in wanted}
    scoped = [
        row for row in deliveries
        if str(row.get("account_id") or "") == account_id
    ]
    direct: dict[str, list[dict[str, Any]]] = {}
    for row in scoped:
        message_id = _message_id(row)
        if message_id in wanted:
            direct.setdefault(message_id, []).append(row)

    models: dict[str, dict[str, Any]] = {}
    for message_id, matched in direct.items():
        campaign_ids = list(dict.fromkeys(
            str(row.get("campaign_id") or "").strip()
            for row in matched if str(row.get("campaign_id") or "").strip()
        ))
        related: list[dict[str, Any]] = []
        links: list[dict[str, Any]] = []
        for campaign_id in campaign_ids:
            campaign_rows, ok = _safe_list(
                base,
                base.list_tracking_deliveries,
                campaign_id=campaign_id,
                account_id=account_id,
                limit=1000,
            )
            related.extend(
                row for row in (campaign_rows if ok else matched)
                if str(row.get("account_id") or "") == account_id
            )
            if core is not None and hasattr(core, "list_tracking_links"):
                campaign_links, _ = _safe_list(
                    base,
                    core.list_tracking_links,
                    campaign_id=campaign_id,
                    account_id=account_id,
                    limit=2000,
                )
                links.extend(
                    row for row in campaign_links
                    if str(row.get("account_id") or "") == account_id
                )
        if not related:
            related = list(matched)
        deduped: dict[str, dict[str, Any]] = {}
        for index, row in enumerate(related):
            key = str(row.get("id") or row.get("delivery_id") or f"row-{index}")
            deduped[key] = row
        related = list(deduped.values())
        delivery_ids = {str(row.get("id") or row.get("delivery_id") or "") for row in related}
        links_by_delivery: dict[str, list[dict[str, Any]]] = {}
        for link in links:
            did = str(link.get("delivery_id") or "")
            if did in delivery_ids:
                links_by_delivery.setdefault(did, []).append(link)
        model: dict[str, Any] = {
            "campaign_id": campaign_ids[0] if len(campaign_ids) == 1 else "",
            "campaign_ids": campaign_ids,
            "deliveries": related,
            "links_by_delivery": links_by_delivery,
            "opened_recipients": sum(1 for row in related if int(row.get("open_count") or 0) > 0),
            "clicked_recipients": sum(
                1 for row in related
                if any(
                    int(link.get("total_clicks") or 0) > 0
                    for link in links_by_delivery.get(str(row.get("id") or row.get("delivery_id") or ""), [])
                )
            ),
        }
        if message_id == detail_message_id and core is not None and hasattr(core, "get_tracking_summary"):
            summaries: dict[str, dict[str, Any]] = {}
            for row in related:
                did = str(row.get("id") or row.get("delivery_id") or "")
                if not did:
                    continue
                summary = v951._safe_call(
                    base, core.get_tracking_summary, delivery_id=did, account_id=account_id
                )
                if isinstance(summary, dict) and summary.get("ok") is not False:
                    summaries[did] = summary
            model["click_summaries"] = summaries
        models[message_id] = model
    return models


def _metric(label: str, value: Any) -> str:
    text = str(value or "").strip() or "—"
    return f'<div class="v954-metric"><strong>{escape(label)}</strong>{escape(text)}</div>'


def _delivery_detail(delivery: dict[str, Any], model: dict[str, Any]) -> str:
    did = str(delivery.get("id") or delivery.get("delivery_id") or "")
    links = model.get("links_by_delivery", {}).get(did, [])
    clicked_links = [link for link in links if int(link.get("total_clicks") or 0) > 0]
    total_clicks = sum(int(link.get("total_clicks") or 0) for link in links)
    first_clicks = sorted(str(link.get("first_click") or "") for link in links if link.get("first_click"))
    last_clicks = sorted(str(link.get("last_click") or "") for link in links if link.get("last_click"))
    summary = model.get("click_summaries", {}).get(did, {})
    unique_clicks = summary.get("unique_clicks")
    if unique_clicks is None:
        unique_clicks = sum(int(link.get("unique_clicks") or 0) for link in links)
    qualitative = summary.get("qualitative_estimate") or {}
    interpretation = ""
    if isinstance(qualitative, dict) and qualitative:
        interpretation = (
            '<p class="small muted">Interpretazione query-time: '
            f'{int(qualitative.get("likely_provider_unique_clicks") or 0)} click unici probabilmente provider/proxy · '
            f'{int(qualitative.get("uncertain_unique_clicks") or 0)} incerti. I conteggi raw non vengono riscritti.</p>'
        )
    link_rows = []
    for link in clicked_links:
        label = str(link.get("anchor_text") or link.get("destination_host") or "Link")
        url = str(link.get("original_url") or link.get("normalized_url") or "")
        link_rows.append(
            "<tr>"
            f"<td>{escape(label)}</td><td><code>{escape(url)}</code></td>"
            f"<td>{int(link.get('total_clicks') or 0)}</td><td>{int(link.get('unique_clicks') or 0)}</td>"
            f"<td>{escape(str(link.get('first_click') or '—'))}</td><td>{escape(str(link.get('last_click') or '—'))}</td></tr>"
        )
    links_html = (
        '<div class="scroll v954-links"><table><thead><tr><th>Link</th><th>Destinazione</th><th>Click totali</th><th>Unici</th><th>Primo click</th><th>Ultimo click</th></tr></thead><tbody>'
        + "".join(link_rows) + '</tbody></table></div>'
        if link_rows else '<p class="small muted">Nessun link cliccato rilevato per questa delivery.</p>'
    )
    metrics = "".join((
        _metric("Ruolo", delivery.get("recipient_role")),
        _metric("Delivery state", delivery.get("delivery_state")),
        _metric("Conversation state", delivery.get("conversation_state")),
        _metric("Open rilevati", int(delivery.get("open_count") or 0)),
        _metric("Prima apertura", delivery.get("first_open_at")),
        _metric("Ultima apertura", delivery.get("last_open_at")),
        _metric("Click totali", total_clicks),
        _metric("Click unici", int(unique_clicks or 0)),
        _metric("Primo click", first_clicks[0] if first_clicks else ""),
        _metric("Ultimo click", last_clicks[-1] if last_clicks else ""),
    ))
    return (
        '<section class="card v954-delivery">'
        f'<h4>{escape(str(delivery.get("recipient") or "Destinatario"))}</h4>'
        f'<div class="v954-grid">{metrics}</div>{interpretation}{links_html}</section>'
    )


def _tracking_detail(model: dict[str, Any] | None) -> str:
    label, css = _tracking_label(model)
    if not model or model.get("unavailable"):
        note = (
            "I dati di tracking non sono disponibili in questo momento."
            if model and model.get("unavailable")
            else "Nessuna delivery di tracking corrisponde a questo account e Message-ID."
        )
        return (
            '<section class="card v954-tracking"><h3>Tracking</h3>'
            f'<p><span class="v954-track {css}">{escape(label)}</span></p>'
            f'<p class="small muted">{escape(note)}</p></section>'
        )
    campaign = str(model.get("campaign_id") or "")
    campaign_note = f' · campagna <code>{escape(campaign)}</code>' if campaign else ""
    deliveries = "".join(_delivery_detail(row, model) for row in model.get("deliveries") or [])
    return (
        '<section class="v954-tracking"><div class="v951-pagehead"><div><h3>Tracking</h3>'
        '<p>Telemetria osservata: proxy immagini, scanner e prefetch possono generare aperture o click automatici.'
        f'{campaign_note}</p></div><span class="v954-track {css}">{escape(label)}</span></div>{deliveries}</section>'
    )


def render_inbox(base: Any, request: Request) -> str:
    params = v952._canonical_inbox_params(request)
    accounts, account_id = v953._selected_account_id(base, request)
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
    mailbox_values, live = v953._mailboxes(base, account_id) if active else ([mailbox], False)
    if live and mailbox not in mailbox_values:
        mailbox = "INBOX" if "INBOX" in mailbox_values else mailbox_values[0]
        params["mailbox"] = mailbox
        params.pop("message_uid", None)
    elif mailbox not in mailbox_values:
        mailbox_values.append(mailbox)
    uid = params.get("message_uid", "")
    results: list[dict[str, Any]] = []
    detail: Any = None
    search_requested = active and bool(accounts)
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

    is_sent = _is_sent_mailbox(accounts, account_id, mailbox)
    detail_message_id = _message_id(detail) if is_sent else ""
    message_ids = [_message_id(row) for row in results] if is_sent else []
    if detail_message_id:
        message_ids.append(detail_message_id)
    models = (
        _build_tracking_read_model(
            base,
            _CORE,
            account_id=account_id or "",
            message_ids=message_ids,
            detail_message_id=detail_message_id,
        )
        if is_sent and account_id else {}
    )

    rows = []
    for row in results:
        row_uid = str(row.get("uid") or row.get("id") or "").strip()
        if not row_uid:
            continue
        href = v952._inbox_url(params, uid=row_uid)
        tracking_cell = ""
        if is_sent:
            label, css = _tracking_label(models.get(_message_id(row)))
            tracking_cell = f'<td><span class="v954-track {css}">{escape(label)}</span></td>'
        rows.append(
            '<tr>'
            f'<td><code>{escape(row_uid)}</code></td>'
            f'<td>{escape(str(row.get("from") or row.get("from_address") or ""))}</td>'
            f'<td>{escape(str(row.get("subject") or ""))}</td>'
            f'<td>{escape(str(row.get("date") or row.get("received_at") or ""))}</td>'
            f'{tracking_cell}<td><a href="{escape(href, quote=True)}">View</a></td></tr>'
        )
    columns = 6 if is_sent else 5
    if not accounts:
        empty = f'<tr><td colspan="{columns}" class="muted">No enabled email accounts are configured.</td></tr>'
    elif search_requested:
        empty = f'<tr><td colspan="{columns}" class="muted">No messages matched this search.</td></tr>'
    else:
        empty = f'<tr><td colspan="{columns}" class="muted">Open Inbox to load the default account.</td></tr>'
    tracking_head = '<th>Tracking</th>' if is_sent else ""
    table = (
        '<div class="scroll"><table><thead><tr><th>UID</th><th>From</th><th>Subject</th><th>Date</th>'
        + tracking_head + '<th></th></tr></thead><tbody>' + ("".join(rows) or empty) + '</tbody></table></div>'
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
        back = v952._inbox_url(params)
        sent_tracking = _tracking_detail(models.get(detail_message_id)) if is_sent else ""
        detail_html = (
            f'<p><a href="{escape(back, quote=True)}">← Back to results</a></p>'
            f'<div class="v951-pagehead"><div><h3>{subject}</h3><p>Mailbox {escape(mailbox)} · UID {escape(uid)}</p></div></div>'
            f'<section class="card"><pre class="v951-message">{escape(text[:20000])}</pre></section>'
            f'{sent_tracking}<div class="v951-grid">{"".join(diagnostics)}</div>{v951._details(detail)}'
        )
    checked = " checked" if params.get("unread_only") == "1" else ""
    return f'''
<section class="tab-panel" id="panel-inbox" data-panel="inbox">
<div class="v951-pagehead"><div><h2>Inbox</h2><p>The selected configured account uses the existing IMAP search/read path.</p></div></div>
<form method="get" action="/dashboard/inbox/search" class="v951-toolbar">
<label>Account {v953._account_select(accounts, account_id)}</label>
<label>Mailbox {v953._mailbox_select(mailbox_values, mailbox)}</label>
<label>Subject <input name="subject" value="{escape(params.get("subject", ""), quote=True)}"></label>
<label>Text <input name="text" value="{escape(params.get("text", ""), quote=True)}"></label>
<label>Since days <input type="number" min="1" max="3650" name="since_days" value="{escape(params["since_days"], quote=True)}"></label>
<label><input type="checkbox" name="unread_only" value="1"{checked}> unread only</label>
<button type="submit">Search</button>
</form>{table}{detail_html}
</section>'''


def augment_dashboard(body: str, base: Any, core: Any, request: Request) -> str:
    body = v953.augment_dashboard(body, base, core, request)
    if "webgui-v954-sent-tracking" not in body and "</style>" in body:
        body = body.replace("</style>", STYLE + "\n</style>", 1)
    return body


def install_webgui_v954(app: Any, base: Any, core: Any, legacy_dashboard: Any) -> Any:
    global _CORE
    _CORE = core
    v951.render_inbox = render_inbox
    v951.augment_dashboard = augment_dashboard
    return legacy_dashboard
