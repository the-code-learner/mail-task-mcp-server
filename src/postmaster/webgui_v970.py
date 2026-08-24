from __future__ import annotations

from html import escape
import json
from typing import Any

from starlette.responses import HTMLResponse


# Presentation-only v9.7.0 overlay. It receives the v9.6.2 shell module only:
# no Starlette app object, no backend stores and no MCP registry.
NAV_GROUPS = (
    ("Operate", (
        ("overview", "Dashboard", "DB"),
        ("inbox", "Inbox / Sent", "ML"),
        ("compose", "Compose", "CP"),
        ("tracking", "Tracking", "TR"),
        ("deliveries", "Deliveries", "DL"),
    )),
    ("Manage", (
        ("accounts", "Accounts", "AC"),
        ("mail-health", "Mail Health", "MH"),
        ("suppressions", "Suppressions", "SP"),
        ("projects", "Projects", "PJ"),
        ("knowledge", "Knowledge", "KN"),
        ("files", "Files", "FL"),
        ("scheduler", "Tasks", "TS"),
    )),
    ("Control", (
        ("security", "Security", "SC"),
        ("domains", "Domain controls", "DM"),
        ("recipients", "Recipient controls", "RC"),
        ("amp", "AMP", "AM"),
        ("system", "System", "SY"),
        ("coverage", "MCP Coverage", "MC"),
    )),
)

VIEW_LABELS = {
    "overview": ("Dashboard", "Operational posture and system signals"),
    "inbox": ("Mail", "Inbox, Sent, reader and privacy context"),
    "compose": ("Compose", "Existing send, draft and thread operations"),
    "tracking": ("Tracking", "Campaign and delivery observability"),
    "deliveries": ("Deliveries", "Delivery-level operational records"),
    "accounts": ("Accounts", "Sender accounts and connection state"),
    "mail-health": ("Mail Health", "Inbound and outbound mail posture"),
    "suppressions": ("Suppressions", "Recipient reliability controls"),
    "projects": ("Projects", "Project scopes and operational grouping"),
    "knowledge": ("Knowledge", "Memories, skills and scoped context"),
    "files": ("Files", "Persistent File Store"),
    "scheduler": ("Tasks", "Registry list and calendar views"),
    "security": ("Security", "Privacy Proxy and security posture"),
    "domains": ("Domain controls", "Authorized domain policy"),
    "recipients": ("Recipient controls", "Exact-address authorization policy"),
    "amp": ("AMP", "AMP-for-Email account capability"),
    "system": ("System", "Runtime and service status"),
    "coverage": ("MCP Coverage", "Current MCP surface coverage"),
}

ENTERPRISE_STYLE = r'''
/* webgui-v970-enterprise-operational-refresh */
:root{
  color-scheme:dark;
  --v970-bg:#0b0f14;--v970-sidebar:#0d1218;--v970-surface:#11171f;
  --v970-surface-2:#151d27;--v970-line:#263240;--v970-line-strong:#344355;
  --v970-text:#e8eef5;--v970-muted:#92a0b1;--v970-accent:#5e9cff;
  --v970-accent-soft:rgba(94,156,255,.12);--v970-ok:#54c982;
  --v970-warn:#e6ad53;--v970-danger:#e66c75;--v970-radius:8px;
  --v970-sidebar-w:236px;--v970-context-h:58px;
  --bg:var(--v970-bg);--card:var(--v970-surface);--card2:var(--v970-surface-2);
  --surface:var(--v970-surface);--line:var(--v970-line);--border:var(--v970-line);
  --text:var(--v970-text);--muted:var(--v970-muted);--accent:var(--v970-accent);
}
html[data-v970-theme="light"]{
  color-scheme:light;--v970-bg:#f3f6f9;--v970-sidebar:#f8fafc;--v970-surface:#fff;
  --v970-surface-2:#f6f8fb;--v970-line:#d8e0e8;--v970-line-strong:#c2cdd8;
  --v970-text:#17202b;--v970-muted:#657385;--v970-accent:#2f6fdb;
  --v970-accent-soft:rgba(47,111,219,.09);--v970-ok:#278a55;
  --v970-warn:#a56b14;--v970-danger:#c14855;
}
@media(prefers-color-scheme:light){
  html:not([data-v970-theme]){
    color-scheme:light;--v970-bg:#f3f6f9;--v970-sidebar:#f8fafc;--v970-surface:#fff;
    --v970-surface-2:#f6f8fb;--v970-line:#d8e0e8;--v970-line-strong:#c2cdd8;
    --v970-text:#17202b;--v970-muted:#657385;--v970-accent:#2f6fdb;
    --v970-accent-soft:rgba(47,111,219,.09);--v970-ok:#278a55;
    --v970-warn:#a56b14;--v970-danger:#c14855;
  }
}
*{box-sizing:border-box;scrollbar-color:var(--v970-line-strong) transparent}
html,body{min-height:100%;background:var(--v970-bg);color:var(--v970-text)}
body{margin:0;font-size:13px;line-height:1.42;letter-spacing:.002em}
body,button,input,select,textarea{font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
a{color:var(--v970-accent)}:focus-visible{outline:2px solid var(--v970-accent);outline-offset:2px}
.shell{display:grid;grid-template-columns:var(--v970-sidebar-w) minmax(0,1fr);min-height:100vh;background:var(--v970-bg)}
.v962-nav{display:none!important}
.v970-sidebar{position:sticky;top:0;height:100vh;overflow:auto;display:flex;flex-direction:column;padding:12px 10px 10px;border-right:1px solid var(--v970-line);background:var(--v970-sidebar)}
.v970-brand{display:flex;align-items:center;gap:9px;padding:6px 7px 12px;border-bottom:1px solid var(--v970-line);margin-bottom:6px}
.v970-mark{display:grid;place-items:center;width:29px;height:29px;border:1px solid var(--v970-line-strong);border-radius:7px;background:var(--v970-surface-2);font-weight:850;font-size:11px}
.v970-brand-copy strong{display:block;font-size:14px;line-height:1.2}.v970-brand-copy small{display:block;color:var(--v970-muted);font-size:10px;margin-top:2px}
.v970-nav-group{margin-top:6px}.v970-nav-label{padding:8px 8px 4px;color:var(--v970-muted);font-size:9px;font-weight:800;text-transform:uppercase;letter-spacing:.1em}
.v970-sidebar a.v970-nav-link{display:flex;align-items:center;gap:8px;min-height:34px;padding:6px 8px;margin:1px 0;border:1px solid transparent;border-radius:7px;text-decoration:none;color:var(--v970-muted);font-weight:620}
.v970-sidebar a.v970-nav-link:hover{background:var(--v970-surface-2);border-color:var(--v970-line);color:var(--v970-text)}
.v970-sidebar a.v970-nav-link.active{background:var(--v970-accent-soft);border-color:color-mix(in srgb,var(--v970-accent) 42%,var(--v970-line));color:var(--v970-text)}
.v970-nav-code{display:grid;place-items:center;min-width:25px;height:21px;border:1px solid var(--v970-line);border-radius:5px;background:var(--v970-surface);font-size:9px;font-weight:850;color:var(--v970-muted)}
.v970-nav-link.active .v970-nav-code{color:var(--v970-accent);border-color:color-mix(in srgb,var(--v970-accent) 45%,var(--v970-line))}
.v970-sidebar-foot{margin-top:auto;padding:10px 7px 2px;border-top:1px solid var(--v970-line);font-size:10px;color:var(--v970-muted)}
.v970-workspace{min-width:0;display:flex;flex-direction:column;min-height:100vh}
.v970-contextbar{position:sticky;top:0;z-index:30;min-height:var(--v970-context-h);display:flex;align-items:center;justify-content:space-between;gap:16px;padding:8px 18px;border-bottom:1px solid var(--v970-line);background:color-mix(in srgb,var(--v970-bg) 92%,transparent);backdrop-filter:blur(10px)}
.v970-context-title strong{display:block;font-size:14px;line-height:1.2}.v970-context-title span{display:block;color:var(--v970-muted);font-size:10px;margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.v970-context-actions{display:flex;align-items:center;gap:6px}.v970-theme-toggle{min-width:36px}
.v970-workspace>main{width:100%;min-width:0;max-width:none;margin:0;padding:14px 18px 42px}

/* Mature, dense surfaces: tables and workspaces first, cards secondary. */
.card,.v962-collapsible,.v963-detail,.v960-reader,.v963-safe-email,.v960-health-columns>section{border-color:var(--v970-line)!important;background:var(--v970-surface)!important;border-radius:var(--v970-radius)!important;box-shadow:none!important}
.card{padding:12px;margin:8px 0}.grid,.v951-grid{gap:9px}
.panel-title,.v951-pagehead,.v963-inbox-head{gap:10px;align-items:center}
.panel-title h2,.v951-pagehead h2,.v963-inbox-head h2{font-size:17px;letter-spacing:-.01em}
.panel-title h3,.v963-detail h3{font-size:14px}.muted,label,.small{color:var(--v970-muted)}
.notice,.flash{border-radius:6px;background:var(--v970-surface-2);border-color:var(--v970-line)}
button,.btn,input,select,textarea{border-color:var(--v970-line-strong);border-radius:6px;background:var(--v970-surface-2);color:var(--v970-text)}
button,.btn{min-height:32px;padding:5px 9px;font-weight:650}
button.primary,.btn.primary{background:var(--v970-accent)!important;border-color:var(--v970-accent)!important;color:white!important}
input,select,textarea{padding:7px 8px}input[type="checkbox"],input[type="radio"]{accent-color:var(--v970-accent)}
.badge,.v963-chip,.project-scope{border-radius:999px;background:var(--v970-surface-2);border-color:var(--v970-line);font-size:10px}
.v951-toolbar{margin:8px 0;padding:8px;border-radius:var(--v970-radius);border-color:var(--v970-line);background:var(--v970-surface);align-items:end}
.v951-metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(132px,1fr));gap:0;margin:8px 0;border:1px solid var(--v970-line);border-radius:var(--v970-radius);overflow:hidden}
.v951-metric{margin:0;border:0;border-right:1px solid var(--v970-line);border-radius:0;background:var(--v970-surface);padding:9px 11px}.v951-metric:last-child{border-right:0}.v951-metric strong{font-size:18px;margin:2px 0}
.project-summary{gap:0;border:1px solid var(--v970-line);border-radius:var(--v970-radius);overflow:hidden}.project-summary>div{border:0;border-right:1px solid var(--v970-line);border-radius:0;background:var(--v970-surface);padding:8px 10px}

.scroll{overflow:auto;border:1px solid var(--v970-line);border-radius:var(--v970-radius);background:var(--v970-surface);overscroll-behavior:contain}.scroll table{min-width:720px}
table{width:100%;border-collapse:collapse;font-size:12px}th{position:sticky;top:0;z-index:2;background:var(--v970-surface-2);color:var(--v970-muted);font-size:9px;text-transform:uppercase;letter-spacing:.065em;font-weight:800}
th,td{padding:7px 9px;border-bottom:1px solid var(--v970-line);vertical-align:top}tbody tr:last-child td{border-bottom:0}tbody tr:hover td{background:color-mix(in srgb,var(--v970-accent) 4%,var(--v970-surface))}
.actions{gap:4px}.actions button{min-height:28px;padding:3px 7px;font-size:11px}.mono,code{font-size:.94em}

/* Inbox/Sent operational workspace. Existing renderer/controls stay intact. */
#panel-inbox>.v963-inbox-head{padding:2px 0 4px}.v960-mailbox-tabs{margin:5px 0 8px;padding-bottom:4px;border-bottom:1px solid var(--v970-line)}
.v960-mailbox-tabs a{border:0;border-bottom:2px solid transparent;border-radius:0;padding:6px 8px}.v960-mailbox-tabs a.active{background:transparent;border-color:var(--v970-accent);color:var(--v970-text)}
.v960-mail-table{table-layout:fixed}.v960-mail-table th:first-child,.v960-mail-table td:first-child{width:34px}.v960-mail-table th:last-child,.v960-mail-table td:last-child{width:160px}
.v963-mail-row.unread td{background:color-mix(in srgb,var(--v970-accent) 5%,var(--v970-surface))}
.v963-detail{margin:10px 0;padding:13px}.v963-detail-actions{margin:9px 0}.v963-safe-email{padding:14px;min-height:180px;line-height:1.55}
.v963-tech{margin-top:10px;border-top:1px solid var(--v970-line);padding-top:7px}.v963-proxy-card{border-left:1px solid var(--v970-line)!important}
.v963-warning{border-radius:var(--v970-radius);border-color:color-mix(in srgb,var(--v970-warn) 55%,var(--v970-line));background:color-mix(in srgb,var(--v970-warn) 8%,var(--v970-surface))}
.v963-full-frame{border-radius:var(--v970-radius);min-height:480px}.v963-thread-compose{border-left:2px solid var(--v970-accent)!important}

/* Knowledge, Tasks, Tracking, Security, System. */
#panel-knowledge .v962-collapsible{margin:7px 0}#panel-knowledge .v962-collapsible>summary{padding:9px 11px}
#panel-knowledge textarea{resize:vertical}.v962-collapsible>summary{padding:9px 11px}.v962-collapsible-body{padding:0 8px 8px}
.v970-overlay-back{display:inline-flex;align-items:center;min-height:32px;margin:0 0 9px;padding:5px 8px;border:1px solid var(--v970-line);border-radius:6px;text-decoration:none;color:var(--v970-text);background:var(--v970-surface-2)}
.v970-knowledge-meta{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:6px;margin:8px 0 12px;padding:8px;border:1px solid var(--v970-line);border-radius:6px;background:var(--v970-surface-2)}
.v970-knowledge-meta div{min-width:0}.v970-knowledge-meta span{display:block;color:var(--v970-muted);font-size:9px;text-transform:uppercase;letter-spacing:.06em}.v970-knowledge-meta strong{display:block;margin-top:2px;overflow-wrap:anywhere;font-size:11px}
.task-view-toggle{display:inline-flex;border:1px solid var(--v970-line);border-radius:6px;overflow:hidden;background:var(--v970-surface-2)}
.task-view-toggle a{padding:5px 9px;text-decoration:none;color:var(--v970-muted);border-right:1px solid var(--v970-line)}.task-view-toggle a:last-child{border-right:0}.task-view-toggle a.active{background:var(--v970-accent-soft);color:var(--v970-text)}
.task-calendar-toolbar{display:flex;justify-content:space-between;align-items:center;gap:12px;margin:9px 0}.task-calendar-month{font-size:15px;font-weight:800}.task-calendar-controls{display:flex;gap:4px}
.task-calendar-shell{border:1px solid var(--v970-line);border-radius:var(--v970-radius);overflow:hidden;background:var(--v970-surface)}
.task-calendar-head,.task-calendar-grid{display:grid;grid-template-columns:repeat(7,minmax(0,1fr))}.task-calendar-head>div{padding:6px;border-right:1px solid var(--v970-line);background:var(--v970-surface-2);color:var(--v970-muted);font-size:9px;text-transform:uppercase;font-weight:800;text-align:center}
.task-calendar-day{min-height:96px;padding:6px;border-right:1px solid var(--v970-line);border-top:1px solid var(--v970-line);background:var(--v970-surface)}.task-calendar-day.outside{background:var(--v970-surface-2);opacity:.64}.task-calendar-day.today{box-shadow:inset 0 0 0 1px var(--v970-accent)}
.task-calendar-date{font-size:10px;font-weight:800;color:var(--v970-muted);margin-bottom:4px}.task-calendar-event{display:block;margin:3px 0;padding:4px 5px;border-radius:5px;text-decoration:none;font-size:10px;overflow:hidden;text-overflow:ellipsis;background:var(--v970-surface-2);border:1px solid var(--v970-line)}
.task-calendar-event small{display:block;color:var(--v970-muted);font-size:9px;margin-top:1px}
.v970-task-day-agenda{margin:8px 0;padding:9px;border:1px solid var(--v970-line);border-radius:var(--v970-radius);background:var(--v970-surface)}
.v970-task-day-agenda-head{display:flex;align-items:baseline;justify-content:space-between;gap:8px;margin-bottom:6px}.v970-task-day-agenda-head strong{font-size:12px}.v970-task-day-agenda-head span{font-size:10px;color:var(--v970-muted)}
.v962-loading{min-height:180px;border:1px dashed var(--v970-line);border-radius:var(--v970-radius);background:var(--v970-surface);font-size:12px}

.v970-mobile-nav,.v970-more-sheet,.v970-sheet-backdrop{display:none}
@media(max-width:1279px){
  :root{--v970-sidebar-w:204px}.v970-workspace>main,.v970-contextbar{padding-left:14px;padding-right:14px}
  .v951-toolbar{overflow-x:auto;flex-wrap:nowrap}.v951-toolbar>*{flex:0 0 auto}.task-calendar-day{min-height:82px}
}
@media(max-width:767px){
  :root{--v970-context-h:52px}body{padding-bottom:68px}.shell{display:block}.v970-sidebar{display:none!important}
  .v970-workspace{min-height:calc(100vh - 68px)}.v970-contextbar{padding:7px 10px;min-height:52px}.v970-context-title strong{font-size:13px}.v970-context-title span{max-width:58vw}
  .v970-workspace>main{padding:9px 9px 18px}
  .v970-mobile-nav{position:fixed;display:grid;grid-template-columns:repeat(5,1fr);left:0;right:0;bottom:0;z-index:70;min-height:64px;padding:5px 5px max(5px,env(safe-area-inset-bottom));border-top:1px solid var(--v970-line);background:color-mix(in srgb,var(--v970-sidebar) 96%,transparent);backdrop-filter:blur(12px)}
  .v970-mobile-nav a,.v970-mobile-nav button{min-width:0;min-height:50px;border:0;background:transparent;color:var(--v970-muted);text-decoration:none;border-radius:6px;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:2px;font-size:9px;font-weight:700;padding:3px}
  .v970-mobile-nav a.active{color:var(--v970-accent);background:var(--v970-accent-soft)}
  .v970-sheet-backdrop.v970-open{display:block;position:fixed;inset:0;z-index:78;background:rgba(0,0,0,.42)}
  .v970-more-sheet.v970-open{display:block;position:fixed;left:7px;right:7px;bottom:70px;z-index:80;max-height:70vh;overflow:auto;border:1px solid var(--v970-line);border-radius:10px;background:var(--v970-surface);padding:8px;box-shadow:0 18px 50px rgba(0,0,0,.28)}
  .v970-more-head{display:flex;justify-content:space-between;align-items:center;padding:4px 5px 8px;border-bottom:1px solid var(--v970-line);font-weight:800}.v970-more-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:5px;padding-top:7px}
  .v970-more-grid a{display:flex;align-items:center;gap:7px;min-height:42px;padding:7px;border:1px solid var(--v970-line);border-radius:7px;text-decoration:none;color:var(--v970-text)}
  .card{padding:9px;margin:6px 0}.grid,.v951-grid{grid-template-columns:1fr;gap:6px}.row{gap:6px}.field.grow{min-width:100%}
  button,.btn{min-height:38px}input,select,textarea{min-height:38px;font-size:16px}textarea{min-height:100px}
  .v951-toolbar{margin:6px 0;padding:7px;display:flex;overflow-x:auto;flex-wrap:nowrap}.v951-toolbar label{min-width:150px}.v951-toolbar button{align-self:end;min-width:78px}
  .v951-metrics{grid-template-columns:repeat(2,minmax(0,1fr))}.scroll{border-radius:6px;max-width:calc(100vw - 18px)}.scroll table{min-width:650px}
  .v960-mailbox-tabs{overflow-x:auto;flex-wrap:nowrap;white-space:nowrap}.v963-refresh{width:100%;justify-content:space-between}

  /* Reader mode keeps the detail's .scroll ancestor alive. Only list chrome and non-detail rows disappear. */
  #panel-inbox:has(.v963-detail)>.v963-inbox-head,#panel-inbox:has(.v963-detail)>.v960-mailbox-tabs,#panel-inbox:has(.v963-detail)>.v951-toolbar,#panel-inbox:has(.v963-detail)>.v960-pagination,#panel-inbox:has(.v963-detail)>.v963-proxy-card{display:none}
  #panel-inbox:has(.v963-detail)>.scroll{display:block;overflow:visible;max-width:none;border:0;background:transparent}
  #panel-inbox:has(.v963-detail)>.scroll>.v960-mail-table{display:block;width:100%;min-width:0;table-layout:auto}
  #panel-inbox:has(.v963-detail)>.scroll>.v960-mail-table>thead{display:none}
  #panel-inbox:has(.v963-detail)>.scroll>.v960-mail-table>tbody,#panel-inbox:has(.v963-detail) .v960-inline-detail,#panel-inbox:has(.v963-detail) .v960-inline-detail>td{display:block;width:100%;min-width:0}
  #panel-inbox:has(.v963-detail)>.scroll>.v960-mail-table>tbody>tr:not(.v960-inline-detail){display:none}
  #panel-inbox:has(.v963-detail) .v960-inline-detail>td{padding:0!important;border:0}
  #panel-inbox .v963-detail{position:fixed;inset:52px 0 64px 0;z-index:58;margin:0;border:0!important;border-radius:0!important;padding:11px;overflow:auto;max-width:100vw;background:var(--v970-bg)!important}
  #panel-inbox .v963-detail>.v963-inbox-head{display:flex}
  #panel-inbox .v963-detail-actions{position:sticky;top:-11px;z-index:4;background:var(--v970-bg);padding:7px 0;border-bottom:1px solid var(--v970-line)}

  /* Knowledge uses list -> full-screen detail -> full-screen create/edit sheet, preserving existing routes/forms. */
  #panel-knowledge textarea{min-height:40vh}
  #panel-knowledge .v970-knowledge-detail,#panel-knowledge .v970-knowledge-editor[open]{position:fixed;inset:52px 0 64px;z-index:58;overflow:auto;margin:0;border:0!important;border-radius:0!important;background:var(--v970-bg)!important;padding:11px}
  #panel-knowledge .v970-knowledge-editor[open]>summary{position:sticky;top:-11px;z-index:4;margin:-11px -11px 8px;padding:11px!important;border-bottom:1px solid var(--v970-line);background:var(--v970-bg)}
  #panel-knowledge .v970-knowledge-editor[open]>.v962-collapsible-body{padding:0}
  #panel-knowledge .grid>.card.wide:last-child .scroll{overflow:visible;max-width:none;border:0;background:transparent}
  #panel-knowledge .grid>.card.wide:last-child table,#panel-knowledge .grid>.card.wide:last-child tbody,#panel-knowledge .grid>.card.wide:last-child tr,#panel-knowledge .grid>.card.wide:last-child td{display:block;width:100%;min-width:0}
  #panel-knowledge .grid>.card.wide:last-child table{min-width:0}#panel-knowledge .grid>.card.wide:last-child thead{display:none}
  #panel-knowledge .grid>.card.wide:last-child tr{margin:6px 0;padding:7px;border:1px solid var(--v970-line);border-radius:7px;background:var(--v970-surface)}
  #panel-knowledge .grid>.card.wide:last-child td{padding:2px 0;border:0}#panel-knowledge .grid>.card.wide:last-child td.actions{display:flex;gap:5px;margin-top:5px}
  .v970-knowledge-meta{grid-template-columns:1fr}

  /* Existing task registry, compact seven-column mobile representation plus selected-day agenda. */
  #panel-scheduler .card.wide:has(form[action="/dashboard/job/update"]){position:fixed;inset:52px 0 64px;z-index:58;overflow:auto;margin:0;border:0!important;border-radius:0!important;background:var(--v970-bg)!important;padding:11px}
  .task-calendar-toolbar{align-items:flex-start;flex-wrap:wrap}.task-calendar-shell{overflow:hidden;max-width:100%}
  .task-calendar-head,.task-calendar-grid{width:100%;min-width:0;grid-template-columns:repeat(7,minmax(0,1fr))}
  .task-calendar-head>div{min-width:0;padding:4px 1px;font-size:8px}.task-calendar-day{min-width:0;min-height:54px;padding:3px;overflow:hidden;cursor:pointer}
  .task-calendar-date{margin-bottom:2px;text-align:center;font-size:9px}.task-calendar-day.v970-selected{background:var(--v970-accent-soft);box-shadow:inset 0 0 0 1px var(--v970-accent)}
  .task-calendar-grid .task-calendar-event{height:5px;min-height:0;margin:2px 0;padding:0;border:0;border-radius:3px;font-size:0;line-height:0;background:var(--v970-accent)}
  .task-calendar-grid .task-calendar-event small{display:none}.v970-task-day-agenda .task-calendar-event{height:auto;min-height:34px;margin:4px 0;padding:6px 7px;font-size:11px;line-height:1.35;border:1px solid var(--v970-line);background:var(--v970-surface-2)}
  .v970-task-day-agenda .task-calendar-event small{display:block;font-size:9px;line-height:1.3}
  .v963-proxy-form,#panel-knowledge .v951-formgrid{grid-template-columns:1fr}
}
@media(max-width:430px){.v970-context-title span{max-width:52vw}.v951-metric strong{font-size:16px}}
@media(prefers-reduced-motion:reduce){*,*::before,*::after{scroll-behavior:auto!important;transition:none!important;animation:none!important}}
'''

ENTERPRISE_SCRIPT = r'''
<script id="v970-enterprise-shell">
(() => {
  const labels = %VIEW_LABELS%;
  let moreReturnFocus = null;

  function currentView() {
    const active = document.querySelector('.tab-panel.active');
    if (active && active.dataset.panel) return active.dataset.panel;
    const hash = (location.hash || '').slice(1);
    if (labels[hash]) return hash;
    const query = new URL(location.href).searchParams.get('ui_view');
    return labels[query] ? query : 'overview';
  }
  function syncContext() {
    const view = currentView(), item = labels[view] || labels.overview;
    const title = document.querySelector('[data-v970-context-title]');
    const sub = document.querySelector('[data-v970-context-subtitle]');
    if (title) title.textContent = item[0];
    if (sub) sub.textContent = item[1];
    document.querySelectorAll('[data-v970-nav]').forEach(el => {
      const active = el.dataset.v970Nav === view;
      el.classList.toggle('active', active);
      if (el.tagName === 'A') active ? el.setAttribute('aria-current','page') : el.removeAttribute('aria-current');
    });
    document.body.dataset.v970View = view;
  }

  function setBackgroundInert(open) {
    const nodes = [
      document.querySelector('.v970-workspace'),
      ...document.querySelectorAll('.v970-mobile-nav a,.v970-mobile-nav button:not([data-v970-more])')
    ].filter(Boolean);
    nodes.forEach(node => {
      if (open) {
        node.dataset.v970InertWas = node.hasAttribute('inert') ? '1' : '0';
        node.setAttribute('inert','');
      } else if (node.dataset.v970InertWas !== undefined) {
        if (node.dataset.v970InertWas === '0') node.removeAttribute('inert');
        delete node.dataset.v970InertWas;
      }
    });
  }
  function moreIsOpen() {
    return document.querySelector('.v970-more-sheet')?.classList.contains('v970-open') || false;
  }
  function closeMore(restoreFocus=true) {
    if (!moreIsOpen()) return;
    const sheet=document.querySelector('.v970-more-sheet');
    const back=document.querySelector('.v970-sheet-backdrop');
    const button=document.querySelector('[data-v970-more]');
    sheet?.classList.remove('v970-open');
    back?.classList.remove('v970-open');
    sheet?.setAttribute('aria-hidden','true');
    button?.setAttribute('aria-expanded','false');
    setBackgroundInert(false);
    if (restoreFocus) {
      const target = moreReturnFocus && document.contains(moreReturnFocus) ? moreReturnFocus : button;
      target?.focus();
    }
    moreReturnFocus = null;
  }
  function openMore() {
    const sheet=document.querySelector('.v970-more-sheet'), back=document.querySelector('.v970-sheet-backdrop'), button=document.querySelector('[data-v970-more]');
    if (!sheet || !button) return;
    moreReturnFocus = document.activeElement instanceof HTMLElement ? document.activeElement : button;
    sheet.classList.add('v970-open'); back?.classList.add('v970-open');
    sheet.setAttribute('aria-hidden','false'); button.setAttribute('aria-expanded','true');
    setBackgroundInert(true);
    (sheet.querySelector('[data-v970-more-close]') || sheet.querySelector('a,button'))?.focus();
  }
  function toggleMore() { moreIsOpen() ? closeMore(true) : openMore(); }
  function trapMoreFocus(ev) {
    if (ev.key !== 'Tab' || !moreIsOpen()) return;
    const sheet=document.querySelector('.v970-more-sheet');
    const focusables=[...(sheet?.querySelectorAll('a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])') || [])]
      .filter(el => !el.hasAttribute('hidden') && getComputedStyle(el).display !== 'none' && getComputedStyle(el).visibility !== 'hidden');
    if (!focusables.length) { ev.preventDefault(); sheet?.focus(); return; }
    const first=focusables[0], last=focusables[focusables.length-1];
    if (ev.shiftKey && document.activeElement === first) { ev.preventDefault(); last.focus(); }
    else if (!ev.shiftKey && document.activeElement === last) { ev.preventDefault(); first.focus(); }
  }

  function applyTheme(value) {
    const root=document.documentElement;
    if(value==='dark'||value==='light') root.dataset.v970Theme=value; else delete root.dataset.v970Theme;
    const button=document.querySelector('[data-v970-theme-toggle]');
    if(button){const actual=root.dataset.v970Theme||'system';button.textContent=actual==='dark'?'☾':actual==='light'?'☀':'◐';button.setAttribute('aria-label','Theme: '+actual+'. Activate to change.');}
  }
  function cycleTheme() {
    const current=document.documentElement.dataset.v970Theme||'system';
    const next=current==='system'?'dark':current==='dark'?'light':'system';
    if(next==='system') localStorage.removeItem('postmaster:v970:theme'); else localStorage.setItem('postmaster:v970:theme',next);
    applyTheme(next);
  }

  function taskDays() { return [...document.querySelectorAll('#panel-scheduler .task-calendar-grid .task-calendar-day')]; }
  function selectTaskDay(day) {
    const days=taskDays(); if (!day || !days.includes(day)) return;
    days.forEach(candidate => {
      const selected=candidate === day;
      candidate.classList.toggle('v970-selected', selected);
      candidate.setAttribute('aria-selected', selected ? 'true' : 'false');
      candidate.tabIndex = selected ? 0 : -1;
    });
    const shell=day.closest('.task-calendar-shell'); if (!shell) return;
    let agenda=shell.nextElementSibling;
    if (!agenda?.classList.contains('v970-task-day-agenda')) {
      agenda=document.createElement('section'); agenda.className='v970-task-day-agenda'; shell.insertAdjacentElement('afterend',agenda);
    }
    const date=day.querySelector('.task-calendar-date')?.textContent?.trim() || 'Selected day';
    const month=document.querySelector('#panel-scheduler .task-calendar-month')?.textContent?.trim() || '';
    agenda.replaceChildren();
    const head=document.createElement('div'); head.className='v970-task-day-agenda-head';
    const strong=document.createElement('strong'); strong.textContent=date;
    const span=document.createElement('span'); span.textContent=month;
    head.append(strong,span); agenda.append(head);
    const events=[...day.querySelectorAll(':scope > .task-calendar-event')];
    if (!events.length) {
      const empty=document.createElement('p'); empty.className='small muted'; empty.textContent='No tasks for this day.'; agenda.append(empty);
    } else {
      events.forEach(event => { const clone=event.cloneNode(true); clone.classList.add('v970-agenda-event'); agenda.append(clone); });
    }
  }
  function initTaskCalendar() {
    const days=taskDays(); if (!days.length) return;
    days.forEach(day => {
      day.setAttribute('role','button'); day.setAttribute('aria-label','Show task agenda for '+(day.querySelector('.task-calendar-date')?.textContent?.trim() || 'day'));
      if (!day.hasAttribute('aria-selected')) day.setAttribute('aria-selected','false');
      if (!day.hasAttribute('tabindex')) day.tabIndex=-1;
    });
    const existing=days.find(day => day.classList.contains('v970-selected'));
    if (existing) return;
    const initial=days.find(day => day.classList.contains('today') && !day.classList.contains('outside')) || days.find(day => !day.classList.contains('outside') && day.querySelector('.task-calendar-event')) || days.find(day => !day.classList.contains('outside')) || days[0];
    selectTaskDay(initial);
  }

  function cleanKnowledgeUrl() {
    const url=new URL(location.href); url.searchParams.delete('view_knowledge'); url.searchParams.delete('edit_knowledge'); url.hash='knowledge'; return url.toString();
  }
  function ensureKnowledgeBack(container) {
    if (!container || container.querySelector(':scope > .v970-overlay-back')) return;
    const back=document.createElement('a'); back.className='v970-overlay-back'; back.href=cleanKnowledgeUrl(); back.dataset.v960Fragment='knowledge'; back.textContent='← Back to list'; container.insertBefore(back,container.firstChild);
  }
  function initKnowledgeDrilldown() {
    const panel=document.querySelector('#panel-knowledge'); if (!panel) return;
    const detail=panel.querySelector('.grid > .card.wide:has(.markdown-viewer)');
    if (detail) {
      detail.classList.add('v970-knowledge-detail'); ensureKnowledgeBack(detail);
      if (!detail.querySelector('.v970-knowledge-meta')) {
        const id=new URL(location.href).searchParams.get('view_knowledge');
        const rows=[...panel.querySelectorAll('.grid > .card.wide:last-child tbody tr')];
        const row=rows.find(candidate => candidate.querySelector('.small.muted.mono')?.textContent?.trim() === id);
        const cells=row ? [...row.children] : [];
        if (cells.length >= 4) {
          const meta=document.createElement('div'); meta.className='v970-knowledge-meta';
          [['Kind',cells[1]],['Scopes',cells[2]],['Priority',cells[3]]].forEach(([label,cell]) => {
            const box=document.createElement('div'), name=document.createElement('span'), value=document.createElement('strong');
            name.textContent=label; value.textContent=cell.textContent?.trim() || '—'; box.append(name,value); meta.append(box);
          });
          detail.querySelector('.markdown-viewer')?.insertAdjacentElement('beforebegin',meta);
        }
      }
    }
    const editor=panel.querySelector('.v962-collapsible[data-v962-state-key="knowledge-editor"]');
    if (editor) {
      editor.classList.add('v970-knowledge-editor');
      if (editor.open) ensureKnowledgeBack(editor.querySelector('.v962-collapsible-body'));
    }
  }

  function syncPresentation() { syncContext(); initTaskCalendar(); initKnowledgeDrilldown(); }

  document.addEventListener('click',ev=>{
    if(ev.target.closest('[data-v970-more]')){toggleMore();return}
    if(ev.target.closest('[data-v970-more-close],.v970-sheet-backdrop')){closeMore(true);return}
    if(ev.target.closest('[data-v970-theme-toggle]')){cycleTheme();return}
    if(ev.target.closest('.v970-more-sheet a'))closeMore(false);
    const day=ev.target.closest('.task-calendar-day');
    if(day && !ev.target.closest('a,button,input,form,select,textarea')){selectTaskDay(day);return}
    queueMicrotask(syncPresentation);
  });
  window.addEventListener('popstate',()=>queueMicrotask(syncPresentation));
  window.addEventListener('hashchange',()=>queueMicrotask(syncPresentation));
  document.addEventListener('keydown',ev=>{
    if(ev.key==='Escape' && moreIsOpen()){ev.preventDefault();closeMore(true);return}
    trapMoreFocus(ev);
    const day=ev.target.closest?.('.task-calendar-day');
    if(day && (ev.key==='Enter'||ev.key===' ')){ev.preventDefault();selectTaskDay(day);}
  });
  new MutationObserver(()=>queueMicrotask(syncPresentation)).observe(document.body,{subtree:true,childList:true,attributes:true,attributeFilter:['class','open']});
  applyTheme(localStorage.getItem('postmaster:v970:theme')||'system');
  syncPresentation();
})();
</script>
'''


def _link(view: str, label: str, code: str, *, mobile: bool = False) -> str:
    css = "v970-mobile-link" if mobile else "v970-nav-link"
    return (
        f'<a class="{css}" href="#{escape(view)}" '
        f'data-v962-nav="{escape(view)}" data-v970-nav="{escape(view)}">'
        f'<span class="v970-nav-code" aria-hidden="true">{escape(code)}</span>'
        f'<span>{escape(label)}</span></a>'
    )


def enterprise_nav() -> str:
    parts = [
        '<aside class="v970-sidebar" aria-label="Primary navigation">',
        '<div class="v970-brand"><span class="v970-mark" aria-hidden="true">PM</span>'
        '<div class="v970-brand-copy"><strong>Postmaster</strong>'
        '<small>WebGUI v9.6.2 · lazy fragments</small></div></div>',
    ]
    for heading, links in NAV_GROUPS:
        parts.append('<div class="v970-nav-group">')
        parts.append(f'<div class="v970-nav-label">{escape(heading)}</div>')
        parts.extend(_link(view, label, code) for view, label, code in links)
        parts.append('</div>')
    parts.append('<div class="v970-sidebar-foot">Dense operational workspace<br>Repository-backed capabilities only</div></aside>')

    primary = (("overview","Home","DB"),("inbox","Mail","ML"),("compose","Compose","CP"),("scheduler","Tasks","TS"))
    parts.append('<nav class="v970-mobile-nav" aria-label="Mobile primary navigation">')
    parts.extend(_link(view,label,code,mobile=True) for view,label,code in primary)
    parts.append('<button type="button" data-v970-more aria-expanded="false" aria-controls="v970-more-sheet"><span class="v970-nav-code" aria-hidden="true">••</span><span>More</span></button></nav>')
    parts.append('<div class="v970-sheet-backdrop" data-v970-more-close aria-hidden="true"></div>')
    parts.append('<section class="v970-more-sheet" id="v970-more-sheet" role="dialog" aria-modal="true" aria-labelledby="v970-more-title" aria-hidden="true" tabindex="-1"><div class="v970-more-head"><span id="v970-more-title">More sections</span><button type="button" data-v970-more-close aria-label="Close more sections">×</button></div><div class="v970-more-grid">')
    for _heading, links in NAV_GROUPS:
        for view,label,code in links:
            if view not in {"overview","inbox","compose","scheduler"}:
                parts.append(_link(view,label,code,mobile=True))
    parts.append('</div></section>')
    return ''.join(parts)


def _context_bar() -> str:
    return (
        '<header class="v970-contextbar">'
        '<div class="v970-context-title"><strong data-v970-context-title>Dashboard</strong>'
        '<span data-v970-context-subtitle>Operational posture and system signals</span></div>'
        '<div class="v970-context-actions"><button class="v970-theme-toggle" type="button" '
        'data-v970-theme-toggle aria-label="Theme: system. Activate to change." title="Theme">◐</button></div>'
        '</header>'
    )


def _install_final_style(v962: Any) -> None:
    original_styles = v962._styles
    if getattr(original_styles, "_postmaster_v970_styles", False):
        return

    def enterprise_styles() -> str:
        styles = str(original_styles())
        # If an earlier experimental installer placed this exact overlay in BASE_STYLE,
        # remove that copy before appending it after every legacy stylesheet.
        styles = styles.replace(ENTERPRISE_STYLE, "")
        return styles.rstrip() + "\n" + ENTERPRISE_STYLE

    enterprise_styles._postmaster_v970_styles = True  # type: ignore[attr-defined]
    v962._styles = enterprise_styles


def install_webgui_v970(v962: Any) -> None:
    # Visual overlay only: mutate shell presentation primitives, never application routes.
    _install_final_style(v962)

    if 'id="v970-enterprise-shell"' not in str(v962.SCRIPT):
        mapping = {key: [title, subtitle] for key, (title, subtitle) in VIEW_LABELS.items()}
        v962.SCRIPT += ENTERPRISE_SCRIPT.replace(
            "%VIEW_LABELS%",
            json.dumps(mapping, ensure_ascii=False, separators=(",", ":")),
        )

    v962._nav = enterprise_nav
    original_shell = v962._shell
    if getattr(original_shell, "_postmaster_v970_shell", False):
        return

    def enterprise_shell(request: Any) -> HTMLResponse:
        response = original_shell(request)
        if "text/html" not in str(response.headers.get("content-type", "")).lower():
            return response
        html = response.body.decode("utf-8")
        html = html.replace("<main>", '<div class="v970-workspace">' + _context_bar() + "<main>", 1)
        html = html.replace("</main>", "</main></div>", 1)
        headers = {key:value for key,value in response.headers.items() if key.lower() != "content-length"}
        headers["X-Postmaster-WebGUI-Design"] = "v9.7.0-enterprise-refresh"
        return HTMLResponse(html, status_code=response.status_code, headers=headers)

    enterprise_shell._postmaster_v970_shell = True  # type: ignore[attr-defined]
    v962._shell = enterprise_shell


__all__ = ["ENTERPRISE_SCRIPT","ENTERPRISE_STYLE","NAV_GROUPS","VIEW_LABELS","enterprise_nav","install_webgui_v970"]
