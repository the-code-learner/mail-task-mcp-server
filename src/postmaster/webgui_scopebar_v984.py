"""v9.8.4 organization-aware WebGUI header (not an access-control boundary).

The 9.6.2 lazy shell stays data-query free. Owner/project/account options are
requested by the authenticated browser after the shell loads; MCP authorization
remains instance-global. This introduces no account secrets in response payloads.
"""
from __future__ import annotations

from typing import Any
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Mount, Route
from .webgui_v962_perf import owners, active_projects, accounts


HEADER = """<div class="v984-scopebar" id="v984-scopebar" aria-label="Organizational context">
<label>Owner<select data-v984-scope="owner_id" aria-label="Owner"><option value="">All owners</option></select></label>
<label>Project<select data-v984-scope="project_id" aria-label="Project"><option value="">All projects</option></select></label>
<label>Email account<select data-v984-scope="account_id" aria-label="Email account"><option value="">All accounts</option></select></label>
</div>"""

STYLE = """<style id="v984-scope-style">
.v984-scopebar{display:flex;align-items:center;gap:8px;flex-wrap:wrap;min-width:0}
.v984-scopebar label{display:flex;flex-direction:column;font-size:10px;gap:2px;min-width:112px}
.v984-scopebar select{max-width:185px;min-width:105px;font-size:12px;padding:5px 8px;border-radius:7px}
.v970-nav-link[data-v970-nav="compose"],.v970-mobile-link[data-v970-nav="compose"]{display:none!important}
@media(max-width:820px){.v984-scopebar{width:100%;justify-content:start}
.v984-scopebar label{flex:1;min-width:90px}.v984-scopebar select{max-width:100%;width:100%}}
</style>"""

SCRIPT = """<script id="v984-scope-script">
(() => {
  const bar = document.getElementById('v984-scopebar');
  if (!bar) return;
  const select = key => bar.querySelector('select[data-v984-scope="'+key+'"]');
  const params = new URL(location.href).searchParams;
  const values = {
    owner_id: params.get('owner_id') || '',
    project_id: params.get('project_id') || params.get('project') || '',
    account_id: params.get('account_id') || params.get('account') || ''
  };
  function fill(sel, rows, title) {
    const saved = values[sel.dataset.v984Scope] || '';
    while (sel.options.length > 1) sel.remove(1);
    for (const row of rows) {
      const id = String(row.id || '');
      if (!id) continue;
      const opt = document.createElement('option');
      opt.value = id;
      opt.textContent = String(row.label || id);
      sel.appendChild(opt);
    }
    sel.value = saved;
    if (sel.value !== saved) sel.value = '';
    sel.disabled = false;
  }
  for (const field of ['owner_id','project_id','account_id']) {
    const element = select(field);
    element.disabled = true;
    element.addEventListener('change', () => {
      const url = new URL(window.location.href);
      const owner = select('owner_id').value;
      let project = select('project_id').value;
      const account = select('account_id').value;
      if (field === 'owner_id') project = '';
      const options = {owner_id:owner,project_id:project,account_id:account};
      for (const [key, value] of Object.entries(options)) {
        if (value) url.searchParams.set(key,value);
        else url.searchParams.delete(key);
      }
      if (project) url.searchParams.set('project',project);
      else url.searchParams.delete('project');
      if (account) url.searchParams.set('account',account);
      else url.searchParams.delete('account');
      url.searchParams.set('ui_view',(url.hash||'#overview').slice(1));
      window.location.assign(url.href);
    });
  }
  fetch('/dashboard/context/options', {
    credentials:'same-origin', headers:{'Accept':'application/json'}
  }).then(async response => {
    if (!response.ok) throw new Error('Unable to load context');
    return response.json();
  }).then(data => {
    const owner = select('owner_id');
    const project = select('project_id');
    const account = select('account_id');
    const selectedOwner = values.owner_id;
    const candidates = (data.projects || []).filter(row => !selectedOwner || row.owner_id === selectedOwner);
    fill(owner,data.owners || []);
    fill(project,candidates);
    fill(account,data.accounts || []);
  }).catch(() => {
    for (const key of ['owner_id','project_id','account_id']) {
      const field = select(key);
      field.disabled = true;
      field.title = 'Context selection temporarily unavailable';
    }
  });
})();
</script>"""


def install_scopebar_webgui_v984(app: Any, base: Any, v962: Any, v970: Any) -> None:
    if getattr(v962, "_v984_scopebar_installed", False):
        return

    async def options(_request: Request) -> JSONResponse:
        owner_rows = owners(base)
        project_rows = active_projects(base)
        email_rows = accounts(base)
        return JSONResponse({
            "owners": [
                {"id":str(x.get("id") or ""),"label":str(x.get("display_name") or x.get("id") or "")}
                for x in owner_rows
            ],
            "projects": [
                {"id":str(x.get("id") or ""),"owner_id":str(x.get("owner_id") or ""),
                 "label":str(x.get("name") or x.get("id") or "")}
                for x in project_rows
            ],
            "accounts": [
                {"id":str(x.get("id") or ""),
                 "label":str(x.get("label") or x.get("email_address") or x.get("id") or "")}
                for x in email_rows
            ],
        }, headers={"Cache-Control":"private, no-store", "X-Content-Type-Options":"nosniff"})

    index = next(
        (i for i, r in enumerate(app.router.routes) if isinstance(r, Mount)),
        len(app.router.routes),
    )
    app.router.routes.insert(index, Route("/dashboard/context/options",options,methods=["GET"]))

    previous_shell = v962._shell
    if getattr(previous_shell, "_v984_scopebar_shell", False):
        return

    def shell(request: Request) -> HTMLResponse:
        response = previous_shell(request)
        if "text/html" not in str(response.headers.get("content-type", "")).lower():
            return response
        html = response.body.decode("utf-8")
        if 'id="v984-scopebar"' not in html:
            anchor = '<div class="v970-context-actions">'
            if anchor in html:
                html = html.replace(anchor,HEADER+anchor,1)
            else:
                html = html.replace("<main>",HEADER+"<main>",1)
        html = html.replace("</head>",STYLE+"</head>",1)
        html = html.replace("</body>",SCRIPT+"</body>",1)
        headers = {k:v for k,v in response.headers.items() if k.lower()!="content-length"}
        return HTMLResponse(html,status_code=response.status_code,headers=headers)

    shell._v984_scopebar_shell = True
    v962._shell = shell
    setattr(v962,"_v984_scopebar_installed",True)
