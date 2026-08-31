from __future__ import annotations

from typing import Any

from starlette.responses import HTMLResponse


_INSTALLED_FLAG = "_compose_confirmation_v971_installed"


def install_compose_confirmation_v971(webgui_v964: Any) -> None:
    """Preserve every selected Stored File across the suppression-confirmation POST."""
    if getattr(webgui_v964, _INSTALLED_FLAG, False):
        return
    original = webgui_v964._confirmation_page

    def confirmation_v971(base: Any, form: Any, blocked: list[dict[str, Any]]) -> HTMLResponse:
        response = original(base, form, blocked)
        try:
            values = [str(value).strip() for value in form.getlist("attachment_file_ids") if str(value).strip()]
        except Exception:
            values = []
        if len(values) <= 1:
            return response
        extras = "".join(webgui_v964._hidden("attachment_file_ids", value) for value in values)
        body = response.body.decode("utf-8")
        marker = '<form method="post" action="/dashboard/compose/send">'
        body = body.replace(marker, marker + extras, 1)
        # Compose is no longer top-level; cancellation returns to the Inbox composer surface.
        body = body.replace('/?ui_view=compose#compose', '/?ui_view=inbox#inbox')
        return HTMLResponse(body, status_code=response.status_code, headers={"Cache-Control": "no-store"})

    webgui_v964._confirmation_page = confirmation_v971
    setattr(webgui_v964, _INSTALLED_FLAG, True)


__all__ = ["install_compose_confirmation_v971"]
