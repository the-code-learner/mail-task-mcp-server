from __future__ import annotations

import os
from html import escape

import uvicorn
from mcp.types import CallToolResult
from starlette.requests import Request
from starlette.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from starlette.routing import Mount, Route

from . import server as _base
from .file_handoff import (
    read_stored_file_resource,
    stored_file_http_response,
    stored_file_resource_result,
)
from .link_tracking import link_store
from .link_tracking_html import eligible_web_url
from .tracked_mail import LinkTrackingMailClient

mcp = _base.mcp
_legacy_build_status = _base.build_status
_legacy_tracking_status = _base.tracking_status
_legacy_get_tracking_campaign = _base.get_tracking_campaign
_legacy_dashboard_home = _base.dashboard_home


def mail_client(account_id: str | None = None) -> LinkTrackingMailClient:
    return LinkTrackingMailClient(_base.account_store().settings(account_id))

_base.mail_client = mail_client


def build_status():
    status = _legacy_build_status()
    status["native_file_resource_handoff"] = True
    status["link_tracking"] = True
    status["sent_copy_tracking_sanitized"] = True
    return status

mcp.remove_tool("build_status")
mcp.add_tool(build_status, name="build_status")
_base.build_status = build_status


@mcp.tool()
def get_stored_file_resource(file_id: str, transport: str = "auto") -> CallToolResult:
    return stored_file_resource_result(_base.file_store(), file_id, transport)


@mcp.resource(
    "postmaster://files/{file_id}",
    name="postmaster_stored_file",
    description="Original bytes for a Postmaster FileStore file identified by canonical file_id.",
)
def stored_file_resource(file_id: str) -> bytes:
    return read_stored_file_resource(_base.file_store(), file_id)


def tracking_status():
    base = _legacy_tracking_status()
    if isinstance(base, dict) and base.get("ok"):
        base["link_tracking"] = link_store().status()
        base["event_types"] = ["pixel", "amp_xhr", "link"]
    return base

mcp.remove_tool("tracking_status")
mcp.add_tool(tracking_status, name="tracking_status")
_base.tracking_status = tracking_status


def get_tracking_campaign(campaign_id: str):
    base = _legacy_get_tracking_campaign(campaign_id)
    if isinstance(base, dict) and base.get("ok") is False:
        return base
    try:
        base["link_tracking"] = link_store().summary(campaign_id=campaign_id)
        base["top_links"] = link_store().top_links(campaign_id=campaign_id, limit=25)
    except Exception as exc:
        base["link_tracking_error"] = f"{type(exc).__name__}: {exc}"
    return base

mcp.remove_tool("get_tracking_campaign")
mcp.add_tool(get_tracking_campaign, name="get_tracking_campaign")
_base.get_tracking_campaign = get_tracking_campaign


@mcp.tool()
def get_tracking_summary(
    campaign_id: str | None = None,
    delivery_id: str | None = None,
    link_id: str | None = None,
    account_id: str | None = None,
):
    """Read-only click summary. Unique click = delivery_id + link_id + client_fingerprint."""
    return _base._safe_call(
        link_store().summary,
        campaign_id=campaign_id, delivery_id=delivery_id, link_id=link_id, account_id=account_id,
    )


@mcp.tool()
def list_tracking_links(
    campaign_id: str | None = None,
    delivery_id: str | None = None,
    link_id: str | None = None,
    account_id: str | None = None,
    clicked_only: bool = False,
    limit: int = 500,
):
    """Read-only tracked link occurrences and aggregates; opaque tokens are never returned."""
    return _base._safe_call(
        link_store().list_links,
        campaign_id=campaign_id, delivery_id=delivery_id, link_id=link_id,
        account_id=account_id, clicked_only=clicked_only, limit=limit,
    )


@mcp.tool()
def list_tracking_events(
    delivery_id: str | None = None,
    campaign_id: str | None = None,
    link_id: str | None = None,
    recipient: str | None = None,
    account_id: str | None = None,
    event_type: str | None = None,
    limit: int = 500,
):
    """Read-only unified tracking events. event_type may be all, pixel, amp_xhr or link."""
    return _base._safe_call(
        link_store().unified_events,
        delivery_id=delivery_id, campaign_id=campaign_id, link_id=link_id,
        recipient=recipient, account_id=account_id, event_type=event_type, limit=limit,
    )


async def public_stored_file_download(request: Request):
    return stored_file_http_response(request, _base.file_store(), require_signature=True)


async def dashboard_file_download(request: Request):
    return stored_file_http_response(request, _base.file_store(), require_signature=False)


async def tracking_click(request: Request):
    token = str(request.path_params.get("token", ""))
    try:
        link = link_store().get_by_token(token)
        destination = str(link.get("original_url") or "")
        if not eligible_web_url(destination):
            raise ValueError("Stored link destination is not an HTTP/HTTPS URL")
    except Exception:
        return PlainTextResponse("Not found", status_code=404, headers={"Cache-Control": "no-store"})

    forwarded = request.headers.get("x-forwarded-for", "")
    client_ip = forwarded.split(",", 1)[0].strip() if forwarded else ""
    if not client_ip and request.client:
        client_ip = request.client.host or ""
    try:
        link_store().record_click(
            link,
            user_agent=request.headers.get("user-agent", ""),
            client_ip=client_ip,
            country_code=request.headers.get("cf-ipcountry", ""),
        )
    except Exception:
        _base.logger.info("Link click could not be recorded", exc_info=True)

    response = RedirectResponse(destination, status_code=302)
    response.headers["Cache-Control"] = "private, no-store, no-cache, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return response


def _tracking_dashboard_fragment(account_id: str | None = None) -> str:
    top = link_store().top_links(account_id=account_id, limit=20)
    events = link_store().unified_events(account_id=account_id, limit=100)
    top_rows = []
    for row in top:
        label = str(row.get("anchor_text") or row.get("destination_host") or row.get("original_url") or "")
        top_rows.append(
            "<tr>"
            f"<td><code>{escape(str(row.get('link_id','')))}</code></td>"
            f"<td>{escape(label)}</td>"
            f"<td>{escape(str(row.get('destination_host','')))}</td>"
            f"<td>{int(row.get('total_clicks') or 0)}</td>"
            f"<td>{int(row.get('unique_clicks') or 0)}</td>"
            f"<td>{int(row.get('unique_recipients') or 0)}</td>"
            f"<td>{escape(str(row.get('first_click') or ''))}</td>"
            f"<td>{escape(str(row.get('last_click') or ''))}</td></tr>"
        )
    if not top_rows:
        top_rows.append('<tr><td colspan="8" class="muted">No link clicks recorded yet.</td></tr>')

    event_rows = []
    for row in events:
        source = " / ".join(x for x in (str(row.get("country_code") or ""), str(row.get("client_source") or "")) if x)
        browser_os = " / ".join(x for x in (str(row.get("browser") or ""), str(row.get("os") or "")) if x)
        label = str(row.get("anchor_text") or row.get("destination_host") or "")
        ua = str(row.get("user_agent") or "")[:180]
        event_rows.append(
            "<tr>"
            f"<td><strong>{escape(str(row.get('event_type') or ''))}</strong></td>"
            f"<td>{escape(str(row.get('recipient') or ''))}</td>"
            f"<td>{escape(str(row.get('observed_at') or ''))}</td>"
            f"<td>{escape(source)}</td><td>{escape(browser_os)}</td>"
            f"<td>{escape(str(row.get('campaign_id') or ''))}<br><span class=\"muted\">{escape(str(row.get('delivery_id') or ''))}</span></td>"
            f"<td>{escape(str(row.get('client_fingerprint') or ''))}</td>"
            f"<td>{escape(label)}</td><td>{escape(str(row.get('link_id') or ''))}</td>"
            f"<td>{escape(str(row.get('destination_host') or ''))}</td>"
            f"<td>{escape(str(row.get('position') if row.get('position') is not None else ''))}</td>"
            f"<td title=\"{escape(str(row.get('user_agent') or ''), quote=True)}\">{escape(ua)}</td></tr>"
        )
    if not event_rows:
        event_rows.append('<tr><td colspan="12" class="muted">No tracking events recorded yet.</td></tr>')

    return f"""
<section class="card wide">
<div class="panel-title"><h2>Top links</h2><span class="small muted">v9.4 click analytics</span></div>
<p class="small">Unique click = <code>delivery_id + link_id + client_fingerprint</code>. Fetches are telemetry; v9.4 does not classify human vs scanner.</p>
<div class="scroll"><table><thead><tr><th>Link ID</th><th>Label</th><th>Destination host</th><th>Total</th><th>Unique</th><th>Recipients</th><th>First click</th><th>Last click</th></tr></thead><tbody>{''.join(top_rows)}</tbody></table></div>
</section>
<section class="card wide">
<div class="panel-title"><h2>Tracking events</h2><span class="small muted">pixel / AMP / link</span></div>
<div class="scroll"><table><thead><tr><th>Type</th><th>Recipient</th><th>Observed UTC</th><th>Country / source</th><th>Browser / OS</th><th>Campaign / delivery</th><th>Client fingerprint</th><th>Link label</th><th>Link ID</th><th>Destination</th><th>Position</th><th>User-Agent</th></tr></thead><tbody>{''.join(event_rows)}</tbody></table></div>
</section>
"""


async def dashboard_home(request: Request):
    response = await _legacy_dashboard_home(request)
    if "text/html" not in str(response.headers.get("content-type", "")).lower():
        return response
    try:
        body = response.body.decode("utf-8")
        fragment = _tracking_dashboard_fragment(request.query_params.get("account") or None)
        marker = '<section class="card">\n<h2>Accuracy / privacy model</h2>'
        if marker in body:
            body = body.replace(marker, fragment + marker, 1)
        elif "</main>" in body:
            body = body.replace("</main>", fragment + "</main>", 1)
        else:
            body += fragment
        return HTMLResponse(body, status_code=response.status_code)
    except Exception:
        _base.logger.info("Could not augment tracking dashboard", exc_info=True)
        return response


_routes = _base.app.router.routes
for index, route in enumerate(list(_routes)):
    if isinstance(route, Route) and route.path == "/dashboard/files/{file_id}/download":
        _routes[index] = Route("/dashboard/files/{file_id}/download", dashboard_file_download, methods=["GET", "HEAD"])
    elif isinstance(route, Route) and route.path == "/":
        _routes[index] = Route("/", dashboard_home, methods=["GET"])

mount_index = next((i for i, route in enumerate(_routes) if isinstance(route, Mount)), len(_routes))
if not any(isinstance(route, Route) and route.path == "/t/c/{token}" for route in _routes):
    _routes.insert(mount_index, Route("/t/c/{token}", tracking_click, methods=["GET"]))
    mount_index += 1
if not any(isinstance(route, Route) and route.path == "/files/{file_id}" for route in _routes):
    _routes.insert(mount_index, Route("/files/{file_id}", public_stored_file_download, methods=["GET", "HEAD"]))

app = _base.app

for _name in dir(_base):
    if _name.startswith("_") or _name in globals():
        continue
    globals()[_name] = getattr(_base, _name)

if __name__ == "__main__":
    uvicorn.run(app, host=os.getenv("MCP_HOST", "0.0.0.0"), port=int(os.getenv("MCP_PORT", "8000")), log_level="info")
