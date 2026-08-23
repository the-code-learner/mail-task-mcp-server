from __future__ import annotations

from html import escape
from typing import Any

from . import webgui_v951 as v951


_INSTALLED = False


def _wrap_system_card(html: str, heading: str, key: str) -> str:
    marker = f"<h3>{heading}</h3>"
    pos = html.find(marker)
    if pos < 0:
        return html
    start = html.rfind('<section class="card', 0, pos)
    end = html.find("</section>", pos)
    if start < 0 or end < 0:
        return html
    end += len("</section>")
    card = html[start:end]
    details = (
        f'<details class="v962-collapsible" data-v962-state-key="{escape(key, quote=True)}">'
        f'<summary>{escape(heading)}</summary>'
        f'<div class="v962-collapsible-body">{card}</div></details>'
    )
    return html[:start] + details + html[end:]


def install_webgui_v962_collapsible_system() -> None:
    """Collapse occasional runtime actions while leaving System status continuously visible."""
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True
    original = v951.render_system

    def render_system_v962(base: Any, request: Any) -> str:
        html = original(base, request)
        for heading, key in (
            ("Restart current version", "system-restart-current"),
            ("Update to latest stable", "system-update-latest"),
            ("Select stable version", "system-select-version"),
        ):
            html = _wrap_system_card(html, heading, key)
        return html

    v951.render_system = render_system_v962
