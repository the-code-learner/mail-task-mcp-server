"""v9.8.4 live, English Tracking analytics pane over existing authenticated WebGUI."""
from __future__ import annotations

import calendar
from collections import Counter
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from html import escape
from typing import Any
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from starlette.requests import Request

from .tracking_geoip_v984 import global_tracking_network_events

_ZONE = ZoneInfo("Europe/Rome")
_PERIODS = {"24h": timedelta(hours=24), "7d": timedelta(days=7),
            "28d": timedelta(days=28), "90d": timedelta(days=90)}
_DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def _time(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed
    except (TypeError, ValueError):
        return None


def _period(request: Request) -> tuple[datetime, datetime, str]:
    selected = str(request.query_params.get("period") or "7d")
    now = datetime.now(UTC)
    if selected in _PERIODS:
        return now - _PERIODS[selected], now, selected
    if selected == "custom":
        try:
            start = datetime.strptime(str(request.query_params["from"]), "%Y-%m-%d").replace(tzinfo=_ZONE)
            end = datetime.strptime(str(request.query_params["to"]), "%Y-%m-%d").replace(tzinfo=_ZONE) + timedelta(days=1)
            if start >= end or end > now.astimezone(_ZONE) + timedelta(days=1):
                raise ValueError("Invalid dates")
            return start.astimezone(UTC), end.astimezone(UTC), selected
        except (KeyError, ValueError):
            return now - timedelta(days=7), now, "7d"
    return now - timedelta(days=7), now, "7d"


def _heatmap(values: list[str], label: str, *, note: str = "") -> str:
    counts: Counter[tuple[int, int]] = Counter()
    for value in values:
        dt = _time(value)
        if dt:
            local = dt.astimezone(_ZONE)
            counts[(local.weekday(), local.hour)] += 1
    maximum = max(counts.values(), default=0)
    cells = "".join(f'<span class="v984-hd">{h:02d}</span>' for h in range(24))
    for d, day in enumerate(_DAYS):
        cells += f'<strong class="v984-day">{day}</strong>'
        for hour in range(24):
            count = counts[(d, hour)]
            level = min(4, (4 * count + maximum - 1) // maximum) if maximum else 0
            cells += (
                f'<span class="v984-heat v984-l{level}" title="{day} {hour:02d}:00: '
                f'{count} event(s)" aria-label="{day} {hour}:00, {count} events"></span>'
            )
    head = escape(label)
    warning = f'<p class="v984-hint">{escape(note)}</p>' if note else ""
    return f'<section class="v984-panel"><h3>{head}</h3><div class="v984-heatwrap"><div class="v984-grid"><span></span>{cells}</div></div>{warning}</section>'


def _received(base: Any, account_id: str, since: datetime, until: datetime) -> tuple[list[str], str]:
    if not account_id:
        return [], "Select one email account to load received mail dates."
    days = max(1, min(3650, (datetime.now(UTC) - since).days + 2))
    try:
        rows = base.mail_client(account_id).search_emails(
            mailbox="INBOX", since_days=days, limit=100,
        )
    except Exception:
        return [], "Mailbox dates are unavailable; tracking data remains independent."
    if not isinstance(rows, list):
        return [], "Mailbox date lookup did not return messages."
    timestamps = []
    for row in rows:
        date = str(row.get("date") or "")
        dt = _time(date)
        if not dt:
            try:
                dt = parsedate_to_datetime(date)
            except (TypeError, ValueError):
                dt = None
        if dt and since <= dt.astimezone(UTC) < until:
            timestamps.append(dt.isoformat())
    return timestamps, "Recent INBOX sample (maximum 100 messages); not a complete IMAP history."


def _sent(base: Any, account_id: str, since: datetime, until: datetime) -> tuple[list[str], bool]:
    with base.analytics_store()._connect() as conn:
        clauses = ["sent_at>=?", "sent_at<?", "sent_at!=''"]
        args: list[Any] = [since.isoformat(), until.isoformat()]
        if account_id:
            clauses.append("account_id=?")
            args.append(account_id)
        results = conn.execute(
            "SELECT DISTINCT account_id,CASE WHEN message_id='' THEN id ELSE message_id END AS logical_id,"
            "sent_at FROM tracking_deliveries WHERE " + " AND ".join(clauses) +
            " ORDER BY sent_at DESC LIMIT 20001", args,
        ).fetchall()
    return [str(r["sent_at"]) for r in results[:20000]], len(results) > 20000


def _events(base: Any, account_id: str, owner_id: str, project_id: str,
            since: datetime, until: datetime) -> tuple[list[dict[str, Any]], bool]:
    # Bounded rows keep the WebGUI responsive; full historical paging is available via MCP.
    first = global_tracking_network_events(
        base.analytics_store(), account_id=account_id or None,
        owner_id=owner_id or None, project_id=project_id or None,
        since=since.isoformat(), until=until.isoformat(), limit=500, offset=0,
    )
    return list(first["events"]), len(first["events"]) >= 500


def _event_html(event: dict[str, Any]) -> str:
    def e(k: str) -> str:
        return escape(str(event.get(k) or "—"))
    return f'''<details class="v984-event">
<summary><strong>{e("event_kind")}</strong><time>{e("observed_at")}</time><span>{e("recipient")}</span><span>{e("geo_country_code")}</span></summary>
<div class="v984-detail">
<div><b>Raw IP</b><code>{e("source_ip")}</code></div>
<div><b>Estimated location</b>{e("geo_city")}, {e("geo_region")}, {e("geo_country_code")}</div>
<div><b>ASN</b>{e("geo_asn")}</div>
<div><b>ISP / Organization</b>{e("geo_isp")} / {e("geo_organization")}</div>
<div><b>GeoIP source / confidence</b>{e("geo_source")} / {e("geo_confidence")}</div>
<div><b>Browser / OS</b>{e("browser")} / {e("os")}</div>
<div><b>User-Agent</b><code>{e("user_agent")}</code></div>
<div><b>Owner / project / account (organizational)</b>{e("owner_id")} / {e("project_id")} / {e("account_id")}</div>
<div><b>Linked email / tracked link</b>{e("delivery_id")} / {e("tracked_link_id")}</div>
</div></details>'''


def render_tracking_v984(base: Any, core: Any, request: Request) -> str:
    from .webgui_v962_views import _panel
    since, until, active = _period(request)
    account = str(request.query_params.get("account_id") or "").strip()
    owner = str(request.query_params.get("owner_id") or "").strip()
    project = str(request.query_params.get("project_id") or "").strip()
    try:
        events, truncated = _events(base, account, owner, project, since, until)
        sent, sent_truncated = _sent(base, account, since, until)
        received, received_note = _received(base, account, since, until)
    except Exception as exc:
        return _panel("tracking", f'<section class="card"><h2>Tracking</h2><p>Unable to load analytics: {escape(type(exc).__name__)}</p></section>')
    kinds = Counter(str(x.get("event_kind") or "") for x in events)
    options = "".join(
        f'<option value="{escape(str(row.get("id") or ""),quote=True)}"'
        f'{" selected" if str(row.get("id") or "")==account else ""}>'
        f'{escape(str(row.get("label") or row.get("email_address") or row.get("id") or ""))}</option>'
        for row in base.account_store().list_accounts()
    )
    period_choices = [
        ("24h", "24 hours"), ("7d", "7 days"), ("28d", "28 days"),
        ("90d", "90 days"), ("custom", "Custom"),
    ]
    tabs = " ".join(
        f'<button type="submit" name="period" value="{k}" class="v984-period'
        f'{" active" if active==k else ""}">{label}</button>'
        for k, label in period_choices
    )
    fday = since.astimezone(_ZONE).date().isoformat()
    tday = (until.astimezone(_ZONE) - timedelta(days=1)).date().isoformat()
    custom = (
        f'<label>From <input type="date" name="from" value="{escape(fday)}"></label>'
        f'<label>To <input type="date" name="to" value="{escape(tday)}"></label>'
    )
    counts = (
        ("Tracking events shown", len(events)),
        ("Opens observed", kinds["email_open"]),
        ("Email clicks", kinds["email_click"]),
        ("Link/file clicks", kinds["tracked_url_click"] + kinds["tracked_file_download"]),
    )
    cards = "".join(f'<div class="v984-stat"><small>{escape(k)}</small><b>{v}</b></div>' for k,v in counts)
    label = f'{since.astimezone(_ZONE).strftime("%d %b %Y %H:%M")} – {until.astimezone(_ZONE).strftime("%d %b %Y %H:%M")} Europe/Rome'
    warning = (
        "This is a 500-event preview, not a complete historical count. Use MCP pagination for full results."
        if truncated else "All matching enriched events are included in this view."
    )
    heat = _heatmap(sent, "When emails are sent", note="Tracked deliveries only; unique message IDs.")
    heat += _heatmap(received, "When emails are received", note=received_note)
    heat += _heatmap([str(e.get("observed_at") or "") for e in events],
                     "When tracking events occur", note="Opens, email clicks, URL clicks and file downloads.")
    rows = "".join(_event_html(row) for row in events[:120])
    style = """<style>
.v984-tracking{--a:#345ec2;font:13px system-ui,sans-serif}
.v984-tracking .v984-toolbar{display:flex;flex-wrap:wrap;align-items:center;gap:9px}
.v984-tracking .v984-toolbar input,.v984-tracking .v984-toolbar select{max-width:165px}
.v984-tracking .v984-period{border:1px solid var(--line);border-radius:7px;padding:7px 10px;background:var(--card);color:var(--text)}
.v984-tracking .v984-period.active{background:var(--a);color:white}
.v984-tracking .v984-stats{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:9px;margin:14px 0}
.v984-tracking .v984-stat,.v984-tracking .v984-panel{padding:13px;border:1px solid var(--line);border-radius:9px;background:var(--card)}
.v984-tracking .v984-stat small{display:block;color:var(--muted)}
.v984-tracking .v984-stat b{font-size:23px}
.v984-tracking .v984-panel{margin:10px 0}
.v984-tracking .v984-grid{display:grid;grid-template-columns:33px repeat(24,minmax(9px,1fr));gap:3px;align-items:center}
.v984-tracking .v984-hd{font-size:8px;color:var(--muted);text-align:center}
.v984-tracking .v984-day{font-size:10px;color:var(--muted)}
.v984-tracking .v984-heat{height:15px;border-radius:2px;background:var(--card2);border:1px solid var(--line)}
.v984-tracking .v984-l1{background:#dceeea}.v984-tracking .v984-l2{background:#9bd2c0}
.v984-tracking .v984-l3{background:#53b395}.v984-tracking .v984-l4{background:#218268}
.v984-tracking .v984-heatwrap{overflow:auto}
.v984-tracking .v984-hint{font-size:11px;color:var(--muted)}
.v984-tracking .v984-event{border:1px solid var(--line);border-radius:7px;padding:9px;margin:7px 0}
.v984-tracking .v984-event summary{display:flex;gap:12px;flex-wrap:wrap;cursor:pointer}
.v984-tracking .v984-event time{color:var(--muted)}
.v984-tracking .v984-detail{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;padding:12px}
.v984-tracking .v984-detail div{overflow-wrap:anywhere}
.v984-tracking .v984-detail b{display:block;color:var(--muted);font-size:10px}
@media(max-width:750px){.v984-tracking .v984-stats{grid-template-columns:repeat(2,1fr)}
.v984-tracking .v984-detail{grid-template-columns:1fr}}
</style>"""
    html = f'''<div class="v984-tracking">{style}<div class="v951-pagehead"><h2>Tracking analytics</h2>
<p>Actual stored events · all owners/projects/accounts by default · network data is only an estimate</p></div>
<form method="get" action="/" class="v984-toolbar">
<input type="hidden" name="ui_view" value="tracking">
<label>Account <select name="account_id"><option value="">All accounts</option>{options}</select></label>
<label>Owner <input name="owner_id" value="{escape(owner,quote=True)}" placeholder="All owners"></label>
<label>Project <input name="project_id" value="{escape(project,quote=True)}" placeholder="All projects"></label>
{tabs}{custom}<button type="submit">Apply</button></form>
<p class="v984-hint">{escape(label)} · {escape(warning)}</p>
<div class="v984-stats">{cards}</div>
{heat}
<section class="v984-panel"><h3>Tracking event timeline</h3>
<p class="v984-hint">Expand any event to inspect the raw IP and recorded network details. Proxy IPs are not recipient location.</p>
{rows or '<p>No enriched tracking events in the selected period.</p>'}</section>
</div>'''
    return _panel("tracking", html)


def install_tracking_dashboard_v984(views: Any) -> None:
    views.render_tracking = render_tracking_v984
