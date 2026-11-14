"""Approved v5 Tracking view, driven only by existing live v9.8.4 services.

The page uses independent account-scoped email and owner/project-scoped generic
queries. This prevents a combined filter from accidentally hiding every event.
MCP authorization remains global; these are user-interface organization filters.
"""
from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta
from html import escape
from typing import Any
from urllib.parse import urlencode

from starlette.requests import Request

from .webgui_tracking_v984 import (
    _period, _events, _sent, _received, _heatmap,
    _activity_trend, _event_html, _time, _ZONE,
)
from .webgui_links_v984 import render_tracked_links_v984
from .webgui_v962_views import _panel


EVENT_TYPES=(
    ("email_open","Email opens"),
    ("email_click","Email clicks"),
    ("tracked_url_click","URL clicks"),
    ("tracked_file_download","File requests"),
)
TABS=(
    ("overview","Overview"),
    ("all","All events"),
    ("email","Email"),
    ("links","URL links"),
    ("files","File links"),
)
PERIODS=(
    ("24h","24 hours"),("7d","7 days"),("28d","28 days"),
    ("90d","90 days"),("custom","Custom"),
)


def _e(value: Any) -> str:
    return escape(str(value if value is not None else ""),quote=True)


def scoped_events(
    base: Any, *, account_id: str, owner_id: str, project_id: str,
    start: datetime, finish: datetime, filters: dict[str,Any],
) -> tuple[list[dict[str,Any]], bool]:
    """Never combine independent email-account and generic owner/project scopes."""
    requested=str(filters.get("event_kind") or "").strip()
    allowed={key for key,_label in EVENT_TYPES}
    kinds=([requested] if requested in allowed else [key for key,_label in EVENT_TYPES])
    merged=[]
    bounded=False
    for kind in kinds:
        email=kind.startswith("email_")
        contextual=dict(filters)
        contextual["event_kind"]=kind
        subset,truncated=_events(
            base,account_id if email else "",
            "" if email else owner_id,
            "" if email else project_id,
            start,finish,filters=contextual,
        )
        merged.extend(subset)
        bounded=bounded or truncated
    order=str(filters.get("sort") or "desc").lower()
    merged.sort(
        key=lambda item:(
            _time(str(item.get("observed_at") or "")) or datetime.min.replace(tzinfo=UTC),
            int(item.get("event_id") or 0),
        ),
        reverse=order!="asc",
    )
    bounded=bounded or len(merged)>500
    return merged[:500],bounded


def _period_form(request: Request, active: str) -> str:
    passed=request.query_params
    fields=(
        "account_id","owner_id","project_id","tracking_tab",
        "event_kind","recipient","country","city","provider","domain","asn","sort",
    )
    hidden="".join(
        '<input type="hidden" name="'+name+'" value="'+_e(passed.get(name))+'">'
        for name in fields if passed.get(name)
    )
    date1=str(passed.get("from") or "")
    date2=str(passed.get("to") or "")
    if not date1 or not date2:
        start,end,_selected=_period(request)
        date1=start.astimezone(_ZONE).date().isoformat()
        date2=(end.astimezone(_ZONE)-timedelta(days=1)).date().isoformat()
    # The lazy dashboard submits GET forms with new FormData(form), which does
    # NOT include the clicked submit button. Period buttons were therefore
    # silently losing their value and every selection reverted to 7 days.
    # Explicit period links make navigation and history deterministic.
    period_base={
        key:value for key,value in passed.items()
        if key not in {"ui_view","period","from","to"}
    }
    buttons="".join(
        '<a class="v985-period'+(" selected" if active==period else "")+
        '" href="'+_e(
            "/?"+urlencode({
                **period_base,"ui_view":"tracking","period":period,
                **({"from":date1,"to":date2} if period=="custom" else {}),
            })+"#tracking"
        )+'"'+(' aria-current="page"' if active==period else "")+'>'
        +label+'</a>'
        for period,label in PERIODS
    )
    return (
        '<form method="get" action="/" class="v985-tracking-periods">'
        '<input type="hidden" name="ui_view" value="tracking">'
        '<input type="hidden" name="period" value="custom">'
        '<strong>Statistics period</strong>'+hidden+buttons
        +'<label>From <input type="date" name="from" value="'+_e(date1)+'"></label>'
        +'<label>To <input type="date" name="to" value="'+_e(date2)+'"></label>'
        +'<button type="submit" class="v985-tracking-apply">Apply</button>'
        +'</form>'
    )


def _tabs(request: Request, current: str) -> str:
    base={key:value for key,value in request.query_params.items()
          if key not in {"tracking_tab","ui_view","event_kind"}}
    base["ui_view"]="tracking"
    links=[]
    for key,label in TABS:
        params=dict(base,tracking_tab=key)
        url="/?"+urlencode(params)+"#tracking"
        links.append(
            '<a class="v985-tracking-tab'+(" active" if current==key else "")
            +'" href="'+_e(url)+'" data-v960-fragment="tracking"'
            +(' aria-current="page"' if current==key else "")
            +'>'+label+'</a>'
        )
    return '<nav class="v985-tracking-tabs" aria-label="Tracking sections">'+"".join(links)+'</nav>'


def _advanced_filters(request: Request) -> str:
    p=request.query_params
    hidden_keys=("account_id","owner_id","project_id","tracking_tab","period","from","to")
    hidden='<input type="hidden" name="ui_view" value="tracking">'+"".join(
        '<input type="hidden" name="'+k+'" value="'+_e(p.get(k))+'">'
        for k in hidden_keys if p.get(k)
    )
    wanted=str(p.get("event_kind") or "")
    options='<option value="">All tracking events</option>'+"".join(
        '<option value="'+_e(key)+'"'+(' selected' if key==wanted else "")+'>'+label+'</option>'
        for key,label in EVENT_TYPES
    )
    fields='<label>Event type<select name="event_kind">'+options+'</select></label>'
    for name,label in (
        ("recipient","Recipient"),("country","Country"),("city","City"),
        ("provider","Network provider"),("domain","Domain"),("asn","ASN"),
    ):
        fields+='<label>'+label+'<input name="'+name+'" value="'+_e(p.get(name))+'"></label>'
    order=str(p.get("sort") or "desc")
    fields+='<label>Event chronology<select name="sort">'
    fields+='<option value="desc"'+(' selected' if order!="asc" else "")+'>Newest first</option>'
    fields+='<option value="asc"'+(' selected' if order=="asc" else "")+'>Oldest first</option>'
    fields+='</select></label>'
    return (
        '<details class="v985-tracking-filters"><summary>Advanced filters</summary>'
        '<form method="get" action="/">'+hidden+'<div class="v985-tracking-filter-grid">'
        +fields+'</div><button type="submit">Apply filters</button></form></details>'
    )


def _stat(name: str, value: Any, note: str) -> str:
    return (
        '<div class="v985-tracking-stat"><small>'+_e(name)+'</small>'
        '<strong>'+_e(value)+'</strong><span>'+_e(note)+'</span></div>'
    )


def _rate(name: str, value: float|None, explanation: str, css: str) -> str:
    shown=(f"{min(100,max(0,value)):.0f}%" if value is not None else "—")
    width=(f"{min(100,max(0,value)):.1f}%" if value is not None else "0%")
    return (
        '<div class="v985-tracking-rate"><div class="v985-tracking-rate-head">'
        '<strong>'+_e(name)+'</strong><b>'+_e(shown)+'</b></div>'
        '<div class="v985-tracking-rate-meter"><span class="'+css
        +'" style="width:'+width+'"></span></div>'
        '<p>'+_e(explanation)+'</p></div>'
    )


def _event_list(events: list[dict[str,Any]], limit: int) -> str:
    return "".join(_event_html(row) for row in events[:limit]) or (
        '<p class="v985-tracking-empty">No events in this period and scope.</p>'
    )


def render_tracking_v985(base: Any, core: Any, request: Request) -> str:
    start,finish,period=_period(request)
    tab=str(request.query_params.get("tracking_tab") or "overview")
    if tab not in {key for key,_ in TABS}:tab="overview"
    account=str(request.query_params.get("account_id") or request.query_params.get("account") or "").strip()
    owner=str(request.query_params.get("owner_id") or "").strip()
    project=str(request.query_params.get("project_id") or request.query_params.get("project") or "").strip()
    filters={key:str(request.query_params.get(key) or "").strip()
             for key in ("event_kind","recipient","country","city","provider","domain","sort","asn")}
    if filters["asn"]:
        try:filters["asn"]=int(filters["asn"])
        except ValueError:filters["asn"]=None
    errors=[]
    try:events,truncated=scoped_events(
        base,account_id=account,owner_id=owner,project_id=project,
        start=start,finish=finish,filters=filters,
    )
    except Exception as exc:
        events,truncated=[],False
        errors.append("Tracking event query unavailable: "+type(exc).__name__)
    try:sent,sent_truncated=_sent(base,account,start,finish)
    except Exception as exc:
        sent,sent_truncated=[],False
        errors.append("Sent history unavailable: "+type(exc).__name__)
    try:received,received_note=_received(base,account,start,finish)
    except Exception as exc:
        received,received_note=[],"Mail search currently unavailable"
        errors.append("Received history unavailable: "+type(exc).__name__)
    counts=Counter(str(e.get("event_kind") or "") for e in events)
    email_rows=[e for e in events if str(e.get("event_kind") or "").startswith("email_")]
    email_recipients={
        str(e.get("recipient") or "").strip().lower()
        for e in email_rows if str(e.get("recipient") or "").strip()
    }
    opened={
        str(e.get("delivery_id") or "") for e in email_rows
        if e.get("event_kind")=="email_open" and e.get("delivery_id")
    }
    clicked={
        str(e.get("delivery_id") or "") for e in email_rows
        if e.get("event_kind")=="email_click" and e.get("delivery_id")
    }
    def ratio(n: int)->float|None:
        return min(100,100*n/len(sent)) if sent else None
    rate_rows=(
        _rate("Observed open signals",ratio(len(opened)),
              "Unique deliveries with an open / tracked sent emails. May include proxies.","v985-r-open")
        +_rate("Observed email click signals",ratio(len(clicked)),
              "Unique deliveries with a click / tracked sent emails.","v985-r-click")
    )
    received_stat=str(len(received)) if account else "—"
    perf=(
        _stat("Sent emails",str(len(sent))+("+" if sent_truncated else ""),
              "Tracked sent message IDs")
        +_stat("Received emails",received_stat,
               "Recent bounded Inbox sample" if account else "Choose an email account")
        +_stat("Observed opens",counts["email_open"],"Not confirmed human reads")
        +_stat("Email clicks",counts["email_click"],"Observed interaction requests")
        +_stat("Recipients with events",len(email_recipients),"Tracked email recipient identifiers")
        +_stat("URL link clicks",counts["tracked_url_click"],"Generic tracked URL requests")
        +_stat("File download requests",counts["tracked_file_download"],"Not proof of receipt")
        +_stat("Tracking events",str(len(events))+("+" if truncated else ""),
               "Bounded 500-event preview")
    )
    warnings=[
        "City, ASN and provider describe the observed network connection, which may be a proxy or scanner. "
        "An open event is not proof of human reading.",
    ]
    if truncated:warnings.append("At least 500 events match; displayed counts and charts may be undercounts.")
    if not account:warnings.append("Received-mail heatmaps require a selected email account.")
    if errors:warnings.extend(errors)
    short_note=" ".join(warnings)
    recent=(
        '<section class="v985-tracking-box"><header>Recent activity'
        '<span>'+str(min(10,len(events)))+' shown</span></header>'
        '<div class="v985-tracking-events">'+_event_list(events,10)+'</div></section>'
    )
    performances=(
        '<section class="v985-tracking-box"><header>My performance'
        '<span>'+_e(period)+'</span></header>'
        '<div class="v985-tracking-performance-grid">'+perf+'</div>'
        '<div class="v985-tracking-rate-grid">'+rate_rows+'</div></section>'
    )
    heat=(
        _heatmap(sent,"When emails are sent",
                 note="Tracked sent messages; timings are stored events.")
        +_heatmap(received,"When emails are received",note=received_note)
        +_heatmap(
            [str(e.get("observed_at") or "") for e in events],
            "When tracking events occur",
            note="Email opens, email clicks, tracked URLs and file requests.",
        )
    )
    filtered=(
        [x for x in events if str(x.get("event_kind") or "").startswith("email_")]
        if tab=="email" else
        [x for x in events if x.get("event_kind")=="tracked_url_click"]
        if tab=="links" else
        [x for x in events if x.get("event_kind")=="tracked_file_download"]
        if tab=="files" else events
    )
    if tab=="overview":
        content=(
            '<div class="v985-tracking-two">'+recent+performances+'</div>'
            '<div class="v985-tracking-heatmaps">'+heat+'</div>'
            +_activity_trend(events,start,finish)
        )
    else:
        title={
            "all":"All tracking events",
            "email":"Email tracking",
            "links":"Tracked URLs",
            "files":"Tracked file requests",
        }.get(tab,"Tracking events")
        content=(
            '<section class="v985-tracking-box"><header>'+title
            +'<span>'+str(min(len(filtered),120))+' shown</span></header>'
            '<div class="v985-tracking-events">'+_event_list(filtered,120)+'</div></section>'
        )
        if tab=="email":content+='<div class="v985-tracking-heatmaps">'+heat+'</div>'
    if tab in {"links","files"}:
        try:
            content+=render_tracked_links_v984(
                base,core,request,
                target_filter=("url" if tab=="links" else "stored_file"),
            )
        except Exception as exc:
            content+='<p>Tracked link manager unavailable: '+_e(type(exc).__name__)+'</p>'
    base_params={k:v for k,v in request.query_params.items() if k!="tracking_tab"}
    base_params.update(ui_view="tracking",tracking_tab="links")
    new_link_url="/?"+urlencode(base_params)+"#tracking"
    notice=''.join('<p>'+_e(w)+'</p>' for w in warnings)
    html=(
        '<div class="v985-tracking">'
        '<div class="v985-tracking-head"><div><h2>Tracking</h2>'
        '<p>Time-filtered statistics · email, tracked URLs and files</p></div>'
        '<a href="'+_e(new_link_url)+'" class="v985-tracking-create" data-v960-fragment="tracking">'
        '+ Create link</a></div>'
        +_period_form(request,period)
        +'<p class="v985-tracking-dates">'+_e(
            f'{start.astimezone(_ZONE):%d %b %Y %H:%M} – {finish.astimezone(_ZONE):%d %b %Y %H:%M} Europe/Rome'
        )+'</p>'
        +_tabs(request,tab)+_advanced_filters(request)+content
        +'<div class="v985-tracking-disclaimer">'+notice+'</div>'
        '</div>'
    )
    return _panel("tracking",html)


TRACKING_STYLE=r"""
.v985-tracking{max-width:1600px;margin:auto}
.v985-tracking-head{display:flex;justify-content:space-between;gap:15px;align-items:center;margin:5px 0 18px}
.v985-tracking-head h2{font-size:22px;margin:0;letter-spacing:-.5px}
.v985-tracking-head p{margin:4px 0;font-size:11px;color:var(--v970-muted)}
.v985-tracking-create{background:#345ec2;color:white!important;border-radius:9px;padding:9px 13px;
 font-size:11px;font-weight:800;text-decoration:none;white-space:nowrap}
.v985-tracking-periods{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin:10px 0}
.v985-tracking-periods>strong{color:var(--v970-muted);font-size:11px;margin-right:8px}
.v985-tracking-periods button,.v985-tracking-periods .v985-period{border:1px solid var(--v970-line);border-radius:7px;background:var(--v970-surface);
 color:var(--v970-text);padding:6px 11px;text-decoration:none;display:inline-flex;align-items:center;min-height:33px}
.v985-tracking-periods .v985-period.selected{background:#345ec2!important;border-color:#345ec2!important;color:white!important}
.v985-tracking-periods label{display:flex;gap:4px;align-items:center;font-size:10px}
.v985-tracking-periods input{padding:5px!important;min-height:30px}
.v985-tracking-dates{font-size:10px;color:var(--v970-muted);margin:6px 0 18px}
.v985-tracking-tabs{display:flex;gap:5px;overflow-x:auto;border-bottom:1px solid var(--v970-line);
 margin:0 0 16px;white-space:nowrap}
.v985-tracking-tab{display:block;padding:10px 13px;border-bottom:2px solid transparent;font-size:12px;
 color:var(--v970-muted);text-decoration:none}
.v985-tracking-tab.active{color:var(--v970-accent);border-color:var(--v970-accent);font-weight:750}
.v985-tracking-filters{padding:8px 12px;margin:0 0 15px;border:1px solid var(--v970-line);
 border-radius:9px;background:var(--v970-surface-2)}
.v985-tracking-filters summary{cursor:pointer;font-size:11px;font-weight:700}
.v985-tracking-filters form{margin:10px 0}.v985-tracking-filter-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:8px;margin-bottom:10px}
.v985-tracking-filter-grid label{display:flex;flex-direction:column;gap:4px;font-size:10px}
.v985-tracking-two{display:grid;grid-template-columns:1.02fr .98fr;gap:14px;align-items:start}
.v985-tracking-box{border:1px solid var(--v970-line);border-radius:10px;overflow:hidden;background:var(--v970-surface);min-width:0}
.v985-tracking-box>header{padding:12px 15px;display:flex;justify-content:space-between;gap:10px;
 font-size:16px;font-weight:800;border-bottom:1px solid var(--v970-line)}
.v985-tracking-box>header span{font-size:10px;color:var(--v970-muted);font-weight:500;align-self:center}
.v985-tracking-events{padding:2px 12px 10px}
.v985-tracking .v984-event{border:0!important;border-bottom:1px solid var(--v970-line)!important;border-radius:0!important;
 margin:0!important;padding:13px 4px!important}
.v985-tracking .v984-event:last-child{border-bottom:0!important}
.v985-tracking .v984-event>summary{display:flex;gap:7px;align-items:center;flex-wrap:wrap;font-size:11px;cursor:pointer}
.v985-tracking .v984-event>summary strong{font-size:12px;min-width:90px;color:var(--v970-accent)}
.v985-tracking .v984-event time{color:var(--v970-muted);font-size:10px}
.v985-tracking .v984-detail{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:9px;margin:10px 0;padding:10px;
 border-radius:8px;background:var(--v970-surface-2)}
.v985-tracking .v984-detail>div{overflow-wrap:anywhere;font-size:11px}
.v985-tracking .v984-detail b{display:block;font-size:10px;color:var(--v970-muted)}
.v985-tracking-performance-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:9px;padding:12px}
.v985-tracking-stat{display:flex;flex-direction:column;gap:5px;padding:12px;border:1px solid var(--v970-line);
 background:var(--v970-surface-2);border-radius:9px;min-width:0}
.v985-tracking-stat small{font-size:10px;color:var(--v970-muted)}
.v985-tracking-stat strong{font-size:23px}
.v985-tracking-stat span{font-size:9px;color:var(--v970-muted)}
.v985-tracking-rate-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:9px;padding:0 12px 12px}
.v985-tracking-rate{border:1px solid var(--v970-line);border-radius:9px;padding:12px}
.v985-tracking-rate-head{display:flex;gap:6px;align-items:center;justify-content:space-between}
.v985-tracking-rate-head strong{font-size:11px}
.v985-tracking-rate-head b{font-size:18px}
.v985-tracking-rate-meter{margin:12px 0;background:var(--v970-surface-2);border-radius:99px;height:9px;overflow:hidden}
.v985-tracking-rate-meter span{display:block;height:100%;border-radius:99px}
.v985-r-open{background:#56c4a2}.v985-r-click{background:#df8191}
.v985-tracking-rate p{font-size:10px;color:var(--v970-muted);margin:0}
.v985-tracking-heatmaps{display:grid;gap:12px;margin:16px 0}
.v985-tracking .v984-panel{border:1px solid var(--v970-line);border-radius:10px;background:var(--v970-surface);
 padding:15px;margin:13px 0}
.v985-tracking .v984-panel>h3{font-size:14px;margin:2px 0 14px}
.v985-tracking .v984-heatwrap{overflow-x:auto}
.v985-tracking .v984-grid{display:grid;grid-template-columns:32px repeat(24,minmax(12px,1fr));gap:3px;align-items:center}
.v985-tracking .v984-hd,.v985-tracking .v984-day{font-size:9px;color:var(--v970-muted);text-align:center}
.v985-tracking .v984-heat{height:17px;min-width:12px;border-radius:2px;background:var(--v970-surface-2)}
.v985-tracking .v984-l1{background:#dceeea}.v985-tracking .v984-l2{background:#9bd2c0}
.v985-tracking .v984-l3{background:#53b395}.v985-tracking .v984-l4{background:#218268}
.v985-tracking .v984-hint{font-size:10px;color:var(--v970-muted)}
.v985-tracking .v984-trend-columns{display:flex;gap:4px;align-items:end;height:106px}
.v985-tracking .v984-trend-column{flex:1;display:flex;flex-direction:column;justify-content:end;align-items:center;height:100%}
.v985-tracking .v984-trend-bars{width:min(100%,24px);display:flex;flex-direction:column;overflow:hidden;border-radius:3px}
.v985-tracking .v984-trend-bars span{display:block;width:100%}
.v985-tracking .v984-trend-column small{height:18px;white-space:nowrap;color:var(--v970-muted);font-size:9px}
.v985-tracking .v984-trend-opens{background:#4f91cc}.v985-tracking .v984-trend-clicks{background:#5caf91}
.v985-tracking .v984-trend-files{background:#d19e5f}
.v985-tracking .v984-trend-legend{display:flex;gap:15px;flex-wrap:wrap;font-size:10px;color:var(--v970-muted)}
.v985-tracking .v984-trend-legend i{display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:4px}
.v985-tracking-empty{color:var(--v970-muted);padding:12px}
.v985-tracking-disclaimer{margin:16px 0;padding:12px 14px;border:1px solid var(--v970-line);background:var(--v970-surface-2);
 border-radius:9px;font-size:10px;color:var(--v970-muted)}
.v985-tracking-disclaimer p{margin:6px 0}
@media(max-width:1020px){.v985-tracking-two{grid-template-columns:1fr}}
@media(max-width:660px){.v985-tracking .v984-detail{grid-template-columns:1fr}
.v985-tracking-performance-grid,.v985-tracking-rate-grid{grid-template-columns:1fr}
.v985-tracking-periods label{flex:1}}
"""


def install_tracking_v985(views: Any, shell: Any) -> None:
    views.render_tracking=render_tracking_v985
    old=shell._styles
    if getattr(old,"_postmaster_v985_tracking",False):return
    def styles()->str:
        return old().rstrip()+"\n"+TRACKING_STYLE
    styles._postmaster_v985_tracking=True
    shell._styles=styles
