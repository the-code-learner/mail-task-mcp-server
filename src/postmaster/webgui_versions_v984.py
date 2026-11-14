"""v9.8.4 release status and guarded version changes in authenticated WebGUI.

Uses the v9.6.9 server-side pending preview/execute backend. Never performs a
version change without CSRF-verified preview and separate explicit confirmation.
"""
from __future__ import annotations

from html import escape
from typing import Any
from starlette.requests import Request
from starlette.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from starlette.routing import Mount, Route

from . import runtime_control
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


def versions_settings(base: Any) -> str:
    channel = runtime_control.preferred_release_channel()
    versions, lookup = runtime_control.available_release_tags(channel=channel)
    build = base.build_status()
    running = str(build.get("version") or "unknown") if isinstance(build, dict) else "unknown"
    selector = runtime_control.read_control().get("selector") or str(
        build.get("requested_version") or "latest"
    ) if isinstance(build, dict) else "unknown"
    options = "".join(
        f'<option value="{_e(v)}">{_e(v)}'
        f'{" · BETA" if "-beta." in v else " · Stable"}</option>'
        for v in versions
    )
    chosen_stable=' selected' if channel=="stable" else ""
    chosen_beta=' selected' if channel=="stable_beta" else ""
    pref_form=(
        '<form method="post" action="/dashboard/versions/channel">'
        f'<input type="hidden" name="csrf" value="{_e(base._csrf_value())}">'
        '<label>Update channel <select name="release_channel">'
        f'<option value="stable"{chosen_stable}>Stable only</option>'
        f'<option value="stable_beta"{chosen_beta}>Stable + Beta</option>'
        '</select></label>'
        '<button type="submit">Save update preference</button>'
        '</form>'
        '<p class="small muted">Stable only is the default. Beta releases are opt-in '
        'prereleases and may contain unfinished functionality. Choosing a channel '
        'changes which updates are shown, not the installed build. '
        'A version change always requires a separate preview and confirmation.</p>'
    )
    if not versions:
        selector_control = '<div class="notice">Release discovery unavailable; version selection is disabled.</div>'
    else:
        selector_control = (
            '<form method="post" action="/dashboard/versions/preview">'
            f'<input type="hidden" name="csrf" value="{_e(base._csrf_value())}">'
            '<label>Action <select name="operation">'
            '<option value="update-latest">Update to latest allowed release</option>'
            '<option value="switch-version">Switch to selected version</option>'
            '<option value="pin-version">Pin selected version</option>'
            '<option value="rollback-version">Roll back to selected version</option>'
            '</select></label>'
            '<label>Target eligible release <select name="target_version">'
            f'{options}</select></label>'
            '<button class="primary" type="submit">Preview change</button>'
            '</form>'
        )
    return (
        '<section class="card wide" id="v984-versions">'
        '<h3>Versions & updates</h3>'
        f'<p>Installed: <strong>{_e(running)}</strong> · Selected runtime: <strong>{_e(selector)}</strong></p>'
        f'<p class="small muted">Release check: {_e(lookup)}. '
        'Draft releases are always excluded; Beta is shown only when selected.</p>'
        f'{pref_form}'
        f'{selector_control}'
        '<p class="small muted">Preview and explicit confirmation are mandatory. '
        'A confirmed change is handled by the existing version selector and process restart mechanism; '
        'no source release or Portainer modification is triggered from this page.</p>'
        '</section>'
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

    async def preview(req: Request) -> Response:
        form, error = await base._verified_form(req)
        if error is not None:
            return error
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
                "Version change preview failed",
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
                "Preview unavailable",
                "<p>The runtime did not provide a complete server-side pending preview. "
                "No version change has been scheduled.</p>"
                '<p><a href="/?ui_view=system#system">Back to Settings</a></p>',
                status=400,
            )
        panel = (
            '<p>The following action changes the selected runtime after a restart.</p>'
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
            'I explicitly approve this exact version change and restart.</label>'
            '<p><button type="submit">Confirm version change</button>'
            ' <a href="/?ui_view=system#system">Cancel</a></p></form>'
        )
        return _response_page("Confirm version change", panel)

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
