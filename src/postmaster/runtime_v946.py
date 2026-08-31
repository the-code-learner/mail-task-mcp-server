from __future__ import annotations

from html import escape
from typing import Any

from starlette.requests import Request
from starlette.responses import HTMLResponse
from starlette.routing import Route

from .stored_file_delivery import PostmasterV946MailClient, stored_file_link_store
from .stored_file_public_v972 import (
    install_stored_file_public_v972,
    public_tracking_target_v972,
)
from .update_status import latest_version_status


def _footer_html(status: dict[str, Any]) -> str:
    version = str(status.get("version") or "unknown").removeprefix("v")
    latest = str(status.get("latest_version") or "").removeprefix("v")
    check_status = str(status.get("update_check_status") or "never")
    available = status.get("update_available")
    if check_status == "ok" and available is True and latest:
        state = f"Update available: v{latest}"
    elif check_status == "ok" and available is False:
        state = "Up to date"
    elif check_status == "error" and available is True and latest:
        state = f"Update available (last known): v{latest}"
    elif check_status == "error" and latest:
        state = f"Update check unavailable · last known v{latest}"
    elif check_status == "error":
        state = "Update check unavailable"
    else:
        state = "Update status unavailable"
    return (
        '<footer class="postmaster-version-footer" '
        'style="margin-top:28px;padding-top:14px;border-top:1px solid var(--line);'
        'color:var(--muted);font-size:12px">'
        f"Postmaster v{escape(version)} · {escape(state)}"
        "</footer>"
    )


def decorate_update_footer(body: str, status: dict[str, Any]) -> str:
    footer = _footer_html(status)
    if "postmaster-version-footer" in body:
        return body
    if "</main>" in body:
        return body.replace("</main>", footer + "</main>", 1)
    if "</body>" in body:
        return body.replace("</body>", footer + "</body>", 1)
    return body + footer


def install_runtime_v946(app: Any, base: Any, core: Any, legacy_dashboard: Any):
    """Compose v9.4.6 behavior plus the v9.7.2 Stored File public-handoff correction."""

    install_stored_file_public_v972(base, core)

    def authorize_stored_file(info: dict[str, Any]) -> bool:
        # Reuse the same owner/project registry validation used by FileStore writes.
        base._require_knowledge_scope(
            str(info.get("owner_id") or ""),
            str(info.get("project_id")) if info.get("project_id") else None,
        )
        return True

    def mail_client(account_id: str | None = None) -> PostmasterV946MailClient:
        return PostmasterV946MailClient(
            base.account_store().settings(account_id),
            file_store=base.file_store(),
            file_authorizer=authorize_stored_file,
            tracking_store=stored_file_link_store(),
        )

    # Existing server and runtime tools resolve these module globals at call time.
    core.mail_client = mail_client
    base.mail_client = mail_client
    core.link_store = stored_file_link_store

    legacy_build_status = core.build_status

    def build_status():
        """Read-only build identity plus lazy latest stable release information."""
        status = legacy_build_status()
        if isinstance(status, dict):
            version = str(status.get("version") or "unknown")
            status.update(latest_version_status(version))
            status["stored_file_attachments"] = True
            status["stored_file_public_downloads"] = True
            status["stored_file_public_download_path"] = "/t/c/*"
            status["stored_file_id_exposed_in_public_url"] = False
            status["live_update_check"] = True
        return status

    core.mcp.remove_tool("build_status")
    core.mcp.add_tool(build_status, name="build_status")
    core.build_status = build_status
    base.build_status = build_status

    async def dashboard_home(request: Request):
        response = await legacy_dashboard(request)
        if "text/html" not in str(response.headers.get("content-type", "")).lower():
            return response
        try:
            body = response.body.decode("utf-8")
            body = decorate_update_footer(body, build_status())
            return HTMLResponse(
                body,
                status_code=response.status_code,
                headers={
                    key: value
                    for key, value in response.headers.items()
                    if key.lower() != "content-length"
                },
            )
        except Exception:
            base.logger.info("Could not augment v9.4.6 update footer", exc_info=True)
            return response

    async def tracking_target(request: Request):
        return await public_tracking_target_v972(
            request,
            tracking_store=stored_file_link_store(),
            file_store=base.file_store(),
            logger=base.logger,
        )

    routes = app.router.routes
    for index, route in enumerate(list(routes)):
        if isinstance(route, Route) and route.path == "/":
            routes[index] = Route("/", dashboard_home, methods=["GET"])
        elif isinstance(route, Route) and route.path == "/t/c/{token}":
            routes[index] = Route("/t/c/{token}", tracking_target, methods=["GET"])

    return dashboard_home, build_status, mail_client
