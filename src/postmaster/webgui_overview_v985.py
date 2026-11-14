"""Approved v5 Overview, backed by live Postmaster data rather than demo objects."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from html import escape
from typing import Any

from starlette.requests import Request

from . import webgui_v962_perf
from .tracking_geoip_v984 import global_tracking_network_events
from .update_status import latest_version_status
from .webgui_tracking_v984 import _period
from .webgui_v962_views import _panel


def _e(value: Any) -> str:
    return escape(str(value if value is not None else "—"), quote=True)


def _metric(label: str, number: Any, note: str) -> str:
    return (
        '<div class="v985-overview-metric"><span>'+_e(label)+'</span>'
        '<strong>'+_e(number)+'</strong><small>'+_e(note)+'</small></div>'
    )


def _version_card(label: str, version: str, status: str) -> str:
    return (
        '<div class="v985-version-card"><span>'+_e(label)+'</span>'
        '<strong>'+_e(version)+'</strong><small>'+_e(status)+'</small></div>'
    )


def render_overview_v985(base: Any, request: Request) -> str:
    from .webgui_tracking_v984 import _ZONE
    since, until, selected=_period(request)
    owner=str(request.query_params.get("owner_id") or "").strip()
    project=str(request.query_params.get("project_id") or request.query_params.get("project") or "").strip()
    account=str(request.query_params.get("account_id") or request.query_params.get("account") or "").strip()
    try:
        build=base.build_status()
        installed=str(build.get("version") or "unknown") if isinstance(build,dict) else "unknown"
    except Exception:
        installed="unknown"
    release=latest_version_status(installed)
    latest=str(release.get("latest_version") or "unavailable")
    update=release.get("update_available")
    current_status=("Update available" if update is True else
                    "Up to date" if update is False else "Release check unavailable")
    latest_status=("Latest published stable release" if latest!="unavailable" else "Release check unavailable")
    try:
        events=global_tracking_network_events(
            base.analytics_store(), account_id=account or None,
            owner_id=owner or None, project_id=project or None,
            since=since.isoformat(),until=until.isoformat(),limit=500,offset=0,
        )["events"]
        events_text=str(len(events))+("+" if len(events)>=500 else "")
    except Exception:
        events_text="—"
    try:
        files=base.file_store().list_files(
            owner_id=owner or None, project_id=project or None,
            include_global=not bool(project),limit=1000,
        )
        files_text=str(len(files))+("+" if len(files)==1000 else "")
    except Exception:
        files_text="—"
    try:
        owner_rows=webgui_v962_perf.owners(base)
        owner_ids=[str(x.get("id") or "") for x in owner_rows if x.get("id")]
        if owner: owner_ids=[owner] if owner in owner_ids else []
        links_count=0
        more_links=False
        for oid in owner_ids:
            result=base.list_tracked_links(owner_id=oid,
                        project_id=project or None,active_only=False,limit=500)
            if isinstance(result,dict) and result.get("ok"):
                rows=result.get("links") or []
                links_count+=len(rows)
                more_links=more_links or len(rows)>=500
        links_text=str(links_count)+("+" if more_links else "")
    except Exception:
        links_text="—"
    unread="—"
    unread_note="Choose an email account"
    if account:
        try:
            rows=base.mail_client(account).search_emails(
                mailbox="INBOX",unread_only=True,
                since_days=3650,limit=100,
            )
            if isinstance(rows,list):
                unread=str(len(rows))+("+" if len(rows)>=100 else "")
                unread_note="Recent unread messages (limit 100)"
        except Exception:
            unread_note="Inbox unavailable"
    # The date selector applies to tracking activity. File/link counts are snapshots.
    note=("Tracking events are observed network requests, not verified human reads. "
          "Event data is restricted to the selected time range; files and links are inventories. "
          "Counts with + are bounded previews.")
    duration=(
        f'{since.astimezone(_ZONE):%d %b %Y} – {until.astimezone(_ZONE):%d %b %Y}'
    )
    options=(("24h","24 hours"),("7d","7 days"),("28d","28 days"),
             ("90d","90 days"),("custom","Custom"))
    periods="".join(
        '<button type="submit" name="period" value="'+key+'" class="v985-period'
        +(" selected" if selected==key else "")+'">'+label+'</button>'
        for key,label in options
    )
    date1=since.astimezone(_ZONE).date().isoformat()
    date2=(until.astimezone(_ZONE)-timedelta(days=1)).date().isoformat()
    form=(
        '<form method="get" action="/" class="v985-periods">'
        '<input type="hidden" name="ui_view" value="overview">'
        +''.join(
            '<input type="hidden" name="'+name+'" value="'+_e(value)+'">'
            for name,value in (("owner_id",owner),("project_id",project),("account_id",account)) if value
        )
        +periods
        +'<label>From <input name="from" type="date" value="'+date1+'"></label>'
        +'<label>To <input name="to" type="date" value="'+date2+'"></label>'
        +'<button type="submit">Apply</button></form>'
    )
    link_items=(("Mail","inbox","Open"),("Tracking","tracking","Open"),
                ("File Store","files","Open"),("Activities","scheduler","Open"),
                ("Workspace","projects","Open"),("Contacts","contacts","Open"))
    quick="".join(
        '<div class="v985-quick-row"><strong>'+title+'</strong>'
        '<a href="#'+view+'" data-v962-nav="'+view+'">'+action+'</a></div>'
        for title,view,action in link_items
    )
    html=(
        '<div class="v985-overview">'
        '<div class="v985-pagehead"><div><h2>Overview</h2>'
        '<p>Operational scope · all owners/projects/accounts by default</p></div>'
        '<a class="v985-link-button" href="#system" data-v962-nav="system">Version settings</a></div>'
        '<div class="v985-version-grid">'
        +_version_card("Installed version","v"+installed.lstrip("v"),current_status)
        +_version_card("Latest available stable","v"+latest.lstrip("v") if latest!="unavailable" else latest,latest_status)
        +'</div>'
        '<p class="v985-source-note">Version information comes from the running build and published stable GitHub releases.</p>'
        +form
        +'<div class="v985-overview-metrics">'
        +_metric("Unread",unread,unread_note)
        +_metric("Tracking events",events_text,"Observed events in range")
        +_metric("Stored files",files_text,"Owner/project inventory")
        +_metric("Tracked links",links_text,"Owner/project inventory")
        +'</div>'
        '<section class="v985-quick-access"><header>Quick access</header>'
        +quick+'</section>'
        '<p class="v985-source-note">'+_e(note)+' Range: '+_e(duration)+'</p>'
        '</div>'
    )
    return _panel("overview",html)


OVERVIEW_STYLE=r"""
.v985-overview{max-width:1500px;margin:auto}
.v985-pagehead{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px;margin:5px 0 17px}
.v985-pagehead h2{font-size:22px;margin:0;letter-spacing:-.5px}
.v985-pagehead p{color:var(--v970-muted);font-size:11px;margin:4px 0}
.v985-link-button{color:var(--v970-text);background:var(--v970-surface);border:1px solid var(--v970-line);
 border-radius:8px;padding:8px 12px;text-decoration:none;font-size:12px;font-weight:700}
.v985-version-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;margin:14px 0 10px}
.v985-version-card,.v985-overview-metric{background:var(--v970-surface-2);border:1px solid var(--v970-line);
 border-radius:10px;padding:16px;min-width:0;display:flex;flex-direction:column;align-items:flex-start;gap:5px}
.v985-version-card span,.v985-overview-metric span{color:var(--v970-muted);font-size:11px}
.v985-version-card strong{font-size:26px;letter-spacing:-.5px;line-height:1.2}
.v985-version-card small{font-size:10px;background:var(--v970-accent-soft);color:var(--v970-accent);padding:3px 9px;border-radius:99px}
.v985-source-note{color:var(--v970-muted);font-size:10px;margin:9px 0 13px}
.v985-overview-metrics{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:11px;margin:15px 0}
.v985-overview-metric{padding:13px}.v985-overview-metric strong{font-size:24px;line-height:1.3}
.v985-overview-metric small{font-size:10px;color:var(--v970-muted)}
.v985-quick-access{border:1px solid var(--v970-line);border-radius:10px;overflow:hidden;margin:15px 0}
.v985-quick-access>header{background:var(--v970-surface-2);padding:12px 14px;font-size:13px;font-weight:800;border-bottom:1px solid var(--v970-line)}
.v985-quick-row{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:12px 14px;
 border-bottom:1px solid var(--v970-line);min-height:48px}
.v985-quick-row:last-child{border-bottom:0}.v985-quick-row strong{font-size:12px}
.v985-quick-row a{text-decoration:none;color:var(--v970-text);font-size:11px;border:1px solid var(--v970-line);
 padding:6px 10px;border-radius:7px}
.v985-quick-row a:hover{background:var(--v970-accent-soft);color:var(--v970-accent)}
.v985-periods{display:flex;align-items:center;flex-wrap:wrap;gap:6px;margin:10px 0 16px}
.v985-period{border:1px solid var(--v970-line)!important;border-radius:8px!important;
 background:var(--v970-surface)!important;color:var(--v970-text)!important;padding:7px 11px!important}
.v985-period.selected{background:#345ec2!important;border-color:#345ec2!important;color:white!important}
.v985-periods label{display:flex;align-items:center;gap:6px}.v985-periods input{padding:5px!important;min-height:31px}
@media(max-width:900px){.v985-overview-metrics{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:570px){.v985-version-grid{grid-template-columns:1fr}.v985-overview-metrics{gap:7px}}
"""


def install_overview_v985(views: Any, v962: Any) -> None:
    views.render_overview=render_overview_v985
    previous=v962._styles
    if getattr(previous,"_postmaster_v985_overview",False):return
    def styles()->str:
        return previous().rstrip()+"\n"+OVERVIEW_STYLE
    styles._postmaster_v985_overview=True
    v962._styles=styles
