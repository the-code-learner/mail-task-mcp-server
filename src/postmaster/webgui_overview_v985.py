"""v9.8.5 v5 Overview: real recent events and bounded performance, no demo data."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from email.utils import parseaddr
from html import escape
from typing import Any
from urllib.parse import urlencode

from starlette.requests import Request

from .webgui_tracking_v984 import _activity_trend, _events, _period, _received, _sent, _time
from .webgui_versions_v984 import version_summary
from .webgui_v962_views import _panel


def _esc(value: Any) -> str:
    return escape(str(value if value is not None else "—"), quote=True)


def _received_sample(base: Any, account: str, since: datetime, until: datetime) -> tuple[int | None,int | None,str]:
    if not account:
        return None, None, "Select an email account to see Inbox statistics."
    try:
        rows = base.mail_client(account).search_emails(
            mailbox="INBOX", since_days=max(1, min(3650, (datetime.now(UTC)-since).days+2)),
            limit=100,
        )
    except Exception:
        return None, None, "The Inbox sample could not be loaded."
    rows = rows if isinstance(rows, list) else []
    valid = []
    for row in rows:
        when=_time(str(row.get("date") or ""))
        if when is None:
            from email.utils import parsedate_to_datetime
            try: when=parsedate_to_datetime(str(row.get("date") or ""))
            except (ValueError,TypeError): when=None
        if when and since <= when.astimezone(UTC) < until:
            valid.append(row)
    senders={
        parseaddr(str(row.get("from") or ""))[1].strip().lower()
        for row in valid
        if parseaddr(str(row.get("from") or ""))[1].strip()
    }
    return len(valid),len(senders),"Recent INBOX sample (maximum 100 messages); not a complete mailbox history."


def render_overview_v985(base: Any, request: Request) -> str:
    from .webgui_tracking_v984 import _ZONE
    since, until, selected = _period(request)
    account = str(request.query_params.get("account_id") or request.query_params.get("account") or "").strip()
    # Email events have an account, not an owner/project. The filters are not ACLs.
    try:
        events, truncated = _events(base,account,"","",since,until,filters={})
    except Exception:
        events,truncated=[],False
    try:
        sent,sent_truncated=_sent(base,account,since,until)
    except Exception:
        sent,sent_truncated=[],False
    received,senders,received_note=_received_sample(base,account,since,until)
    opens={str(e.get("delivery_id") or "") for e in events
           if e.get("event_kind")=="email_open" and e.get("delivery_id")}
    click_messages={str(e.get("delivery_id") or "") for e in events
           if e.get("event_kind")=="email_click" and e.get("delivery_id")}
    unique_messages=max(1,len(sent))
    # Observed-event rates are NOT verified human reading percentages.
    open_rate=100*len(opens)/unique_messages if sent else None
    click_rate=100*len(click_messages)/unique_messages if sent else None
    if open_rate is not None: open_rate=min(100,open_rate)
    if click_rate is not None: click_rate=min(100,click_rate)
    choices=(("24h","24 hours"),("7d","7 days"),("28d","28 days"),("90d","90 days"),("custom","Custom"))
    date1=since.astimezone(_ZONE).date().isoformat()
    date2=(until.astimezone(_ZONE)-timedelta(days=1)).date().isoformat()
    periods="".join(
        f'<button type="submit" name="period" value="{key}" class="v985-period'
        f'{" selected" if selected==key else ""}">{label}</button>'
        for key,label in choices
    )
    custom=(
        f'<label>From <input type="date" name="from" value="{date1}"></label>'
        f'<label>To <input type="date" name="to" value="{date2}"></label>'
    )
    scope='<input type="hidden" name="account_id" value="'+_esc(account)+'">' if account else ""
    form=(
        '<form class="v985-periods" action="/" method="get">'
        '<input type="hidden" name="ui_view" value="overview">'
        +scope+periods+custom+'<button type="submit">Apply</button></form>'
    )
    recent=[]
    labels={
        "email_open":("◎","Email open observed"),
        "email_click":("↗","Email link clicked"),
        "tracked_url_click":("↗","Tracked URL requested"),
        "tracked_file_download":("▣","Tracked file requested"),
    }
    for item in events[:10]:
        kind=str(item.get("event_kind") or "")
        icon,label=labels.get(kind,("•","Tracking event"))
        timestamp=str(item.get("observed_at") or "")
        target=str(item.get("recipient") or item.get("label") or item.get("tracked_link_id") or "Event")
        tone="v985-open" if kind=="email_open" else "v985-click"
        recent.append(
            '<div class="v985-activity"><span class="v985-activity-icon '+tone+'">'+icon+'</span>'
            '<div class="v985-activity-body"><strong>'+_esc(label)+'</strong>'
            '<p>'+_esc(target)+'</p></div><time>'+_esc(timestamp[:16].replace("T"," "))+'</time></div>'
        )
    if not recent:
        recent.append('<p class="v985-empty">No tracking events recorded in this period.</p>')
    def metric(title: str, number: Any, detail: str) -> str:
        return (
            '<div class="v985-kpi"><small>'+_esc(title)+'</small>'
            '<strong>'+_esc(number)+'</strong>'
            '<span>'+_esc(detail)+'</span></div>'
        )
    sent_text=str(len(sent)) + ("+" if sent_truncated else "")
    received_text=str(received) if received is not None else "—"
    senders_text=str(senders) if senders is not None else "—"
    perf=(
        '<div class="v985-performance-grid">'
        +metric("Sent emails (tracked)",sent_text,"Distinct recorded message IDs")
        +metric("Recipients with observed opens",len(opens),"Not necessarily human readers")
        +metric("Received emails",received_text,"Bounded Inbox sample")
        +metric("Senders",senders_text,"Bounded Inbox sample")
        +'</div>'
    )
    def rate(name: str, value: float|None, note: str, css: str) -> str:
        display=(f"{value:.0f}%" if value is not None else "—")
        width=f"{max(0.0,min(100.0,value or 0.0)):.1f}%"
        return (
            '<div class="v985-rate"><div class="v985-rate-top"><strong>'+name+'</strong>'
            '<b>'+display+'</b></div>'
            '<div class="v985-rate-meter"><span class="'+css+'" style="width:'+width+'"></span></div>'
            '<p>'+_esc(note)+'</p></div>'
        )
    rates=rate("Observed open signals",open_rate,"Unique email deliveries with opens / tracked sent emails.","v985-rate-open")
    rates+=rate("Observed email click signals",click_rate,"Unique email deliveries with clicks / tracked sent emails.","v985-rate-click")
    warning=(
        'Tracking and proxy requests are signals, not confirmation of a human reading. '
        'Limited event history is displayed here; use Tracking and MCP for full paging. '
        +received_note
    )
    if truncated: warning+=" At least 500 events matched; rates may be undercounted."
    section=(
        '<div class="v985-overview">'
        '<div class="v985-pagehead"><div><h2>Overview</h2>'
        '<p>Recent tracking activity and operational performance</p></div>'
        '<a class="v985-link-button" href="#tracking" data-v962-nav="tracking">View tracking →</a></div>'
        +form+version_summary(base)
        +'<div class="v985-dashboard-grid">'
        '<section class="v985-feature"><header>Recent activity</header>'
        '<div class="v985-recent-items">'+''.join(recent)+'</div>'
        '<a class="v985-view-all" href="#tracking" data-v962-nav="tracking">All tracking events →</a></section>'
        '<section class="v985-feature"><header>My performance</header>'
        +perf+'<div class="v985-rate-grid">'+rates+'</div>'
        +'</section></div>'
        '<div class="v985-overview-note">'+_esc(warning)+'</div>'
        '</div>'
    )
    return _panel("overview",section)


OVERVIEW_STYLE = r"""
.v985-overview{max-width:1500px;margin:auto}
.v985-pagehead{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px;margin:7px 0 13px}
.v985-pagehead h2{font-size:22px;margin:0;letter-spacing:-.5px}
.v985-pagehead p{color:var(--v970-muted);font-size:11px;margin:5px 0}
.v985-link-button,.v985-view-all{color:var(--v970-accent);text-decoration:none;font-size:12px;font-weight:700}
.v985-periods{display:flex;align-items:center;flex-wrap:wrap;gap:6px;margin:10px 0 16px}
.v985-period{border:1px solid var(--v970-line)!important;border-radius:8px!important;
 background:var(--v970-surface)!important;color:var(--v970-text)!important;padding:7px 11px!important}
.v985-period.selected{background:#345ec2!important;border-color:#345ec2!important;color:white!important}
.v985-periods label{display:flex;align-items:center;gap:6px}
.v985-periods input{padding:5px!important;min-height:31px}
.v985-release-summary{margin-bottom:16px!important}
.v985-release-summary .panel-title{margin-bottom:9px}
.v985-release-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px}
.v985-release-grid>div{display:flex;flex-direction:column;gap:4px;min-width:0;padding:10px 12px;
 border:1px solid var(--v970-line);background:var(--v970-surface-2);border-radius:8px}
.v985-release-grid small{font-size:10px;color:var(--v970-muted)}
.v985-release-grid strong{font-size:19px;color:var(--v970-text);overflow-wrap:anywhere}
.v985-dashboard-grid{display:grid;grid-template-columns:1.02fr .98fr;gap:15px;align-items:start}
.v985-feature{border:1px solid var(--v970-line);background:var(--v970-surface);border-radius:11px;overflow:hidden;min-width:0}
.v985-feature>header{font-size:17px;font-weight:800;padding:16px 17px;border-bottom:1px solid var(--v970-line)}
.v985-activity{padding:14px 17px;display:flex;gap:11px;align-items:center;border-bottom:1px solid var(--v970-line)}
.v985-activity-icon{flex:0 0 35px;width:35px;height:35px;display:grid;place-items:center;
 font-size:16px;border-radius:50%;background:var(--v970-accent-soft);color:var(--v970-accent)}
.v985-activity-icon.v985-open{background:color-mix(in srgb,var(--v970-ok) 12%,var(--v970-surface));color:var(--v970-ok)}
.v985-activity-body{min-width:0;flex:1}
.v985-activity-body strong{font-size:12px}
.v985-activity-body p{margin:3px 0 0;color:var(--v970-muted);font-size:11px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.v985-activity time{font-size:10px;color:var(--v970-muted);white-space:nowrap}
.v985-view-all{display:block;padding:13px 17px}
.v985-empty{padding:16px;color:var(--v970-muted)}
.v985-performance-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;padding:13px}
.v985-kpi{padding:15px;border:1px solid var(--v970-line);background:var(--v970-surface-2);border-radius:9px;
 display:flex;flex-direction:column;gap:5px}
.v985-kpi small{font-size:10px;font-weight:800;letter-spacing:.04em;text-transform:uppercase;color:var(--v970-muted)}
.v985-kpi strong{font-size:25px;line-height:1.25}
.v985-kpi span{font-size:10px;color:var(--v970-muted)}
.v985-rate-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;padding:0 13px 13px}
.v985-rate{padding:12px;border:1px solid var(--v970-line);border-radius:9px;background:var(--v970-surface)}
.v985-rate-top{display:flex;justify-content:space-between;gap:7px;align-items:center}
.v985-rate-top strong{font-size:11px}.v985-rate-top b{font-size:18px}
.v985-rate-meter{height:8px;border-radius:8px;background:var(--v970-surface-2);overflow:hidden;margin:12px 0}
.v985-rate-meter span{display:block;height:100%;border-radius:8px}
.v985-rate-open{background:#55bd9a}.v985-rate-click{background:#e5a0a9}
.v985-rate p{font-size:10px;color:var(--v970-muted);margin:0}
.v985-overview-note{font-size:10px;color:var(--v970-muted);line-height:1.6;padding:14px 0}
@media(max-width:1030px){.v985-dashboard-grid{grid-template-columns:1fr}}
@media(max-width:700px){.v985-release-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:430px){.v985-performance-grid,.v985-rate-grid{grid-template-columns:1fr}.v985-activity time{max-width:80px;white-space:normal}}
"""


def install_overview_v985(views: Any, v962: Any) -> None:
    views.render_overview=render_overview_v985
    previous=v962._styles
    if getattr(previous,"_postmaster_v985_overview",False): return
    def styles()->str:
        return previous().rstrip()+"\n"+OVERVIEW_STYLE
    styles._postmaster_v985_overview=True
    v962._styles=styles
