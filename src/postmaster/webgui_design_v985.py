"""v9.8.5: the approved v5 information architecture, wired to real lazy views.

This module replaces the visual shell, not the production mail/track/file/task
handlers. The existing authenticated lazy route, forms, CSRF, preview/execute
and all 134 MCP tools remain authoritative.
"""
from __future__ import annotations

from html import escape
from typing import Any

from .webgui_v962_views import VIEWS


NAV_GROUPS_V985 = (
    ("Mail", (
        ("inbox", "Inbox", "✉"),
        ("sent", "Sent", "➤"),
        ("drafts", "Drafts", "▤"),
        ("spam", "Spam", "◇"),
    )),
    ("Analytics", (
        ("tracking", "Tracking", "◎"),
        ("files", "Files", "▣"),
        ("scheduler", "Activities", "▦"),
    )),
    ("Organization", (
        ("projects", "Workspace", "◇"),
        ("contacts", "Contacts", "♙"),
        ("knowledge", "Knowledge", "◈"),
    )),
    ("Administration", (
        ("system", "Settings", "⚙"),
        ("coverage", "MCP Catalog", "⌘"),
    )),
)

MORE_VIEWS = (
    ("accounts", "Email accounts", "AC"),
    ("mail-health", "Mail health", "MH"),
    ("deliveries", "Deliveries", "DL"),
    ("suppressions", "Suppressions", "SP"),
    ("security", "Security", "SC"),
    ("amp", "AMP", "AM"),
    ("structured-data", "Structured data", "DB"),
    ("domains", "Domain authorization (legacy)", "DM"),
    ("recipients", "Recipient authorization (legacy)", "RC"),
    ("compose", "Compose (legacy)", "CP"),
)

MAILBOXES = {"inbox": "INBOX", "sent": "Sent", "drafts": "Drafts", "spam": "Spam"}


def _link(key: str, label: str, icon: str, *, mobile: bool = False) -> str:
    if key in MAILBOXES:
        view = "inbox"
        mailbox = MAILBOXES[key]
        href = "/?ui_view=inbox&mailbox=" + mailbox + "#inbox"
        alias = f' data-v985-mailbox="{escape(mailbox, quote=True)}"'
    else:
        view = key
        href = "#" + key
        alias = ""
    css = "v970-mobile-link v985-link" if mobile else "v970-nav-link v985-link"
    return (
        f'<a class="{css}" href="{escape(href, quote=True)}" '
        f'data-v962-nav="{escape(view)}" data-v970-nav="{escape(view)}"{alias}>'
        f'<span class="v970-nav-code v985-icon" aria-hidden="true">{escape(icon)}</span>'
        f'<span class="v985-navlabel">{escape(label)}</span></a>'
    )


def navigation() -> str:
    # Structured Data and future views are registered during runtime composition.
    # Read the live view registry here rather than a stale module-level import.
    from . import webgui_v962 as shell
    live_views = tuple(shell.VIEWS)
    groups = []
    included: set[str] = set()
    for heading, links in NAV_GROUPS_V985:
        group = [f'<div class="v970-nav-group v985-nav-group"><div class="v970-nav-label">{escape(heading)}</div>']
        for key, label, symbol in links:
            included.add("inbox" if key in MAILBOXES else key)
            group.append(_link(key, label, symbol))
        group.append("</div>")
        groups.extend(group)
    rest = [(key, label, icon) for key, label, icon in MORE_VIEWS if key in live_views]
    included.update(key for key, _name, _icon in rest)
    # A release may add a view without updating this file. Never make it unreachable.
    for view in live_views:
        if view not in included:
            rest.append((view, view.replace("-", " ").title(), "•"))
    more = (
        '<details class="v985-more-tools" id="v985-more-tools"><summary>More tools</summary>'
        '<div class="v985-more-items">'
        + "".join(_link(key, label, icon) for key, label, icon in rest)
        + "</div></details>"
    )
    sidebar = (
        '<aside class="v970-sidebar v985-sidebar" aria-label="Primary navigation">'
        '<a href="/?ui_view=inbox#inbox" class="v985-compose" data-v985-compose '
        'data-v962-nav="inbox" aria-label="Compose email"><span aria-hidden="true">✎</span> Compose</a>'
        + "".join(groups) + more
        + '<div class="v985-nav-foot">Postmaster MCP · Connected operational workspace</div></aside>'
    )
    mobile = (
        '<nav class="v970-mobile-nav v985-mobile-nav" aria-label="Mobile primary navigation">'
        + _link("inbox", "Mail", "✉", mobile=True)
        + _link("tracking", "Tracking", "◎", mobile=True)
        + _link("scheduler", "Activities", "▦", mobile=True)
        + _link("contacts", "Contacts", "♙", mobile=True)
        + '<button type="button" data-v970-more aria-expanded="false" aria-controls="v970-more-sheet">'
        '<span class="v970-nav-code" aria-hidden="true">••</span>More</button></nav>'
        '<div class="v970-sheet-backdrop" data-v970-more-close aria-hidden="true"></div>'
        '<section class="v970-more-sheet" id="v970-more-sheet" role="dialog" '
        'aria-modal="true" aria-hidden="true" tabindex="-1" aria-labelledby="v970-more-title">'
        '<div class="v970-more-head"><strong id="v970-more-title">More sections</strong>'
        '<button type="button" data-v970-more-close aria-label="Close menu">×</button></div>'
        '<div class="v970-more-grid">'
        + "".join(_link(k, label, icon, mobile=True) for group, items in NAV_GROUPS_V985
                  for k, label, icon in items if k not in {"inbox", "tracking", "scheduler", "contacts"})
        + "".join(_link(k, label, icon, mobile=True) for k, label, icon in rest)
        + "</div></section>"
    )
    return sidebar + mobile


def contextbar(release_version: str) -> str:
    current = "v" + release_version.lstrip("v")
    return (
        '<header class="v970-contextbar v985-header" aria-label="Postmaster header">'
        '<div class="v985-topbar">'
        '<a class="v985-brand" href="#overview" data-v962-nav="overview" aria-label="Postmaster overview">'
        '<span class="v985-brand-logo" aria-hidden="true">◈</span>'
        'Postmaster <em>MCP</em></a>'
        '<form class="v985-search" method="get" action="/" role="search" aria-label="Search mail">'
        '<input type="hidden" name="ui_view" value="inbox">'
        '<input type="hidden" name="inbox_search" value="1">'
        '<span aria-hidden="true">⌕</span>'
        '<input name="text" placeholder="Search email…" aria-label="Search email messages">'
        '<button type="submit" aria-label="Run mail search">Search</button></form>'
        '<div class="v985-top-actions">'
        f'<span class="v985-release">{escape(current)}</span>'
        '<button class="v970-theme-toggle v985-theme" type="button" '
        'data-v970-theme-toggle aria-label="Toggle light and dark theme" title="Theme">◐</button>'
        '<a href="#overview" data-v962-nav="overview" class="v985-top-home" '
        'title="Overview" aria-label="Go to Overview">▦</a>'
        '</div></div>'
        '<div class="v985-scope-row">'
        '<div class="v970-context-title v985-view-context">'
        '<strong data-v970-context-title>Overview</strong>'
        '<span data-v970-context-subtitle>Current workspace</span></div>'
        '<div class="v970-context-actions"></div>'
        '</div></header>'
    )


DESIGN_STYLE = r"""
/* Approved v5 visual language applied after every legacy and 9.8.4 stylesheet. */
:root,html:not([data-v970-theme]),html[data-v970-theme="light"]{
 --v970-bg:#f8fafc;--v970-sidebar:#f0f4f9;--v970-surface:#ffffff;
 --v970-surface-2:#f8fafc;--v970-line:#dde5f0;--v970-line-strong:#cbd5e4;
 --v970-text:#233047;--v970-muted:#6e7c90;--v970-accent:#345ec2;
 --v970-accent-soft:#e8eefc;--v970-ok:#168565;--v970-warn:#ad762e;
 --v970-danger:#a34545;--v970-radius:10px;--v970-sidebar-w:196px;
 --bg:#f8fafc;--card:#ffffff;--card2:#f8fafc;--text:#233047;
 --muted:#6e7c90;--line:#dde5f0;--accent:#345ec2;--danger:#a34545;--ok:#168565;
 color-scheme:light;
}
html[data-v970-theme="dark"]{
 --v970-bg:#121b27;--v970-sidebar:#121d2b;--v970-surface:#182331;
 --v970-surface-2:#202e3d;--v970-line:#2f4052;--v970-line-strong:#405269;
 --v970-text:#e5edf7;--v970-muted:#a1aec1;--v970-accent:#8ba9ff;
 --v970-accent-soft:#283d5d;--v970-ok:#63cca5;--v970-warn:#e2ad69;
 --v970-danger:#ee9999;--bg:#121b27;--card:#182331;--card2:#202e3d;
 --text:#e5edf7;--muted:#a1aec1;--line:#2f4052;--accent:#8ba9ff;
 --danger:#ee9999;--ok:#63cca5;color-scheme:dark;
}
html,body{font-size:13px;line-height:1.5}
body{background:var(--v970-surface)!important;color:var(--v970-text)}
.shell{display:grid!important;grid-template-columns:var(--v970-sidebar-w) minmax(0,1fr)!important;grid-template-rows:106px minmax(0,1fr);min-height:100vh}
.v970-workspace{display:contents!important}
.v985-header{position:sticky!important;top:0!important;grid-column:1/-1;grid-row:1;z-index:60!important;
 min-height:106px!important;height:106px;padding:0!important;display:flex!important;
 flex-direction:column;align-items:stretch!important;gap:0!important;background:var(--v970-surface)!important;
 border-bottom:1px solid var(--v970-line)!important;backdrop-filter:none!important}
.v985-topbar{height:57px;min-height:57px;display:flex;align-items:center;gap:16px;padding:0 18px;border-bottom:1px solid var(--v970-line)}
.v985-brand{font-size:18px;font-weight:850;letter-spacing:-.5px;text-decoration:none;color:var(--v970-text);
 white-space:nowrap;min-width:176px;display:flex;align-items:center;gap:7px}
.v985-brand em{font-style:normal;color:var(--v970-accent)}
.v985-brand-logo{font-size:20px;color:var(--v970-accent)}
.v985-search{display:flex;align-items:center;gap:8px;width:min(460px,100%);margin:auto;border:1px solid var(--v970-line);
 background:var(--v970-surface-2);border-radius:9px;padding:4px 8px;color:var(--v970-muted)}
.v985-search input{flex:1;min-width:40px;border:0!important;box-shadow:none!important;background:transparent!important;
 min-height:30px;padding:4px!important;outline:none;font-size:13px;color:var(--v970-text)}
.v985-search button{font-size:11px;min-height:27px!important;padding:4px 9px!important;background:var(--v970-accent-soft);color:var(--v970-accent);border-radius:6px}
.v985-top-actions{display:flex;align-items:center;gap:9px;white-space:nowrap}
.v985-release{font-size:11px;color:var(--v970-muted)}
.v985-theme,.v985-top-home{border:0!important;background:transparent!important;font-size:19px;color:var(--v970-muted)!important;
 display:grid;place-items:center;text-decoration:none;width:34px;height:34px;padding:0!important;border-radius:7px!important}
.v985-theme:hover,.v985-top-home:hover{background:var(--v970-accent-soft)!important}
.v985-scope-row{display:flex;align-items:center;justify-content:flex-end;gap:10px;min-height:48px;padding:4px 20px}
.v985-view-context{display:none!important}
.v985-scope-row>.v970-context-actions{display:none!important}
.v985-header .v984-scopebar{display:flex;align-items:center;gap:12px;flex-wrap:wrap;padding:0!important;
 background:transparent!important;border:0!important;justify-content:flex-end}
.v985-header .v984-scopebar label{font-size:10px;color:var(--v970-muted);font-weight:700;display:flex;gap:4px;flex-direction:column}
.v985-header .v984-scopebar select{font-size:11px;min-width:118px;max-width:215px;min-height:31px;
 border:1px solid var(--v970-line)!important;padding:5px 8px!important;border-radius:7px}
.v985-sidebar{grid-column:1;grid-row:2;position:sticky!important;top:106px!important;
 height:calc(100vh - 106px)!important;padding:10px 9px 15px!important;overflow-y:auto!important;
 background:var(--v970-sidebar)!important;box-shadow:none;border-right:1px solid var(--v970-line)}
.v985-compose{display:flex;align-items:center;justify-content:center;gap:8px;background:#345ec2;color:white!important;
 text-decoration:none;font-weight:800;border-radius:9px;padding:10px;margin:6px 1px 12px}
.v985-compose:hover{background:#234ba5;color:#fff!important}
.v985-nav-group{margin-top:7px!important}
.v985-nav-group .v970-nav-label{font-size:10px!important;letter-spacing:.07em!important;
 padding:11px 10px 5px!important;text-transform:uppercase;color:var(--v970-muted)!important}
.v985-sidebar .v985-link{font-size:13px!important;font-weight:500!important;min-height:37px!important;
 padding:8px 10px!important;border:0!important;border-radius:8px!important;gap:9px!important;line-height:1.35}
.v985-sidebar .v985-icon{background:transparent!important;border:0!important;font-size:16px!important;
 min-width:20px!important;width:20px!important;height:20px!important;color:inherit!important}
.v985-sidebar .v985-link:hover,.v985-sidebar .v985-link.active{
 background:var(--v970-accent-soft)!important;color:var(--v970-accent)!important}
.v985-sidebar .v985-link.active{font-weight:800!important}
.v985-sidebar .v985-link[data-v985-mailbox].active:not(.v985-folder-active){background:transparent!important;
 color:var(--v970-muted)!important;font-weight:500!important}
.v985-sidebar .v985-link.v985-folder-active{background:var(--v970-accent-soft)!important;
 color:var(--v970-accent)!important;font-weight:800!important}
.v985-more-tools{margin-top:9px;padding:4px 0;border-top:1px solid var(--v970-line)}
.v985-more-tools>summary{color:var(--v970-muted);padding:10px;font-size:12px;cursor:pointer}
.v985-more-items{display:grid;gap:2px}.v985-nav-foot{font-size:10px;margin-top:25px;padding:10px;color:var(--v970-muted);
 border-top:1px solid var(--v970-line)}
.v970-workspace>main{grid-column:2;grid-row:2;min-height:calc(100vh - 106px)!important;
 padding:18px 23px 55px!important;max-width:none!important;width:100%;background:var(--v970-surface);
 overflow-x:hidden}
.v970-workspace>main .v962-perfline{font-size:9px;opacity:.65}
.v951-pagehead h2,.panel-title h2,.v963-inbox-head h2{font-size:22px!important;letter-spacing:-.5px!important}
.v951-pagehead p{font-size:11px;color:var(--v970-muted)}
.card,.v962-collapsible,.v963-detail,.v960-reader,.v963-safe-email,.v960-health-columns>section{
 background:var(--v970-surface)!important;border:1px solid var(--v970-line)!important;
 border-radius:10px!important;box-shadow:none!important}
.card{padding:14px!important;margin:12px 0!important}
.grid,.v951-grid{gap:13px!important}
.v951-metrics{display:grid!important;grid-template-columns:repeat(auto-fit,minmax(155px,1fr))!important;gap:10px!important;
 border:0!important;border-radius:0!important;margin:15px 0!important;overflow:visible!important}
.v951-metric{border:1px solid var(--v970-line)!important;border-radius:10px!important;background:var(--v970-surface-2)!important;padding:12px!important}
.v951-metric strong{font-size:23px!important}
button,input,select,textarea{border-radius:8px!important}
button{min-height:33px!important}
button.primary,.btn.primary{background:#345ec2!important;color:white!important;border-color:#345ec2!important}
.v951-toolbar{background:var(--v970-surface-2)!important;border-radius:10px!important;padding:10px!important;flex-wrap:wrap!important}
.v960-mailbox-tabs a.active{border-bottom:2px solid var(--v970-accent);color:var(--v970-accent)}
.scroll{border-radius:10px!important}
table{font-size:12px}td,th{padding:10px 11px!important}th{font-size:10px!important}
#panel-tracking .v984-panel{border-radius:10px!important}
#panel-tracking .v984-stat{background:var(--v970-surface-2)!important;border-radius:10px!important}
#panel-tracking .v984-stats{gap:10px!important}
#panel-tracking .v984-period{border-radius:8px!important;padding:8px 11px!important}
#panel-tracking .v984-period.active{background:#345ec2!important;color:white!important}
#panel-scheduler .task-calendar-day{min-height:90px}
#panel-scheduler .task-calendar-shell{border-radius:10px}
#panel-contacts .v984-contact-tab{border-radius:7px}
#panel-files .v962-collapsible{margin-bottom:14px}
.v970-mobile-nav,.v970-more-sheet,.v970-sheet-backdrop{display:none}
/* Keep the approved light/dark language while preserving the v970 mobile menu/focus mechanics. */
@media(max-width:1090px){
 .v985-brand{min-width:0}
 .v985-topbar{gap:9px;padding:0 12px}
 .v985-sidebar{width:100%}
 .v970-workspace>main{padding:14px 16px 45px!important}
}
@media(max-width:800px){
 .shell{grid-template-columns:60px minmax(0,1fr)!important}
 .v985-sidebar{display:block!important;padding:8px 5px!important}
 .v985-compose{padding:10px 3px;font-size:0}.v985-compose span{font-size:21px}
 .v985-sidebar .v985-navlabel,.v985-sidebar .v970-nav-label,.v985-sidebar .v985-more-tools,.v985-nav-foot{display:none!important}
 .v985-sidebar .v985-link{justify-content:center!important;padding:10px 2px!important}
 .v985-topbar .v985-brand{font-size:15px}
 .v985-header .v984-scopebar{gap:5px}
 .v985-header .v984-scopebar select{min-width:85px;max-width:132px}
 .v970-workspace>main{padding:12px!important}
}
@media(max-width:560px){
 body{padding-bottom:67px!important}
 .shell{display:block!important}
 .v985-sidebar{display:none!important}
 .v985-header{height:auto!important;min-height:116px!important;position:sticky!important}
 .v985-topbar{min-height:56px;height:56px;gap:7px;padding:0 8px}
 .v985-brand{font-size:13px!important;letter-spacing:-.4px}
 .v985-brand-logo{display:none}
 .v985-release{display:none}
 .v985-search{min-width:65px;flex:1}
 .v985-search button{font-size:0;padding:4px 8px!important}
 .v985-search button::before{content:"⌕";font-size:15px}
 .v985-top-actions{gap:3px}
 .v985-theme,.v985-top-home{width:27px;height:29px;font-size:17px}
 .v985-scope-row{padding:5px 7px;min-height:60px}
 .v985-header .v984-scopebar{width:100%!important;justify-content:space-between}
 .v985-header .v984-scopebar label{flex:1;min-width:0;font-size:9px}
 .v985-header .v984-scopebar select{width:100%;min-width:0;max-width:100%;font-size:11px}
 .v970-workspace{display:block!important}
 .v970-workspace>main{padding:13px 11px 78px!important;min-height:calc(100vh - 116px)!important}
 .v970-mobile-nav{display:grid!important;z-index:75}
 .v970-more-sheet.v970-open,.v970-sheet-backdrop.v970-open{display:block!important}
 .v951-pagehead h2,.panel-title h2{font-size:19px!important}
 .v951-metrics{grid-template-columns:repeat(2,minmax(0,1fr))!important}
 .v984-tracking .v984-detail{grid-template-columns:1fr}
 #panel-scheduler .task-calendar-day{min-height:60px}
}
@media(prefers-reduced-motion:reduce){.v985-link,.v985-compose{transition:none!important}}
"""

DESIGN_SCRIPT = r"""<script id="v985-production-ui">
(() => {
 'use strict';
 const folders={INBOX:'inbox',Sent:'sent',Drafts:'drafts',Spam:'spam'};
 let composePending=false;
 const scope = document.querySelector('#v984-scopebar');
 function currentFolder(){
   const p=new URL(location.href).searchParams;
   return (p.get('mailbox')||'INBOX').toLowerCase();
 }
 function updateAliases(){
   const active=document.querySelector('.tab-panel.active')?.dataset.panel||'overview';
   const current=currentFolder();
   document.querySelectorAll('[data-v985-mailbox]').forEach(a=>{
     const chosen=active==='inbox'&&a.dataset.v985Mailbox.toLowerCase()===current;
     a.classList.toggle('v985-folder-active',chosen);
     if(chosen) a.setAttribute('aria-current','page');
     else a.removeAttribute('aria-current');
   });
 }
 function revealCompose(){
   if(!composePending) return;
   const container=document.querySelector('#panel-inbox.active');
   const details=container?.querySelector('details.v971-floating-compose,details#v960-compose');
   if(!details) return;
   details.open=true;
   composePending=false;
   details.scrollIntoView({block:'start',behavior:'auto'});
   const field=details.querySelector('input[name="to"]');
   field?.focus({preventScroll:true});
 }
 document.addEventListener('click',event=>{
   const a=event.target.closest('[data-v985-compose]');
   if(a){composePending=true;queueMicrotask(revealCompose);}
   const link=event.target.closest('[data-v985-mailbox]');
   if(link){
     // v962 handles this ordinary fragment navigation; preserve the query.
     queueMicrotask(updateAliases);
   }
 });
 const observer=new MutationObserver(()=>{updateAliases();revealCompose();});
 const main=document.querySelector('.v970-workspace > main');
 if(main)observer.observe(main,{subtree:false,childList:true});
 window.addEventListener('popstate',updateAliases);
 window.addEventListener('hashchange',updateAliases);
 updateAliases();
})();
</script>"""


def install_webgui_design_v985(v962: Any, v970: Any, version: str) -> None:
    """Make approved v5 shell the production default; retain all lazy fragments."""
    previous_styles = v962._styles
    if getattr(previous_styles, "_postmaster_v985_styles", False):
        return

    def styles() -> str:
        return previous_styles().rstrip() + "\n" + DESIGN_STYLE

    styles._postmaster_v985_styles = True
    v962._styles = styles
    v962._nav = navigation
    v970._context_bar = lambda: contextbar(version)
    if 'id="v985-production-ui"' not in v962.SCRIPT:
        v962.SCRIPT += DESIGN_SCRIPT

    labels = {
        "overview": ("Overview", "Recent activity, performance and release status"),
        "inbox": ("Mail", "Inbox, Sent, Drafts, Spam and safe reader"),
        "tracking": ("Tracking", "Period-scoped analytics, events, URLs and files"),
        "files": ("Files", "Stored File previews and explicit downloads"),
        "scheduler": ("Activities", "Task registry and real calendar"),
        "projects": ("Workspace", "Owner and project organization"),
        "contacts": ("Contacts", "Address book, Inbox review and sending policy"),
        "knowledge": ("Knowledge", "Memories and skills"),
        "system": ("Settings", "Updates, configuration and service status"),
        "coverage": ("MCP Catalog", "Connected tool capability catalog"),
    }
    v970.VIEW_LABELS.update(labels)
