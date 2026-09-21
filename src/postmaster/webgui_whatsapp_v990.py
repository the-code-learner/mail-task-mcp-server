from __future__ import annotations

import base64
import html
from io import BytesIO
from typing import Any, Mapping
from urllib.parse import quote

from starlette.requests import Request
from starlette.responses import PlainTextResponse, RedirectResponse
from starlette.routing import Mount, Route


VIEW = "whatsapp"
LABEL = "WhatsApp"


def _local_qr_data_uri(payload: str) -> str | None:
    """Render QR locally when qrcode is available; never calls an external service."""
    try:
        import qrcode
        import qrcode.image.svg
        qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=4, box_size=8)
        qr.add_data(str(payload)); qr.make(fit=True)
        image = qr.make_image(image_factory=qrcode.image.svg.SvgPathImage)
        buf = BytesIO(); image.save(buf)
        return "data:image/svg+xml;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:
        return None


def qr_renderer_status() -> dict[str, object]:
    try:
        import qrcode  # noqa: F401
        available = True
    except Exception:
        available = False
    return {"local": True, "remote_service": False, "available": available}


def render_whatsapp_panel(status: Mapping[str, Any], *, qr_payload: str | None = None) -> str:
    network = status.get("network") if isinstance(status.get("network"), Mapping) else {}
    connected = bool(network.get("connected"))
    paired = bool(status.get("paired"))
    state = "Connected" if connected else ("Paired / disconnected" if paired else "Not paired")
    qr_uri = _local_qr_data_uri(str(qr_payload)) if qr_payload else None
    if qr_payload and qr_uri:
        qr_block = (
            '<div class="wa-qr"><img class="wa-qr-image" '
            f'src="{html.escape(qr_uri, quote=True)}" alt="WhatsApp pairing QR">'
            '<p>Scan from WhatsApp → Linked devices. QR rendering is local and never uses a third-party endpoint.</p></div>'
        )
    elif qr_payload:
        qr_block = (
            '<div class="notice warn"><strong>Pairing QR renderer unavailable.</strong> '
            'The transient pairing payload is retained encrypted server-side and is not exposed as plain text in this page.</div>'
        )
    else:
        qr_block = '<p class="muted">No active pairing QR. Start pairing explicitly to generate one.</p>'
    return (
        '<section id="whatsapp-panel" data-postmaster-whatsapp-v990="1">'
        '<div class="v951-pagehead"><div><h2>WhatsApp</h2>'
        '<p>Clean-room companion session supervision.</p></div><span class="badge">v9.9.0</span></div>'
        f'<p><strong>Status:</strong> {html.escape(state)}</p>'
        '<p>Reading messages here never sends read receipts. A read receipt is permitted only inside an explicit outbound reply flow.</p>'
        f'{qr_block}'
        '<div class="wa-actions">'
        '<form method="post" action="/dashboard/whatsapp/pair"><button type="submit">Pair device</button></form>'
        '<form method="post" action="/dashboard/whatsapp/reconnect"><button type="submit">Reconnect</button></form>'
        '</div></section>'
    )


def _render_view(base: Any) -> str:
    service = base.whatsapp_service_v990()
    status = service.status()
    qr = service.pairing_qr()
    return '<section class="tab-panel" id="panel-whatsapp" data-panel="whatsapp">' + render_whatsapp_panel(status, qr_payload=qr) + '</section>'


def _redirect(message: str = "") -> RedirectResponse:
    suffix = ("?ui_view=whatsapp&flash=" + quote(message[:240])) if message else "?ui_view=whatsapp"
    return RedirectResponse("/" + suffix + "#whatsapp", status_code=303)


async def _verified(base: Any, request: Request):
    form, error = await base._verified_form(request)
    if error:
        return form, error
    return form, None


async def start_pairing(base: Any, request: Request):
    _form, error = await _verified(base, request)
    if error: return error
    try:
        result = await base.whatsapp_service_v990().start_pairing()
        message = "Pairing QR generated" if result.get("qr") else "Pairing started"
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
    return _redirect(message)


async def reconnect(base: Any, request: Request):
    _form, error = await _verified(base, request)
    if error: return error
    try:
        result = await base.whatsapp_service_v990().reconnect()
        message = "WhatsApp reconnected" if result.get("connected") or result.get("ok") else "Reconnect requested"
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
    return _redirect(message)


def _insert_route(app: Any, route: Route) -> None:
    for index, current in enumerate(app.router.routes):
        if isinstance(current, Mount):
            app.router.routes.insert(index, route); return
    app.router.routes.append(route)


def _extend_nav(nav_module: Any) -> None:
    groups = []
    found = False
    for heading, links in nav_module.NAV_GROUPS:
        mutable = list(links)
        if any(item[0] == VIEW for item in mutable): found = True
        if heading == "Communicate" and not found:
            mutable.append((VIEW, LABEL, "WA")); found = True
        groups.append((heading, tuple(mutable)))
    if not found and groups:
        heading, links = groups[-1]; groups[-1] = (heading, tuple(links) + ((VIEW, LABEL, "WA"),))
    nav_module.NAV_GROUPS = tuple(groups)


def install_webgui_whatsapp_v990(app: Any, base: Any, shell: Any, views: Any, nav_module: Any) -> None:
    """Install the v9.9 WhatsApp supervision/pairing view into the existing lazy WebGUI shell."""
    old_render = shell.render_view
    def render_with_whatsapp(bound_base: Any, core: Any, request: Request, view: str) -> str:
        if view == VIEW:
            return _render_view(bound_base)
        return old_render(bound_base, core, request, view)
    if VIEW not in shell.VIEWS: shell.VIEWS = tuple(shell.VIEWS) + (VIEW,)
    if VIEW not in views.VIEWS: views.VIEWS = tuple(views.VIEWS) + (VIEW,)
    shell.render_view = render_with_whatsapp
    views.render_view = render_with_whatsapp
    _extend_nav(nav_module)

    handlers = (
        ("/dashboard/whatsapp/pair", start_pairing, "v990_whatsapp_pair"),
        ("/dashboard/whatsapp/reconnect", reconnect, "v990_whatsapp_reconnect"),
    )
    existing = {getattr(route, "path", None) for route in app.router.routes}
    for path, handler, name in handlers:
        if path in existing: continue
        async def endpoint(request: Request, _handler=handler):
            if request.method != "POST":
                return PlainTextResponse("Method Not Allowed", status_code=405, headers={"Allow": "POST"})
            return await _handler(base, request)
        _insert_route(app, Route(path, endpoint, methods=["POST"], name=name))


__all__ = [
    "VIEW", "LABEL", "qr_renderer_status", "render_whatsapp_panel", "start_pairing", "reconnect", "install_webgui_whatsapp_v990",
]
