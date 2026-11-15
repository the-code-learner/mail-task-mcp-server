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
        "release_tags": tags,
        # A user running a Beta may explicitly return to the latest Stable,
        # even though Stable is numerically older. This is a switch, not an upgrade.
        "stable_update": bool(stable and stable != active_ref and
                              (channels.newer_than(stable, running) is True or
                               "-beta." in running)),
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
    eligible = [tag for tag in data["release_tags"]
                if channels.release_is_allowed(tag, channel)]
    def exact_options(kind: str) -> str:
        return "".join(
            '<option value="' + _e(tag) + '">' + _e(tag) + '</option>'
            for tag in eligible if channels.parse_release_tag(tag)[1] == kind
        )
    exact_select = (
        '<option value="">Select an exact release...</option>'
        '<optgroup label="Stable releases">' + exact_options("stable") + '</optgroup>'
        + ('<optgroup label="Beta releases">' + exact_options("beta") + '</optgroup>'
           if channel == channels.STABLE_AND_BETA else '')
    )
    exact_enabled = bool(eligible and data["check_status"] == "ok")

    def update_form(kind: str, target: str, enabled: bool) -> str:
        label = ("Switch to Stable" if "-beta." in str(data["current_version"])
                 else "Update Stable") if kind == "stable" else "Update Beta"
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
        f'<p class="small muted" data-v988-message="stable">{"Switch back to Stable" if stable_enabled and "-beta." in data["current_version"] else "Update available" if stable_enabled else "Latest Stable already installed" if stable == "v" + data["current_version"].removeprefix("v") else "Stable is older than the current version"}</p>'
        + update_form("stable", stable, stable_enabled) +
        '</div>'
        '<div class="v988-release-card">'
        '<strong>Latest Beta</strong>'
        f'<div class="v988-release-version" data-v988-version="beta">{_e(beta or "Unavailable")}</div>'
        f'<p class="small muted" data-v988-message="beta">{"Update available" if beta_enabled else "Enable Beta in update channel" if channel == "stable" else "No newer Beta release"}</p>'
        + update_form("beta", beta, beta_enabled) +
        '</div></div>'
        '<section class="v988-exact-pin" aria-label="Pin an exact version">'
        '<h4>Install or pin an exact release</h4>'
        '<p class="small muted">Choose any published version, including older releases. '
        'The chosen release remains pinned until explicitly changed. '
        'A separate confirmation is required before restart.</p>'
        '<form method="post" action="/dashboard/versions/preview" data-v988-pin>'
        f'<input type="hidden" name="csrf" value="{csrf}">'
        '<input type="hidden" name="operation" value="pin-version">'
        '<label>Exact release <select name="target_version" required'
        + ('' if exact_enabled else ' disabled') + '>'
        + exact_select + '</select></label>'
        '<button type="submit" class="primary" disabled>Preview &amp; pin version</button>'
        '</form></section>'
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
#v984-versions .v988-exact-pin{margin:18px 0;padding:16px;border:1px solid var(--v970-line);border-radius:12px;background:var(--v970-surface-2)}
#v984-versions .v988-exact-pin h4{margin:0 0 8px;font-size:15px}
#v984-versions [data-v988-pin]{display:flex;align-items:end;gap:12px;flex-wrap:wrap}
#v984-versions [data-v988-pin] label{display:flex;flex-direction:column;gap:5px;flex:1;min-width:205px}
#v984-versions [data-v988-pin] select{width:100%;max-width:480px}
#v984-versions [data-v988-pin] button:disabled{opacity:.45;cursor:not-allowed}
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
        const button=form.querySelector('button');
        button.disabled=!enabled;
        if(kind==='stable')button.textContent=data.current_version.includes('-beta.')
          ?'Switch to Stable':'Update Stable';
        live.querySelector('[data-v988-message="'+kind+'"]').textContent=
          !allowed?'Enable Stable + Beta to install prereleases':
          !value?'No published '+kind+' release':
          data.check_status!=='ok'?'Release lookup unavailable; retrying':
          enabled?(kind==='stable'&&data.current_version.includes('-beta.')?'Switch back to Stable':'Update available'):
          kind==='stable'&&!data.current_version.includes('-beta.')&&
            value==='v'+data.current_version.replace(/^v/,'')?'Latest Stable already installed':
          kind==='stable'?'Stable is older than the current version':'Already up to date';
      }
      live.querySelector('form[data-v988-channel] [name=csrf]').value=data.csrf;
      const pin=live.querySelector('form[data-v988-pin]');
      if(pin){
        const select=pin.querySelector('[name=target_version]');
        const previous=select.value;
        const valid=(data.release_tags||[]).filter(tag=>
          /^v\d+\.\d+\.\d+(?:-beta\.[1-9]\d*)?$/.test(tag) &&
          (data.beta_allowed || !tag.includes('-beta.')));
        select.replaceChildren(new Option('Select an exact release...',''));
        for(const [name,values] of [
          ['Stable releases',valid.filter(v=>!v.includes('-beta.'))],
          ['Beta releases',valid.filter(v=>v.includes('-beta.'))]
        ]){
          if(!values.length)continue;
          const group=document.createElement('optgroup');group.label=name;
          values.forEach(v=>group.appendChild(new Option(v,v)));
          select.appendChild(group);
        }
        select.value=valid.includes(previous)?previous:'';
        const ready=data.check_status==='ok' && valid.length>0;
        select.disabled=!ready;
        pin.querySelector('[name=csrf]').value=data.csrf;
        pin.querySelector('button').disabled=!ready||!select.value;
      }
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
  document.addEventListener('change',ev=>{
    const select=ev.target.closest?.('form[data-v988-pin] select[name=target_version]');
    if(!select)return;
    select.form.querySelector('button').disabled=!select.value||select.disabled;
  });
  document.addEventListener('submit',async ev=>{
    const form=ev.target.closest?.('form[data-v988-update],form[data-v988-channel],form[data-v988-pin]');
    if(!form)return;
    ev.preventDefault();
    const clicked=form.dataset.v988Update;
    // Save the version the user actually clicked before refreshing.
    const pinning=form.hasAttribute('data-v988-pin');
    const target=(clicked||pinning)?form.querySelector('[name=target_version]').value:'';
    const data=await refresh(true);
    const feedback=root()?.querySelector('[data-v988-feedback]');
    if(!data){if(feedback)feedback.textContent='Session update unavailable. Try again.';return;}
    form.querySelector('[name=csrf]').value=data.csrf;
    if(pinning && (!target || data.check_status!=='ok' ||
        !(data.release_tags||[]).includes(target) ||
        (target.includes('-beta.')&&!data.beta_allowed))){
      if(feedback)feedback.textContent='This exact release is no longer eligible. Review the list and select a version again.';
      return;
    }
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


# Shared confirmation styling is used by the dashboard popup and the
# full-page no-JavaScript fallback. This does not change the approval protocol.
VERSION_CONFIRM_STYLE = r"""
.pm-vconfirm-overlay{position:fixed;inset:0;z-index:30000;background:rgba(6,13,24,.78);display:grid;place-items:center;padding:16px;box-sizing:border-box;overflow-y:auto;color:#eaf1fc;font:14px/1.55 Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
.pm-vconfirm-overlay *{box-sizing:border-box}
.pm-vconfirm-card{display:block;width:min(540px,100%);max-height:calc(100dvh - 32px);overflow-y:auto;background:#172538;border:1px solid #3c526e;box-shadow:0 25px 80px rgba(0,0,0,.55);border-radius:17px;padding:26px;position:relative}
.pm-vconfirm-head{display:flex;align-items:flex-start;gap:13px;margin:0 36px 18px 0}
.pm-vconfirm-icon{display:grid;place-items:center;flex:0 0 46px;width:46px;height:46px;border-radius:13px;background:#284878;color:#bbd4ff;font-size:24px}
.pm-vconfirm-kicker{margin:0 0 3px;font-size:10px;letter-spacing:.1em;text-transform:uppercase;font-weight:800;color:#92b2e8}
.pm-vconfirm-title{margin:0!important;color:#f0f5fd;font-size:22px!important;line-height:1.2;font-weight:750}
.pm-vconfirm-sub{margin:8px 0 17px;color:#b3c4da;font-size:13px}
.pm-vconfirm-close{position:absolute;right:18px;top:18px;width:35px;height:35px;display:grid;place-items:center;background:#253850;color:#c3d5eb;border:1px solid #405773;border-radius:9px;font-size:20px;cursor:pointer}
.pm-vconfirm-close:hover{background:#364d6b}
.pm-vconfirm-versions{display:grid;grid-template-columns:minmax(0,1fr) 34px minmax(0,1fr);align-items:center;gap:8px;background:#1d2f45;border:1px solid #3a536f;border-radius:12px;padding:17px 15px;margin:14px 0}
.pm-vconfirm-version-label{display:block;margin-bottom:7px;color:#9db3ce;font-size:11px;font-weight:700}
.pm-vconfirm-version-number{display:block;overflow-wrap:anywhere;color:#f2f6ff;font-weight:800;font-size:17px;line-height:1.3}
.pm-vconfirm-target .pm-vconfirm-version-number{color:#a7c9ff}
.pm-vconfirm-arrow{text-align:center;font-size:21px;color:#87a4ca}
.pm-vconfirm-info{display:flex;gap:10px;align-items:flex-start;background:#203147;border:1px solid #354b64;border-radius:10px;padding:11px 13px;margin:14px 0;color:#cad8ec;font-size:12px}
.pm-vconfirm-info-icon{font-size:17px;color:#8fb4ed;line-height:1.2}
.pm-vconfirm-info strong{color:#e9f2ff}
.pm-vconfirm-details{border:1px solid #344960;border-radius:9px;margin:15px 0;background:#1a2a3e}
.pm-vconfirm-details summary{cursor:pointer;padding:11px 12px;color:#afc5e1;font-size:12px;font-weight:750}
.pm-vconfirm-details dl{margin:0;padding:0 12px 10px}
.pm-vconfirm-details dl div{display:grid;grid-template-columns:115px minmax(0,1fr);gap:10px;padding:8px 0;border-top:1px solid #344960;font-size:11px;overflow-wrap:anywhere}
.pm-vconfirm-details dt{color:#9cb3cc}.pm-vconfirm-details dd{margin:0;color:#e3edf9}
.pm-vconfirm-consent{display:flex;gap:12px;align-items:flex-start;border:1px solid #456187;background:#223a55;border-radius:10px;padding:14px;margin:16px 0;color:#e7f2ff;font-size:13px;font-weight:650;cursor:pointer}
.pm-vconfirm-consent input{width:18px;height:18px;min-width:18px;accent-color:#8cb6fa;margin:2px 0 0;padding:0}
.pm-vconfirm-actions{display:flex;align-items:center;justify-content:flex-end;flex-wrap:wrap;gap:10px;margin-top:19px}
.pm-vconfirm-cancel{display:inline-flex;align-items:center;justify-content:center;min-height:42px;padding:10px 16px;background:#26384e;border:1px solid #49607a;border-radius:9px;text-decoration:none!important;color:#dce9f9!important;font-weight:750}
.pm-vconfirm-submit{min-height:42px;padding:10px 16px;border:1px solid #94b6fa;border-radius:9px;background:#82aafb;color:#10213d;font-weight:800;cursor:pointer}
.pm-vconfirm-submit:hover{background:#a3c3ff}
.pm-vconfirm-card :is(button,a,input,summary):focus-visible{outline:2px solid #a4c7ff;outline-offset:3px}
.pm-vconfirm-actions button[disabled]{opacity:.55;cursor:not-allowed}
@media(max-width:510px){.pm-vconfirm-card{padding:19px}.pm-vconfirm-title{font-size:19px!important}.pm-vconfirm-versions{padding:12px 10px}.pm-vconfirm-version-number{font-size:14px}.pm-vconfirm-actions{flex-direction:column-reverse;align-items:stretch}.pm-vconfirm-actions>*{width:100%;text-align:center}.pm-vconfirm-details dl div{grid-template-columns:85px minmax(0,1fr)}}
"""


def _confirmation_markup(operation: str, info: dict[str, Any],
                         preview_id: str, csrf: str, ttl: Any) -> str:
    """Escaped, accessible confirmation. No call to execute occurs here."""
    exact_target = str(info.get("target_version_ref") or "")
    current = str(info.get("current_version") or "Unknown")
    label = {
        "update-latest": "Install latest eligible release",
        "switch-version": "Switch to selected release",
        "pin-version": "Pin selected release",
        "rollback-version": "Restore previous release",
    }.get(operation, "Change release")
    try:
        seconds = max(0, int(ttl))
        ttl_text = f"{seconds // 60} min {seconds % 60} sec" if seconds >= 60 else f"{seconds} sec"
    except (ValueError, TypeError):
        ttl_text = str(ttl or "Unknown")
    return (
        '<div class="pm-vconfirm-overlay" data-vconfirm-overlay>'
        '<section class="pm-vconfirm-card" role="dialog" aria-modal="true"'
        ' aria-labelledby="pm-vconfirm-heading" aria-describedby="pm-vconfirm-description"'
        ' data-vconfirm-dialog>'
        '<button type="button" class="pm-vconfirm-close" data-vconfirm-dismiss'
        ' aria-label="Close update confirmation">×</button>'
        '<div class="pm-vconfirm-head"><div class="pm-vconfirm-icon" aria-hidden="true">↻</div>'
        '<div><p class="pm-vconfirm-kicker">Postmaster MCP · Versions &amp; updates</p>'
        '<h1 class="pm-vconfirm-title" id="pm-vconfirm-heading">Confirm update and restart</h1>'
        '</div></div>'
        '<p class="pm-vconfirm-sub" id="pm-vconfirm-description">'
        'Review the selected version. Nothing will change until you explicitly approve.'
        '</p>'
        '<div class="pm-vconfirm-versions">'
        '<div><span class="pm-vconfirm-version-label">Currently installed</span>'
        f'<strong class="pm-vconfirm-version-number">{_e(current)}</strong></div>'
        '<span class="pm-vconfirm-arrow" aria-hidden="true">→</span>'
        '<div class="pm-vconfirm-target"><span class="pm-vconfirm-version-label">Selected release</span>'
        f'<strong class="pm-vconfirm-version-number">{_e(exact_target)}</strong></div></div>'
        '<div class="pm-vconfirm-info"><span class="pm-vconfirm-info-icon" aria-hidden="true">ⓘ</span>'
        '<div><strong>A container restart is required.</strong> '
        'Postmaster will install the selected release, restart, and verify the running version '
        'before returning to the dashboard. Scheduled tasks and stored files are not removed.</div></div>'
        '<details class="pm-vconfirm-details"><summary>Technical details</summary><dl>'
        f'<div><dt>Operation</dt><dd>{_e(label)}</dd></div>'
        f'<div><dt>Current build</dt><dd>{_e(info.get("current_build"))}</dd></div>'
        f'<div><dt>Version selector</dt><dd>{_e(info.get("current_selector"))}</dd></div>'
        f'<div><dt>Confirmation expires</dt><dd>{_e(ttl_text)}</dd></div>'
        '</dl></details>'
        '<form method="post" action="/dashboard/versions/execute" data-vconfirm-execute>'
        f'<input type="hidden" name="csrf" value="{_e(csrf)}">'
        f'<input type="hidden" name="operation" value="{_e(operation)}">'
        f'<input type="hidden" name="target_version" value="{_e(exact_target)}">'
        f'<input type="hidden" name="preview_id" value="{_e(preview_id)}">'
        '<label class="pm-vconfirm-consent">'
        '<input type="checkbox" name="approved" value="yes" required data-vconfirm-consent>'
        f'<span>I approve installing <strong>{_e(exact_target)}</strong> '
        'and restarting Postmaster.</span></label>'
        '<div class="pm-vconfirm-actions">'
        '<a class="pm-vconfirm-cancel" data-vconfirm-dismiss '
        'href="/?ui_view=system&amp;settings_tab=versions#system">Cancel</a>'
        '<button class="pm-vconfirm-submit" type="submit" data-vconfirm-submit>'
        'Confirm update &amp; restart</button></div></form>'
        '</section></div>'
    )


def _confirmation_page(markup: str) -> HTMLResponse:
    """Complete readable fallback if Javascript or fragment fetching is unavailable."""
    return HTMLResponse(
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>Confirm update and restart · Postmaster MCP</title>'
        '<style>html{color-scheme:dark}body{margin:0;background:#101b2a}'
        + VERSION_CONFIRM_STYLE + '</style></head><body>'
        + markup + '</body></html>',
        headers={"Cache-Control":"private, no-store","X-Content-Type-Options":"nosniff",
                 "Referrer-Policy":"no-referrer",
                 "Content-Security-Policy":"default-src 'self'; base-uri 'none'; frame-ancestors 'self'"},
    )


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
                return previous_styles() + "\n" + VERSIONS_STYLE + "\n" + VERSION_CONFIRM_STYLE
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
        if operation == "pin-version":
            snapshot = _release_snapshot(base)
            if not (
                target in snapshot.get("release_tags", [])
                and channels.release_is_allowed(target, snapshot["release_channel"])
                and snapshot["check_status"] == "ok"
            ):
                return _response_page(
                    "Release unavailable",
                    "<p>The selected version is not a verified published release "
                    "for the current update channel. No changes were made.</p>"
                    '<p><a href="/?ui_view=system#system">Back to Settings</a></p>',
                    status=400,
                )
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
        markup = _confirmation_markup(
            operation, info, preview_id, base._csrf_value(),
            result.get("confirmation_expires_in_seconds"),
        )
        if req.headers.get("X-Postmaster-Preview-Modal") == "1":
            return HTMLResponse(
                markup,
                headers={"Cache-Control": "private, no-store",
                         "X-Content-Type-Options": "nosniff",
                         "Referrer-Policy": "no-referrer",
                         "Vary": "X-Postmaster-Preview-Modal"},
            )
        return _confirmation_page(markup)

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
        # Wait at least 15 seconds, then verify the actual running build.
        # A simple HTTP 200 is not sufficient: the old runtime might still
        # answer during restart. Never submit the update POST a second time.
        return HTMLResponse(
            '<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>Updating Postmaster MCP</title>'
            '<style>html{color-scheme:dark}*{box-sizing:border-box}'
            'body{margin:0;min-height:100dvh;display:grid;place-items:center;'
            'background:#101b2a;color:#edf3ff;font-family:Inter,system-ui,Arial,sans-serif}'
            'main{width:min(500px,calc(100vw - 32px));padding:38px 30px;'
            'border:1px solid #34465f;border-radius:16px;text-align:center;'
            'background:#172437;box-shadow:0 18px 44px #070f1d55}'
            '.mark{display:inline-grid;place-items:center;width:64px;height:64px;'
            'margin-bottom:15px;border-radius:17px;background:#274574;'
            'color:#a4c8ff;font-size:32px}h1{font-size:23px;margin:0 0 12px}'
            'p{line-height:1.6;color:#adbed5;font-size:14px}'
            'strong{color:#eff5ff}.seconds{font-weight:750;color:#8ab5ff}'
            '.bar{height:6px;background:#283c57;border-radius:6px;overflow:hidden;'
            'margin:23px 0}.bar>span{display:block;height:100%;width:100%;'
            'background:#6598ee;transform-origin:left;transition:transform 1s linear}'
            'a{color:#9ac2ff}a:focus-visible{outline:2px solid #9ac2ff;outline-offset:3px}'
            '</style></head><body>'
            '<main role="status" aria-live="polite">'
            '<div class="mark" aria-hidden="true">✉</div>'
            '<h1>Updating Postmaster MCP</h1>'
            '<p>Update scheduled for <strong id="target-version">' +
            _e(result.get("target_version_ref")) +
            '</strong>. The container is restarting.</p>'
            '<p id="update-status">Checking the installed build in '
            '<span id="countdown" class="seconds">15</span> seconds…</p>'
            '<div class="bar" aria-hidden="true"><span id="progress"></span></div>'
            '<p><a href="/?ui_view=system&amp;settings_tab=versions#system">'
            'Return to dashboard now</a></p>'
            '<p>The dashboard will open automatically only once the requested '
            'build is running. No update operation will be repeated.</p>'
            '<noscript><p>Automatic status checks require JavaScript. '
            'You may return manually using the link above.</p></noscript>'
            '</main>'
            '<script>(()=>{"use strict";'
            'const target=document.getElementById("target-version").textContent.trim();'
            'const status=document.getElementById("update-status");'
            'const counter=document.getElementById("countdown");'
            'const progress=document.getElementById("progress");'
            'const dashboard="/?ui_view=system&settings_tab=versions#system";'
            'const minimumMs=15000;const start=Date.now();'
            'let complete=false,checking=false,lastCheck=0;'
            'const normalize=s=>String(s||"").trim().replace(/^v/,"");'
            'async function check(){'
            'if(complete||checking)return;checking=true;'
            'const controller=new AbortController();'
            'const timeout=setTimeout(()=>controller.abort(),4000);'
            'try{'
            'const response=await fetch("/dashboard/versions/status?update_health="+Date.now(),{'
            'method:"GET",credentials:"same-origin",cache:"no-store",'
            'headers:{"Accept":"application/json"},signal:controller.signal});'
            'if(!response.ok)throw new Error("Health not available");'
            'const data=await response.json();'
            'const running=normalize(data.current_version);'
            'if(data.ok===true&&running===normalize(target)){'
            'complete=true;status.textContent="New version verified. Opening dashboard…";'
            'location.replace(dashboard);return;}'
            'status.textContent="Waiting for the requested version…"+'
            '(running?" Currently running: "+running+".":"");'
            '}catch(_e){status.textContent="Waiting for Postmaster to restart…";}'
            'finally{clearTimeout(timeout);checking=false;}'
            '}'
            'const tick=()=>{if(complete)return;'
            'const elapsed=Date.now()-start;'
            'const remaining=Math.max(0,Math.ceil((minimumMs-elapsed)/1000));'
            'if(remaining>0){counter.textContent=String(remaining);'
            'progress.style.transform="scaleX("+(remaining/15)+")";}'
            'else{progress.style.transform="scaleX(0)";'
            'if(!lastCheck||Date.now()-lastCheck>=3000){'
            'lastCheck=Date.now();check();}}'
            'setTimeout(tick,250);};tick();})();</script></body></html>',
            headers={"Cache-Control": "private, no-store",
                     "Referrer-Policy": "no-referrer",
                     "X-Content-Type-Options": "nosniff"},
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
