"""v9.8.5 Settings: real version management first, operational diagnostics on demand."""
from __future__ import annotations

from html import escape
from typing import Any

from starlette.requests import Request

from . import webgui_v951 as v951
from .webgui_versions_v984 import versions_settings
from .webgui_v962_views import _panel


def render_settings_v985(base: Any, request: Request) -> str:
    version=versions_settings(base)
    actions=(
        ("Email accounts","accounts","Manage configured sender accounts and connectivity"),
        ("Privacy and security","security","Inspect privacy proxy, authentication and mail policy"),
        ("MCP Catalog","coverage","Inspect the available MCP capabilities"),
        ("Contacts and sending permissions","contacts","Authorized recipients and domains"),
    )
    shortcuts="".join(
        '<a class="v985-settings-shortcut" href="#'+escape(view)+'" data-v962-nav="'+escape(view)+'">'
        '<strong>'+escape(title)+'</strong><span>'+escape(note)+'</span><b>→</b></a>'
        for title,view,note in actions
    )
    def status_card(title: str, fn: Any) -> str:
        try:
            data=base._safe_call(fn)
        except Exception:
            data={"status":"unavailable"}
        return (
            '<div class="v985-settings-status">'
            '<strong>'+escape(title)+'</strong>'
            +v951._status_rows(data)
            +v951._details(data)
            +'</div>'
        )
    diagnostics=(
        status_card("Runtime",base.build_status)
        +status_card("Tracking",base.tracking_status)
        +status_card("Knowledge",base.context_engine().status)
        +status_card("File Store",base.file_store().status)
        +status_card("Scheduler",base.scheduler().status)
    )
    html=(
        '<div class="v985-settings">'
        '<div class="v985-pagehead"><div><h2>Settings</h2>'
        '<p>Release management, accounts, privacy and system configuration</p></div></div>'
        '<div class="v985-settings-tabs" aria-label="Settings categories">'
        '<a class="current" href="#v984-versions">Versions & updates</a>'
        '<a href="#v985-settings-other">Manage services</a>'
        '<a href="#v985-settings-diagnostics">Diagnostics</a>'
        '</div>'
        +version
        +'<section class="v985-settings-card" id="v985-settings-other">'
        '<h3>Services & permissions</h3><div class="v985-settings-shortcuts">'
        +shortcuts
        +'</div></section>'
        +'<details class="v985-settings-card" id="v985-settings-diagnostics">'
        '<summary>Detailed service status</summary><p class="small muted">'
        'Live subsystem snapshots; expand individual sections for diagnostic details.</p>'
        '<div class="v985-settings-diagnostics">'+diagnostics+'</div></details>'
        '</div>'
    )
    return _panel("system",html)


SETTINGS_STYLE = r"""
.v985-settings{max-width:1380px;margin:auto}
.v985-settings-tabs{display:flex;flex-wrap:wrap;gap:7px;border-bottom:1px solid var(--v970-line);margin:15px 0}
.v985-settings-tabs a{color:var(--v970-muted);padding:10px 13px;text-decoration:none;font-size:12px;
 border-bottom:2px solid transparent}
.v985-settings-tabs a.current,.v985-settings-tabs a:hover{color:var(--v970-accent);border-bottom-color:var(--v970-accent)}
.v985-settings .card.wide,.v985-settings-card{border:1px solid var(--v970-line)!important;
 background:var(--v970-surface)!important;border-radius:10px!important;padding:18px!important;margin:14px 0!important}
.v985-settings h3{font-size:16px;margin:0 0 12px}
.v985-settings #v984-versions form{display:flex;gap:12px;flex-wrap:wrap;align-items:end}
.v985-settings #v984-versions form label{display:flex;flex-direction:column;gap:5px}
.v985-settings #v984-versions select{min-width:160px}
.v985-settings-shortcuts{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:11px}
.v985-settings-shortcut{display:grid;grid-template-columns:1fr auto;gap:4px 12px;border:1px solid var(--v970-line);
 border-radius:9px;padding:13px 16px;text-decoration:none;color:var(--v970-text);background:var(--v970-surface-2)}
.v985-settings-shortcut strong{font-size:12px}.v985-settings-shortcut span{font-size:10px;color:var(--v970-muted)}
.v985-settings-shortcut b{color:var(--v970-accent);grid-row:1/3;grid-column:2;align-self:center}
.v985-settings-card>summary{font-size:16px;font-weight:800;cursor:pointer}
.v985-settings-diagnostics{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:11px;margin-top:12px}
.v985-settings-status{border:1px solid var(--v970-line);border-radius:9px;padding:12px;overflow-wrap:anywhere}
.v985-settings-status>strong{display:block;font-size:13px;margin-bottom:9px}
@media(max-width:690px){.v985-settings-shortcuts{grid-template-columns:1fr}.v985-settings .card.wide{padding:12px!important}}
"""


def install_settings_v985(v951_mod: Any, v962: Any) -> None:
    v951_mod.render_system=render_settings_v985
    previous=v962._styles
    if getattr(previous,"_postmaster_v985_settings",False):return
    def styles()->str:
        return previous().rstrip()+"\n"+SETTINGS_STYLE
    styles._postmaster_v985_settings=True
    v962._styles=styles
