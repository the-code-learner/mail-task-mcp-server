"""Mount the one-task-type autonomous email worker on the real app lifespan."""
from __future__ import annotations

import contextlib
from typing import Any

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
