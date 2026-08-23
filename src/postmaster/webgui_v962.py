from __future__ import annotations

import time
from html import escape
from typing import Any

from starlette.requests import Request
from starlette.responses import HTMLResponse
from starlette.routing import Mount, Route

from . import webgui_v951 as v951
from . import webgui_v952 as v952
from . import webgui_v953 as v953
from . import webgui_v954 as v954
from . import webgui_v960 as v960
from . import webgui_v961 as v961
from .webgui_v962_views import (
    VIEWS,
    dashboard_project_create,
    dashboard_project_delete,
    dashboard_project_update,
    render_view,
)


NAV = (
    ("overview", "Dashboard"), ("accounts", "Accounts"), ("mail-health", "Mail Health"),
    ("inbox", "Inbox"), ("compose", "Compose"), ("tracking", "Tracking"),
    ("deliveries", "Deliveries"), ("suppressions", "Suppressions"), ("security", "Security"),
    ("amp", "AMP"), ("domains", "Domains"), ("recipients", "Recipients"),
    ("projects", "Projects"), ("knowledge", "Knowledge"), ("files", "Files"),
    ("scheduler", "Tasks"), ("system", "System"), ("coverage", "MCP Coverage"),
)

BASE_STYLE = r'''
:root{color-scheme:dark;--bg:#101317;--card:#171c22;--card2:#1d232b;--text:#eef2f7;--muted:#9ca8b6;--line:#303844;--accent:#68a0ff;--danger:#ff6b6b;--ok:#71d18c}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;font-size:14px}a{color:var(--accent)}button,input,select,textarea{font:inherit}button{border:1px solid var(--line);border-radius:8px;background:var(--card2);color:var(--text);padding:7px 10px;cursor:pointer}button.primary,.primary{border-color:var(--accent)}button.danger,.danger{border-color:var(--danger);color:#ffd2d2}.shell{display:grid;grid-template-columns:230px minmax(0,1fr);min-height:100vh}.v962-nav{position:sticky;top:0;height:100vh;overflow:auto;border-right:1px solid var(--line);padding:16px 10px;background:#0d1014}.v962-brand{padding:4px 9px 14px}.v962-brand strong{font-size:18px}.v962-brand small{display:block;color:var(--muted);margin-top:3px}.v962-nav a{display:block;text-decoration:none;color:var(--muted);padding:8px 10px;border-radius:8px;margin:2px 0}.v962-nav a.active{background:var(--card2);color:var(--text)}main{min-width:0;padding:18px 22px 60px}.tab-panel{display:none}.tab-panel.active{display:block}.card{border:1px solid var(--line);background:var(--card);border-radius:12px;padding:14px;margin:10px 0}.card.wide{grid-column:1/-1}.grid,.v951-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:12px}.row{display:flex;gap:9px;align-items:end;flex-wrap:wrap}.field{display:flex;flex-direction:column;gap:4px}.field.grow{flex:1;min-width:220px}label{color:var(--muted);font-size:12px}input,select,textarea{border:1px solid var(--line);border-radius:8px;background:#0f141a;color:var(--text);padding:8px;max-width:100%}textarea{width:100%}.panel-title,.v951-pagehead{display:flex;justify-content:space-between;gap:12px;align-items:flex-start;flex-wrap:wrap}.panel-title h2,.v951-pagehead h2{margin:0}.v951-pagehead p{margin:4px 0;color:var(--muted)}.v951-toolbar{display:flex;gap:8px;align-items:end;flex-wrap:wrap;border:1px solid var(--line);background:var(--card);border-radius:10px;padding:10px;margin:10px 0}.v951-toolbar label{display:flex;flex-direction:column;gap:4px}.v951-metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:8px;margin:12px 0}.v951-metric{border:1px solid var(--line);border-radius:10px;padding:10px;background:var(--card)}.v951-metric span,.v951-metric small{display:block;color:var(--muted);font-size:11px}.v951-metric strong{display:block;font-size:20px;margin:3px 0}.small{font-size:11px}.muted{color:var(--muted)}.mono,code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace}.badge{display:inline-block;border:1px solid var(--line);border-radius:999px;padding:3px 7px;font-size:11px}.notice,.flash{border:1px solid var(--line);border-radius:10px;padding:10px;margin:10px 0;background:rgba(104,160,255,.06)}.flash{border-color:#d6a84d}.scroll{overflow:auto}table{width:100%;border-collapse:collapse}th,td{text-align:left;border-bottom:1px solid var(--line);padding:8px;vertical-align:top}th{font-size:11px;color:var(--muted)}.actions{display:flex;gap:5px;flex-wrap:wrap}.actions form{display:inline}.form-section{border-top:1px solid var(--line);padding-top:10px;margin-top:10px}.form-section h3{margin:0 0 7px}.v951-checks{display:flex;gap:10px;flex-wrap:wrap;margin:8px 0}.v951-checks label{color:var(--text)}details summary{cursor:pointer}.v962-collapsible{border:1px solid var(--line);border-radius:12px;background:var(--card);margin:10px 0}.v962-collapsible>summary{font-weight:800;padding:12px 14px;list-style:none}.v962-collapsible>summary::-webkit-details-marker{display:none}.v962-collapsible>summary::after{content:" ▸";color:var(--muted)}.v962-collapsible[open]>summary::after{content:" ▾"}.v962-collapsible-body{padding:0 10px 8px}.v962-collapsible-body>.card{border:0;margin:0;padding:4px}.v962-danger{border-color:var(--danger)}.v962-loading{min-height:130px;display:grid;place-items:center;color:var(--muted)}.v962-perfline{font-size:10px;color:var(--muted);margin:4px 0 10px}.project-summary{display:flex;gap:8px;flex-wrap:wrap}.project-summary>div{border:1px solid var(--line);border-radius:8px;padding:8px}.project-summary strong,.project-summary span{display:block}.markdown-viewer{line-height:1.55}.markdown-viewer pre,.v951-message{white-space:pre-wrap;overflow-wrap:anywhere}.v951-formgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:9px}.v951-formgrid .wide{grid-column:1/-1}
@media(max-width:820px){.shell{grid-template-columns:1fr}.v962-nav{position:static;height:auto;border-right:0;border-bottom:1px solid var(--line);display:flex;gap:4px;overflow:auto;padding:8px}.v962-brand{display:none}.v962-nav a{white-space:nowrap}main{padding:12px}}
'''

SCRIPT = r'''
<script id="v962-lazy-dashboard">
(() => {
  const views = new Set(%VIEWS%);
  const controllers = new Map();
  const generations = new Map();

  function viewFromUrl(url) {
    const u = new URL(url || location.href, location.href);
    const hash = (u.hash || '').slice(1);
    if (views.has(hash)) return hash;
    const query = u.searchParams.get('ui_view');
    return views.has(query) ? query : 'overview';
  }
  function activate(view) {
    document.querySelectorAll('.tab-panel').forEach(p => p.classList.toggle('active', p.dataset.panel === view));
    document.querySelectorAll('[data-v962-nav]').forEach(a => a.classList.toggle('active', a.dataset.v962Nav === view));
  }
  function fragmentUrl(view, url) {
    const u = new URL(url || location.href, location.href);
    u.pathname = '/dashboard/view/' + encodeURIComponent(view);
    u.hash = '';
    u.searchParams.set('ui_view', view);
    return u.toString();
  }
  function detailKey(el) { return el.dataset.v962StateKey ? 'postmaster:v962:' + el.dataset.v962StateKey : ''; }
  function bindDetails(root=document) {
    root.querySelectorAll('details[data-v962-state-key]').forEach(el => {
      const key = detailKey(el);
      if (key && el.dataset.v962ForceOpen !== '1') {
        const saved = sessionStorage.getItem(key);
        if (saved === '1') el.open = true;
        if (saved === '0') el.open = false;
      }
      if (!el.dataset.v962Bound) {
        el.dataset.v962Bound = '1';
        el.addEventListener('toggle', () => { if (key) sessionStorage.setItem(key, el.open ? '1' : '0'); });
      }
    });
  }
  function slugify(value) {
    return String(value || '').normalize('NFKD').replace(/[\u0300-\u036f]/g,'').toLowerCase().replace(/[^a-z0-9]+/g,'-').replace(/^-+|-+$/g,'').replace(/-+/g,'-').slice(0,64).replace(/-+$/g,'');
  }
  function bindProjectSlug(root=document) {
    root.querySelectorAll('[data-v962-project-name]').forEach(name => {
      const form = name.closest('form'); if (!form) return;
      const slug = form.querySelector('[data-v962-project-slug]'); if (!slug) return;
      if (!slug.dataset.v962Bound) {
        slug.dataset.v962Bound='1'; slug.addEventListener('input', () => { slug.dataset.v962Edited='1'; });
        name.addEventListener('input', () => { if (slug.dataset.v962Edited !== '1') slug.value = slugify(name.value); });
      }
    });
  }
  function bindReader(root=document) {
    root.querySelectorAll('.v960-detail-shell').forEach(shell => {
      shell.querySelectorAll('[data-v960-detail-tab]').forEach(button => {
        button.onclick = () => {
          const tab = button.dataset.v960DetailTab;
          shell.querySelectorAll('[data-v960-detail-tab]').forEach(x => x.classList.toggle('active', x===button));
          shell.querySelectorAll('[data-v960-detail-pane]').forEach(x => x.classList.toggle('active', x.dataset.v960DetailPane===tab));
        };
      });
    });
    root.querySelectorAll('tr[data-v960-href]').forEach(row => {
      row.tabIndex=0;
      const go=() => load(viewFromUrl(row.dataset.v960Href), row.dataset.v960Href, true, true);
      row.onclick=ev=>{if(!ev.target.closest('a,button,input,form,select,textarea'))go();};
      row.onkeydown=ev=>{if((ev.key==='Enter'||ev.key===' ')&&!ev.target.closest('a,button,input,form,select,textarea')){ev.preventDefault();go();}};
    });
    root.querySelectorAll('form[data-v960-send]').forEach(form => {
      if (!form.dataset.v962SendBound) { form.dataset.v962SendBound='1'; form.addEventListener('submit',()=>form.querySelectorAll('button[type="submit"]').forEach(b=>b.disabled=true)); }
    });
  }
  function bindAll(root=document) { bindDetails(root); bindProjectSlug(root); bindReader(root); }

  async function load(view, url=location.href, push=false, force=false) {
    if (!views.has(view)) view='overview';
    activate(view);
    const target = document.querySelector('#panel-' + CSS.escape(view));
    if (!target) return;
    if (!force && target.dataset.v962Loaded === '1') {
      if (push) history.pushState({v962:view},'',url);
      return;
    }
    for (const [other, controller] of controllers) if (other !== view) { controller.abort(); controllers.delete(other); }
    const old = controllers.get(view); if (old) old.abort();
    const controller = new AbortController(); controllers.set(view, controller);
    const generation = (generations.get(view) || 0) + 1; generations.set(view, generation);
    const started = performance.now();
    try {
      const res = await fetch(fragmentUrl(view,url), {signal:controller.signal,headers:{'X-Postmaster-Fragment':'1'}});
      if (!res.ok) throw new Error('fragment ' + res.status);
      const html = await res.text();
      if (controller.signal.aborted || generations.get(view) !== generation) return;
      const box=document.createElement('div'); box.innerHTML=html.trim();
      const next=box.firstElementChild;
      if (!next || next.dataset.panel !== view) throw new Error('invalid fragment');
      // Preserve the v9.6.1 active-fragment invariant during replacement.
      if (target.classList.contains('active')) next.classList.add('active');
      next.dataset.v962Loaded='1';
      const timing=res.headers.get('Server-Timing') || '';
      const line=document.createElement('div'); line.className='v962-perfline';
      line.textContent='Fragment ' + (performance.now()-started).toFixed(1) + ' ms' + (timing ? ' · ' + timing : '');
      next.insertBefore(line,next.firstChild);
      target.replaceWith(next);
      bindAll(next);
      if (push) history.pushState({v962:view},'',url);
    } catch (err) {
      if (err && err.name === 'AbortError') return;
      target.innerHTML='<div class="flash">Could not load this dashboard fragment. <button type="button" data-v962-retry>Retry</button></div>';
      const retry=target.querySelector('[data-v962-retry]'); if(retry) retry.onclick=()=>load(view,url,false,true);
    } finally {
      if (controllers.get(view) === controller) controllers.delete(view);
    }
  }

  document.addEventListener('click', ev => {
    const a=ev.target.closest('a'); if(!a) return;
    let u; try { u=new URL(a.href,location.href); } catch (_) { return; }
    if (u.origin !== location.origin) return;
    const view=viewFromUrl(u.toString());
    if (!views.has(view)) return;
    const explicit = a.hasAttribute('data-v962-nav') || a.hasAttribute('data-v960-fragment') || views.has((u.hash||'').slice(1));
    if (!explicit) return;
    ev.preventDefault();
    const isNav=a.hasAttribute('data-v962-nav');
    load(view,u.toString(),true,!isNav);
  });

  document.addEventListener('submit', ev => {
    const form=ev.target; if(!(form instanceof HTMLFormElement))return;
    if((form.method||'get').toLowerCase()!=='get')return;
    ev.preventDefault();
    const u=new URL(form.action||'/',location.origin); const data=new FormData(form);
    for(const [k,v] of data.entries()) if(String(v).length)u.searchParams.set(k,String(v));
    const view=viewFromUrl(u.toString()); u.hash=view;
    load(view,u.toString(),true,true);
  });

  window.addEventListener('popstate',()=>load(viewFromUrl(location.href),location.href,false,true));
  bindAll();
  load(viewFromUrl(location.href),location.href,false,true);
})();
</script>
'''


def _styles() -> str:
    return BASE_STYLE + "\n" + "\n".join(str(getattr(module, "STYLE", "")) for module in (v951, v952, v953, v954, v960, v961))


def _nav() -> str:
    links = "".join(
        f'<a href="#{escape(view)}" data-v962-nav="{escape(view)}">{escape(label)}</a>'
        for view, label in NAV
    )
    return f'<nav class="v962-nav"><div class="v962-brand"><strong>Postmaster</strong><small>WebGUI v9.6.2 · lazy fragments</small></div>{links}</nav>'


def _shell(request: Request) -> HTMLResponse:
    placeholders = "".join(
        f'<section class="tab-panel" id="panel-{escape(view)}" data-panel="{escape(view)}" data-v962-loaded="0"><div class="v962-loading">Loading {escape(view)}…</div></section>'
        for view in VIEWS
    )
    flash = (request.query_params.get("flash") or "").strip()
    banner = f'<div class="flash">{escape(flash)}</div>' if flash else ""
    script = SCRIPT.replace("%VIEWS%", repr(list(VIEWS)))
    html = f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Postmaster</title><style>{_styles()}</style></head><body><div class="shell">{_nav()}<main>{banner}{placeholders}</main></div>{script}</body></html>'''
    return HTMLResponse(html, headers={"Cache-Control": "private, no-store", "X-Postmaster-WebGUI": "9.6.2-lazy"})


def _insert_route(app: Any, route: Route) -> None:
    for index, current in enumerate(app.router.routes):
        if isinstance(current, Mount):
            app.router.routes.insert(index, route)
            return
    app.router.routes.append(route)


def _replace_get_root(app: Any, endpoint: Any) -> None:
    for index, route in enumerate(app.router.routes):
        if isinstance(route, Route) and route.path == "/" and "GET" in (route.methods or set()):
            app.router.routes[index] = Route("/", endpoint, methods=["GET"], name=route.name)
            return
    _insert_route(app, Route("/", endpoint, methods=["GET"]))


def install_webgui_v962(app: Any, base: Any, core: Any, previous_dashboard_home: Any):
    """Replace the monolithic dashboard with a query-free shell plus isolated tab fragments."""

    async def dashboard_home_v962(request: Request):
        return _shell(request)

    async def dashboard_fragment_v962(request: Request):
        view = str(request.path_params.get("view") or "").strip()
        if view not in VIEWS:
            return HTMLResponse("Unknown dashboard view", status_code=404)
        started = time.perf_counter()
        try:
            html = render_view(base, core, request, view)
        except Exception as exc:
            core.logger.exception("v9.6.2 WebGUI fragment failed: %s", view)
            return HTMLResponse(f'<section class="tab-panel" id="panel-{escape(view)}" data-panel="{escape(view)}"><div class="flash">{escape(type(exc).__name__ + ": " + str(exc))}</div></section>', status_code=500)
        elapsed = (time.perf_counter() - started) * 1000.0
        return HTMLResponse(
            html,
            headers={
                "Cache-Control": "private, no-store",
                "Server-Timing": f"fragment;dur={elapsed:.2f}",
                "X-Postmaster-Fragment": view,
            },
        )

    async def project_create(request: Request):
        return await dashboard_project_create(base, request)

    async def project_update(request: Request):
        return await dashboard_project_update(base, request)

    async def project_delete(request: Request):
        return await dashboard_project_delete(base, request)

    _replace_get_root(app, dashboard_home_v962)
    _insert_route(app, Route("/dashboard/view/{view}", dashboard_fragment_v962, methods=["GET"]))
    _insert_route(app, Route("/dashboard/project/create", project_create, methods=["POST"]))
    _insert_route(app, Route("/dashboard/project/update", project_update, methods=["POST"]))
    _insert_route(app, Route("/dashboard/project/delete", project_delete, methods=["POST"]))
    return dashboard_home_v962
