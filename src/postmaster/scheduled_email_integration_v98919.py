"""Mount the one-task-type autonomous email worker on the real app lifespan."""
from __future__ import annotations

import contextlib
from typing import Any
from html import escape

from starlette.routing import Route, Mount
from starlette.responses import HTMLResponse, RedirectResponse

from .scheduled_email_v98919 import ScheduledEmailService


def install_scheduled_email_runtime_v98919(app: Any, base: Any) -> ScheduledEmailService:
    existing = getattr(base, "scheduled_email_service_v98919", None)
    if existing is not None:
        return existing
    scheduler = base.scheduler()
    service = ScheduledEmailService(
        scheduler, lambda account_id: base.mail_client(account_id),
        poll_seconds=min(30, max(2, scheduler.settings.poll_seconds)),
    )
    base.scheduled_email_service_v98919 = service
    scheduler.autonomous_scheduled_email_service_v98919 = service
    async def cancel_route(request: Any):
        form, error = await base._verified_form(request)
        if error is not None:
            return error
        try:
            service.cancel(str(form.get("job_id") or "").strip())
        except Exception as exc:
            return HTMLResponse(
                '<h1>Unable to cancel scheduled email</h1><p>' +
                escape(str(exc)) + '</p><p><a href="/?ui_view=scheduler#scheduler">'
                'Return to Tasks</a></p>',
                status_code=400,
                headers={"Cache-Control": "no-store"},
            )
        return RedirectResponse("/?ui_view=scheduler#scheduler", status_code=303)

    if not any(isinstance(route, Route) and
               route.path == "/dashboard/scheduled-email/cancel"
               for route in app.router.routes):
        index = next((i for i, route in enumerate(app.router.routes)
                      if isinstance(route, Mount)), len(app.router.routes))
        app.router.routes.insert(
            index,
            Route("/dashboard/scheduled-email/cancel", cancel_route,
                  methods=["POST"], name="scheduled_email_cancel_v98919"),
        )
    previous = app.router.lifespan_context

    @contextlib.asynccontextmanager
    async def lifespan(instance: Any):
        async with previous(instance):
            service.start()
            try:
                yield
            finally:
                service.stop()

    app.router.lifespan_context = lifespan
    return service
