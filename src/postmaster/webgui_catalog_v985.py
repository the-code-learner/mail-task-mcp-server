"""Approved v5 MCP Catalog: actual registered tools, no static legacy v9.5 inventory."""
from __future__ import annotations

from html import escape
from typing import Any

from starlette.requests import Request


def _group(name: str) -> str:
    n=name.lower()
    if n.startswith("db_"):
        return "Structured Data"
    if "contact" in n or "authoriz" in n or "domain" in n or "recipient" in n:
        return "Contacts & authorization"
    if "track" in n or "click" in n or "campaign" in n or "delivery" in n:
        return "Tracking & analytics"
    if "file" in n or "attachment" in n:
        return "File Store"
    if "calendar" in n or "invite" in n or "email" in n or "mail" in n or "draft" in n:
        return "Email & calendar"
    if "job" in n or "scheduler" in n or "task" in n or "execution" in n:
        return "Activities"
    if "owner" in n or "project" in n or "memory" in n or "skill" in n or "knowledge" in n or "context" in n:
        return "Workspace & knowledge"
    return "Runtime & administration"


ORDER=(
    "Email & calendar",
    "Tracking & analytics",
    "File Store",
    "Activities",
    "Workspace & knowledge",
    "Contacts & authorization",
    "Structured Data",
    "Runtime & administration",
)


CATALOG_STYLE=r"""
.v985-catalog .v985-catalog-head{display:flex;align-items:center;gap:15px;justify-content:space-between;flex-wrap:wrap;margin:5px 0 14px}
.v985-catalog .v985-catalog-head h2{font-size:23px;margin:0}
.v985-catalog .v985-catalog-head p{font-size:11px;color:var(--v970-muted);margin:5px 0}
.v985-catalog .v985-catalog-head input{width:min(400px,100%);min-width:240px;border-radius:8px}
.v985-catalog .v985-catalog-summary{font-size:12px;color:var(--v970-muted);margin:12px 0}
.v985-catalog .v985-tool-group{border:1px solid var(--v970-line);border-radius:10px;
 background:var(--v970-surface);padding:0!important;margin:12px 0;overflow:hidden}
.v985-catalog .v985-tool-group>summary{display:flex;gap:12px;align-items:center;justify-content:space-between;
 font-size:13px;font-weight:800;padding:14px;cursor:pointer;background:var(--v970-surface-2)}
.v985-catalog .v985-tool-group>summary span{font-size:11px;font-weight:500;color:var(--v970-muted)}
.v985-catalog .v985-tool-rows{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:9px;padding:12px}
.v985-catalog .v985-tool{border:1px solid var(--v970-line);border-radius:8px;padding:12px;min-width:0;background:var(--v970-surface)}
.v985-catalog .v985-tool strong{display:block;font:600 12px ui-monospace,monospace;color:var(--v970-text);
 overflow-wrap:anywhere}
.v985-catalog .v985-tool p{color:var(--v970-muted);font-size:10px;line-height:1.6;margin:7px 0 0;overflow-wrap:anywhere}
.v985-catalog .v985-tool[hidden],.v985-catalog .v985-tool-group[hidden]{display:none!important}
@media(max-width:650px){.v985-catalog .v985-tool-rows{grid-template-columns:1fr}
.v985-catalog .v985-catalog-head input{min-width:0;width:100%}}
"""

CATALOG_SCRIPT=r"""<script id="v985-mcp-catalog-filter">
(() => {
  document.addEventListener('input',event => {
    const input=event.target.closest('[data-v985-tool-search]');
    if(!input)return;
    const panel=input.closest('.v985-catalog');
    if(!panel)return;
    const q=input.value.trim().toLowerCase();
    for(const group of panel.querySelectorAll('.v985-tool-group')){
      let shown=0;
      for(const card of group.querySelectorAll('.v985-tool')){
        const visible=card.textContent.toLowerCase().includes(q);
        card.hidden=!visible;
        if(visible)shown++;
      }
      group.hidden=shown===0;
      if(q && shown)group.open=true;
    }
  });
})();
</script>"""


def render_catalog_v985(base: Any, request: Request, tool_snapshot: list[tuple[str,str]]) -> str:
    counts={section:[] for section in ORDER}
    for name,description in tool_snapshot:
        counts[_group(name)].append((name,description))
    groups=[]
    for section in ORDER:
        items=counts[section]
        if not items:continue
        rows="".join(
            '<article class="v985-tool"><strong>'+escape(name)+'</strong>'
            '<p>'+escape(description[:400] or "No description provided")+'</p></article>'
            for name,description in sorted(items)
        )
        groups.append(
            '<details class="v985-tool-group" open>'
            '<summary>'+escape(section)+' <span>'+str(len(items))+' tools</span></summary>'
            '<div class="v985-tool-rows">'+rows+'</div></details>'
        )
    html=(
        '<section class="v985-catalog"><div class="v985-catalog-head"><div>'
        '<h2>MCP Catalog</h2><p>Actual registered MCP tools in this instance, not a static documentation snapshot.</p></div>'
        '<input type="search" data-v985-tool-search aria-label="Search MCP tools" placeholder="Search tools…">'
        '</div><p class="v985-catalog-summary">'
        +str(len(tool_snapshot))+' MCP commands available · '
        +str(len(groups))+' functional categories'
        '</p>'+''.join(groups)
        +'</section>'
    )
    return '<section class="tab-panel" id="panel-coverage" data-panel="coverage">'+html+'</section>'


def install_catalog_v985(v951: Any, v962: Any, core: Any) -> None:
    manager=getattr(getattr(core,"mcp",None),"_tool_manager",None)
    if manager is None or not callable(getattr(manager,"list_tools",None)):
        raise RuntimeError("MCP tool registry unavailable; refusing a misleading Catalog")
    registered=manager.list_tools()
    snapshot=sorted(
        (str(x.name),str(x.description or ""))
        for x in registered
    )
    if not snapshot:
        raise RuntimeError("MCP registry unexpectedly empty")
    def render(base: Any, request: Request) -> str:
        return render_catalog_v985(base,request,snapshot)
    v951.render_coverage=render
    old=v962._styles
    if not getattr(old,"_v985_catalog",False):
        def styles()->str:
            return old().rstrip()+"\n"+CATALOG_STYLE
        styles._v985_catalog=True
        v962._styles=styles
    if 'id="v985-mcp-catalog-filter"' not in v962.SCRIPT:
        v962.SCRIPT+=CATALOG_SCRIPT
