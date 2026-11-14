"""v9.8.4 release status and guarded version changes in authenticated WebGUI.

Uses the v9.6.9 server-side pending preview/execute backend. Never performs a
version change without CSRF-verified preview and separate explicit confirmation.
"""
from __future__ import annotations

from html import escape
import os
from datetime import UTC, datetime
import threading
import time
from typing import Any
from starlette.requests import Request
from starlette.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response, JSONResponse
from starlette.routing import Mount, Route

from . import runtime_control
from . import release_channels_v986 as channels
from .update_status import latest_version_status


def _e(value: Any) -> str:
    return escape(str(value or ""), quote=True)


def version_summary(base: Any) -> str:
    try:
        status = base.build_status()
        current = str(status.get("version") or "unknown") if isinstance(status, dict) else "unknown"
        selector = str(status.get("requested_version") or "unknown") if isinstance(status, dict) else "unknown"
        remote = latest_version_status(current)
        latest = str(remote.get("latest_version") or "unavailable")
        update = remote.get("update_available")
        state = "Available" if update is True else ("Up to date" if update is False else "Unknown")
        checked = str(remote.get("update_checked_at") or "Not checked")
    except Exception:
        current, selector, latest, state, checked = "unknown", "unknown", "unavailable", "Unknown", "Unavailable"
    return (
        '<section class="card wide v984-release-summary">'
        '<div class="panel-title"><h3>Version status</h3>'
        '<a href="/?ui_view=system#system">Versions & updates</a></div>'
        '<div class="v984-release-grid">'
        f'<div><small>Installed version</small><strong>{_e(current)}</strong></div>'
        f'<div><small>Latest eligible update</small><strong>{_e(latest)}</strong></div>'
        f'<div><small>Update status</small><strong>{_e(state)}</strong></div>'
        f'<div><small>Requested selector</small><strong>{_e(selector)}</strong></div></div>'
        f'<p class="small muted">Last successful check: {_e(checked)}. '
        'The selected update channel determines eligible releases. '
        'Draft PRs are never installable updates.</p>'
        '</section>'
    )


_CATALOG_LOCK = threading.RLock()
_CATALOG_AT = 0.0
_CATALOG_TAGS: list[str] = []
_CATALOG_STATE = "never"
_CATALOG_CHECKED = ""


def _release_snapshot(base: Any) -> dict[str, Any]:
    """Both channels, one 30s per-process request budget, last-known-good tags."""
    global _CATALOG_AT, _CATALOG_TAGS, _CATALOG_STATE, _CATALOG_CHECKED
    now = time.monotonic()
    with _CATALOG_LOCK:
        if not _CATALOG_AT or now - _CATALOG_AT >= 30:
            _CATALOG_AT = now
            tags, state = runtime_control.available_release_tags(
                channel=channels.STABLE_AND_BETA, force=True
            )
            _CATALOG_STATE = state
            if state == "ok":
                _CATALOG_TAGS = list(tags)
                _CATALOG_CHECKED = datetime.now(UTC).isoformat()
        tags = list(_CATALOG_TAGS)
        state = _CATALOG_STATE
        checked = _CATALOG_CHECKED
    stable = next((v for v in tags if channels.parse_release_tag(v) and
                   channels.parse_release_tag(v)[1] == channels.STABLE), None)
    beta = next((v for v in tags if channels.parse_release_tag(v) and
                 channels.parse_release_tag(v)[1] == "beta"), None)
    build = base.build_status()
    running = str(build.get("version") or "unknown") if isinstance(build, dict) else "unknown"
    selected = runtime_control.preferred_release_channel()
    wanted = str(runtime_control.read_control().get("selector") or "latest")
    active_ref = "v" + running.removeprefix("v") if running != "unknown" else "unknown"
    mismatch = wanted != "latest" and wanted != active_ref
    bootstrap_supported = os.getenv("POSTMASTER_BOOTSTRAP_CONTROL_V2", "").lower() == "true"
    return {
        "selected_target": wanted,
        "activation_mismatch": mismatch,
        "bootstrap_control_supported": bootstrap_supported,
        "ok": True, "current_version": running, "release_channel": selected,
        "stable": stable, "beta": beta,
        "stable_update": channels.newer_than(stable, running) is True,
        "beta_update": (selected == channels.STABLE_AND_BETA and
                        channels.newer_than(beta, running) is True),
        "beta_allowed": selected == channels.STABLE_AND_BETA,
        "checked_at": checked, "check_status": state, "interval_seconds": 30,
    }


def versions_settings(base: Any) -> str:
    data = _release_snapshot(base)
    channel = data["release_channel"]
    selector = runtime_control.read_control().get("selector") or "latest"
    stable = str(data["stable"] or "")
    beta = str(data["beta"] or "")
    stable_enabled = bool(data["stable_update"] and data["check_status"] == "ok")
    beta_enabled = bool(data["beta_update"] and data["check_status"] == "ok")
    csrf = _e(base._csrf_value())
    chosen_stable = ' selected' if channel == "stable" else ""
    chosen_beta = ' selected' if channel == "stable_beta" else ""

    def update_form(kind: str, target: str, enabled: bool) -> str:
        label = "Update Stable" if kind == "stable" else "Update Beta"
        disabled = "" if enabled else " disabled"
        return (
            '<form method="post" action="/dashboard/versions/preview" '
            'data-v988-update="' + kind + '">'
            f'<input type="hidden" name="csrf" value="{csrf}">'
            '<input type="hidden" name="operation" value="switch-version">'
            f'<input type="hidden" name="target_version" value="{_e(target)}">'
            f'<button type="submit" class="primary"{disabled}>{label}</button>'
            '</form>'
        )

    return (
        '<section class="card wide" id="v984-versions" data-v988-updates>'
        '<h3>Updates</h3>'
        '<p class="small muted" data-v9894-activation '
        + ('style="display:block"' if data["activation_mismatch"] else 'style="display:none"') + '>'
        + (('Update not active: running ' + _e(data["current_version"])
            + ', selected ' + _e(data["selected_target"])
            + ('. The Portainer bootstrap may not support WebUI/MCP updates. '
               'Update the stack command before retrying.'
               if not data["bootstrap_control_supported"]
               else '. Waiting for a completed container bootstrap.'))
           if data["activation_mismatch"] else '') + '</p>'
        f'<p>Installed: <strong data-v988-current>{_e(data["current_version"])}</strong>'
        f' · Runtime: <strong>{_e(selector)}</strong></p>'
        '<p class="small muted">Postmaster checks the latest Stable and Beta '
        'releases every 30 seconds while Settings is open. '
        'An update requires explicit confirmation before restarting.</p>'
        '<div class="v988-release-grid">'
        '<div class="v988-release-card">'
        '<strong>Latest Stable</strong>'
        f'<div class="v988-release-version" data-v988-version="stable">{_e(stable or "Unavailable")}</div>'
        f'<p class="small muted" data-v988-message="stable">{"Update available" if stable_enabled else "No newer Stable release"}</p>'
        + update_form("stable", stable, stable_enabled) +
        '</div>'
        '<div class="v988-release-card">'
        '<strong>Latest Beta</strong>'
        f'<div class="v988-release-version" data-v988-version="beta">{_e(beta or "Unavailable")}</div>'
        f'<p class="small muted" data-v988-message="beta">{"Update available" if beta_enabled else "Enable Beta in update channel" if channel == "stable" else "No newer Beta release"}</p>'
        + update_form("beta", beta, beta_enabled) +
        '</div></div>'
        '<form method="post" action="/dashboard/versions/channel" data-v988-channel>'
        f'<input type="hidden" name="csrf" value="{csrf}">'
        '<label>Update channel <select name="release_channel">'
        f'<option value="stable"{chosen_stable}>Stable only</option>'
        f'<option value="stable_beta"{chosen_beta}>Stable + Beta</option>'
        '</select></label>'
        '<button type="submit">Save preference</button></form>'
        '<p class="small muted" data-v988-check>'
        f'Last check: {_e(data["checked_at"] or "Never")} · {_e(data["check_status"])}'
        '</p><p class="small muted" data-v988-feedback role="status" aria-live="polite"></p>'
        '<p class="small muted">Beta installs stay pinned. '
        'No changes to the original Portainer YAML are necessary.</p>'
        '</section>'
    )


VERSIONS_STYLE = r"""
#v984-versions .v988-release-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px;margin:18px 0}
#v984-versions .v988-release-card{border:1px solid var(--v970-line);border-radius:12px;padding:17px;background:var(--v970-surface-2)}
#v984-versions .v988-release-card>strong{font-size:13px;color:var(--v970-muted)}
#v984-versions .v988-release-version{font-size:24px;font-weight:800;letter-spacing:-.5px;margin:9px 0}
#v984-versions .v988-release-card button:disabled{opacity:.45;cursor:not-allowed}
#v984-versions [data-v988-channel]{display:flex;gap:10px;align-items:end;flex-wrap:wrap}
#v984-versions [data-v988-channel] label{display:flex;flex-direction:column;gap:5px}
@media(max-width:720px){#v984-versions .v988-release-grid{grid-template-columns:1fr}}
"""


VERSIONS_SCRIPT = r"""<script id="v988-live-updates">
(() => {
  'use strict';
  let pending=null, last=0, current=null;
  function root(){return document.querySelector('#panel-system.active [data-v988-updates]');}
  async function refresh(force=false) {
    if (!root() || document.hidden)return null;
    if(pending)return pending;
    if(!force && current && Date.now()-last<28000)return current;
    pending=fetch('/dashboard/versions/status',{credentials:'same-origin',cache:'no-store',
      headers:{'Accept':'application/json'}}).then(async res=>{
      if(!res.ok)throw new Error('HTTP '+res.status);
      const data=await res.json();
      if(!data.ok)throw new Error('Update service unavailable');
      current=data;last=Date.now();
      const live=root();if(!live)return data;
      live.querySelector('[data-v988-current]').textContent=data.current_version;
      const activation=live.querySelector('[data-v9894-activation]');
      if(activation){
        activation.style.display=data.activation_mismatch?'block':'none';
        activation.textContent=data.activation_mismatch
          ? 'Update not active: running '+data.current_version+
            ', selected '+data.selected_target+
            (!data.bootstrap_control_supported
              ? '. The Portainer bootstrap may not support WebUI/MCP updates. Update the stack command before retrying.'
              : '. Waiting for a completed container bootstrap.')
          : '';
      }
      live.querySelector('[data-v988-check]').textContent='Last check: '+(data.checked_at||'Never')+' · '+data.check_status;
      for(const kind of ['stable','beta']) {
        const value=data[kind]||'';
        live.querySelector('[data-v988-version="'+kind+'"]').textContent=value||'Unavailable';
        const form=live.querySelector('form[data-v988-update="'+kind+'"]');
        const allowed=kind==='stable'||data.beta_allowed;
        const newer=kind==='stable'?data.stable_update:data.beta_update;
        const enabled=Boolean(allowed && newer && value && data.check_status==='ok');
        form.querySelector('[name=target_version]').value=value;
        form.querySelector('[name=csrf]').value=data.csrf;
        form.querySelector('button').disabled=!enabled;
        live.querySelector('[data-v988-message="'+kind+'"]').textContent=
          !allowed?'Enable Stable + Beta to install prereleases':
          !value?'No published '+kind+' release':
          data.check_status!=='ok'?'Release lookup unavailable; retrying':
          enabled?'Update available':'Already up to date';
      }
      live.querySelector('form[data-v988-channel] [name=csrf]').value=data.csrf;
      return data;
    }).catch(err=>{
      root()?.querySelector('[data-v988-feedback]')?.replaceChildren(
        document.createTextNode('Could not check for updates: '+err.message));
      return null;
    }).finally(()=>{pending=null});
    return pending;
  }
  setInterval(()=>{if(root()&&!document.hidden)refresh(true)},30000);
  document.addEventListener('visibilitychange',()=>{if(!document.hidden)refresh(true)});
  const main=document.querySelector('.v970-workspace > main');
  if(main)new MutationObserver(()=>{if(root())refresh()}).observe(main,{childList:true,subtree:false});
  document.addEventListener('submit',async ev=>{
    const form=ev.target.closest?.('form[data-v988-update],form[data-v988-channel]');
    if(!form)return;
    ev.preventDefault();
    const clicked=form.dataset.v988Update;
    // Save the version the user actually clicked before refreshing.
    const target=clicked?form.querySelector('[name=target_version]').value:'';
    const data=await refresh(true);
    const feedback=root()?.querySelector('[data-v988-feedback]');
    if(!data){if(feedback)feedback.textContent='Session update unavailable. Try again.';return;}
    form.querySelector('[name=csrf]').value=data.csrf;
    if(clicked && (data[clicked]!==target ||
         !(clicked==='stable'?data.stable_update:data.beta_update) ||
         (clicked==='beta'&&!data.beta_allowed) || data.check_status!=='ok')){
      if(feedback)feedback.textContent='The latest release changed. Review the new version and click Update again.';
      return;
    }
    HTMLFormElement.prototype.submit.call(form);
  },true);
  refresh();
})();
</script>"""


def _response_page(title: str, content: str, status: int = 200) -> HTMLResponse:
    return HTMLResponse(
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>{_e(title)} · Postmaster</title>'
        '<style>body{font:14px/1.55 system-ui;background:#101820;color:#eef2f8;'
        'max-width:720px;margin:42px auto;padding:20px}'
        'section{padding:20px;background:#1b2937;border-radius:12px}'
        'input,button{padding:9px;margin:8px;border-radius:6px}'
        'button{cursor:pointer}a{color:#98baff}code{overflow-wrap:anywhere}</style></head>'
        f'<body><section><h1>{_e(title)}</h1>{content}</section></body></html>',
        status_code=status,
        headers={"Cache-Control":"private, no-store","X-Content-Type-Options":"nosniff",
                 "Referrer-Policy":"no-referrer","Content-Security-Policy":
                 "default-src 'self'; base-uri 'none'; frame-ancestors 'self'"},
    )


def install_versions_webgui_v984(app: Any, base: Any, core: Any,
                                v962: Any, views: Any, v970: Any) -> None:
    if any(isinstance(r, Route) and r.path == "/dashboard/versions/preview"
           for r in app.router.routes):
        return

    previous_overview = views.render_overview

    def render_overview(b: Any, req: Request) -> str:
        html = previous_overview(b, req)
        return html[:html.rfind("</section>")] + version_summary(b) + html[html.rfind("</section>"):]

    views.render_overview = render_overview
    previous_system = views.v951.render_system

    def render_system(b: Any, req: Request) -> str:
        html = previous_system(b, req)
        last = html.rfind("</section>")
        return html[:last] + versions_settings(b) + html[last:]

    views.v951.render_system = render_system
    v962.NAV = tuple((k, "Settings" if k == "system" else name) for k, name in v962.NAV)
    v970.NAV_GROUPS = tuple(
        (heading, tuple((key, "Settings" if key == "system" else label, code)
                        for key, label, code in links))
        for heading, links in v970.NAV_GROUPS
    )
    v970.VIEW_LABELS["system"] = ("Settings", "Runtime versions and system configuration")
    if hasattr(v962, "_styles"):
        previous_styles = v962._styles
        if not getattr(previous_styles, "_v988_release_styles", False):
            def styles() -> str:
                return previous_styles() + "\n" + VERSIONS_STYLE
            styles._v988_release_styles = True
            v962._styles = styles
    if hasattr(v962, "SCRIPT") and 'id="v988-live-updates"' not in v962.SCRIPT:
        v962.SCRIPT += VERSIONS_SCRIPT

    async def live_status(req: Request) -> Response:
        data = _release_snapshot(base)
        data["csrf"] = base._csrf_value()
        return JSONResponse(data, headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
        })

    async def preview(req: Request) -> Response:
        form, error = await base._verified_form(req)
        if error is not None:
            return _response_page(
                "Session expired",
                '<p>Your update page has an outdated security token. '
                'Return to Settings and retry.</p>'
                '<p><a href="/?ui_view=system&settings_tab=versions#system">'
                'Back to Updates</a></p>', status=403,
            )
        operation = str(form.get("operation") or "").strip()
        target = str(form.get("target_version") or "").strip()
        if operation not in {"update-latest", "switch-version", "pin-version", "rollback-version"}:
            return PlainTextResponse("Unsupported update operation", status_code=400)
        result = core.runtime_version_change_preview(
            operation=operation,
            target_version=None if operation == "update-latest" else target,
        )
        if not isinstance(result, dict) or not result.get("ok"):
            return _response_page(
                "Unable to prepare update",
                f'<p>{_e(result.get("error") if isinstance(result,dict) else "Unknown error")}</p>'
                '<p><a href="/?ui_view=system#system">Back to Settings</a></p>',
                status=400,
            )
        info = dict(result.get("action_preview") or {})
        # Since v9.6.9, the MCP lifecycle uses a server-side pending preview.
        # The public preview_id is correlation metadata, NOT an approval bearer
        # token. The former confirmation_token no longer exists in the runtime.
        preview_id = str(result.get("preview_id") or "")
        exact_target = str(info.get("target_version_ref") or "")
        if not preview_id or not exact_target:
            return _response_page(
                "Update unavailable",
                "<p>The runtime did not provide a complete server-side pending preview. "
                "No version change has been scheduled.</p>"
                '<p><a href="/?ui_view=system#system">Back to Settings</a></p>',
                status=400,
            )
        panel = (
            '<p>Postmaster will install this version and restart after your confirmation.</p>'
            f'<p><b>Operation:</b> {_e(info.get("operation"))}<br>'
            f'<b>Current:</b> {_e(info.get("current_version"))}<br>'
            f'<b>Build:</b> {_e(info.get("current_build"))}<br>'
            f'<b>Current selector:</b> {_e(info.get("current_selector"))}<br>'
            f'<b>Target:</b> {_e(exact_target)}<br>'
            f'<b>Confirmation TTL:</b> {_e(result.get("confirmation_expires_in_seconds"))} s</p>'
            '<form action="/dashboard/versions/execute" method="post">'
            f'<input type="hidden" name="csrf" value="{_e(base._csrf_value())}">'
            f'<input type="hidden" name="operation" value="{_e(operation)}">'
            f'<input type="hidden" name="target_version" value="{_e(exact_target)}">'
            f'<input type="hidden" name="preview_id" value="{_e(preview_id)}">'
            '<label><input type="checkbox" name="approved" value="yes" required>'
            'I approve this update and restart.</label>'
            '<p><button type="submit">Confirm update &amp; restart</button>'
            ' <a href="/?ui_view=system#system">Cancel</a></p></form>'
        )
        return _response_page("Confirm update and restart", panel)

    async def execute(req: Request) -> Response:
        form, error = await base._verified_form(req)
        if error is not None:
            return error
        if str(form.get("approved") or "") != "yes":
            return PlainTextResponse("Explicit confirmation required", status_code=400)
        operation = str(form.get("operation") or "").strip()
        target = str(form.get("target_version") or "").strip()
        preview_id = str(form.get("preview_id") or "").strip()
        if not target or not preview_id:
            return PlainTextResponse("Invalid or missing server-side preview", status_code=400)
        # Only explicit approval in this separate, CSRF-checked POST may execute
        # the exact version the user reviewed. The runtime atomically consumes
        # and validates the matching pending preview before scheduling restart.
        result = core.runtime_version_change_execute(
            operation=operation, target_version=target, preview_id=preview_id,
        )
        if not isinstance(result, dict) or not result.get("ok"):
            return _response_page(
                "Version change not applied",
                f'<p>{_e(result.get("error") if isinstance(result,dict) else "Unknown error")}</p>'
                '<p><a href="/?ui_view=system#system">Back to Settings</a></p>',
                status=400,
            )
        return _response_page(
            "Version change scheduled",
            f'<p>Target release: <strong>{_e(result.get("target_version_ref"))}</strong></p>'
            '<p>Postmaster will restart through the existing runtime selector. '
            'Reconnect and verify the installed build after restart.</p>',
        )

    async def save_channel(req: Request) -> Response:
        form,error=await base._verified_form(req)
        if error is not None:
            return error
        chosen=str(form.get("release_channel") or "").strip()
        try:
            # Never make switching to Stable only silently downgrade an
            # already running beta on the next ordinary latest-selector boot.
            current=base.build_status()
            running=str(current.get("version") or "") if isinstance(current,dict) else ""
            if chosen=="stable" and "-beta." in running:
                raise ValueError(
                    "A Beta runtime is installed. Preview and install a published "
                    "Stable release before disabling Beta updates."
                )
            runtime_control.save_release_channel(chosen)
        except (ValueError,OSError) as exc:
            return _response_page(
                "Update preference not saved",
                f'<p>{_e(str(exc))}</p>'
                '<p><a href="/?ui_view=system&settings_tab=versions#system">Back to Settings</a></p>',
                status=400,
            )
        return RedirectResponse(
            "/?ui_view=system&settings_tab=versions#system",status_code=303,
        )

    i = next((i for i, route in enumerate(app.router.routes) if isinstance(route, Mount)),
             len(app.router.routes))
    app.router.routes.insert(i, Route("/dashboard/versions/preview", preview, methods=["POST"]))
    app.router.routes.insert(i+1, Route("/dashboard/versions/execute", execute, methods=["POST"]))
    app.router.routes.insert(i+2, Route("/dashboard/versions/channel", save_channel, methods=["POST"]))
    app.router.routes.insert(i+3, Route("/dashboard/versions/status", live_status, methods=["GET"]))
