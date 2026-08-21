from __future__ import annotations

from typing import Any

from starlette.requests import Request
from starlette.responses import HTMLResponse
from starlette.routing import Mount, Route

from .webgui_helpers import (
    decorate_navigation, decorate_styles, decorate_version, project_rows,
    replace_panel, runtime_version,
)
from .webgui_knowledge import knowledge_fragment
from .webgui_projects import files_fragment, project_overview_fragment
from .webgui_tasks import dashboard_job_update, task_fragment


def install_webgui_v945(app: Any, base: Any, legacy_dashboard: Any) -> Any:
    async def dashboard_home(request: Request):
        response = await legacy_dashboard(request)
        if "text/html" not in str(response.headers.get("content-type", "")).lower():
            return response
        try:
            body = response.body.decode("utf-8")
            projects = project_rows(base)
            body = decorate_navigation(
                body,
                len(projects),
                project_overview_fragment(base, request),
            )
            body = replace_panel(body, "knowledge", knowledge_fragment(base, request))
            body = replace_panel(body, "files", files_fragment(base, request))
            body = replace_panel(body, "scheduler", task_fragment(base, request))
            body = decorate_version(body, runtime_version(base))
            body = decorate_styles(body)
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
            base.logger.info("Could not augment v9.4.5 WebGUI", exc_info=True)
            return response

    async def job_update(request: Request):
        return await dashboard_job_update(base, request)

    routes = app.router.routes
    for index, route in enumerate(list(routes)):
        if isinstance(route, Route) and route.path == "/":
            routes[index] = Route("/", dashboard_home, methods=["GET"])
            break
    mount_index = next(
        (i for i, route in enumerate(routes) if isinstance(route, Mount)),
        len(routes),
    )
    if not any(
        isinstance(route, Route) and route.path == "/dashboard/job/update"
        for route in routes
    ):
        routes.insert(
            mount_index,
            Route("/dashboard/job/update", job_update, methods=["POST"]),
        )
    return dashboard_home
