from __future__ import annotations

from html import escape
from typing import Any, Callable


STYLE = r'''
/* post-v9.7.0 mail safety / reputation IA */
.v971-mail-safety{margin:0 0 14px}.v971-mail-safety-head{display:flex;justify-content:space-between;gap:12px;align-items:flex-start;flex-wrap:wrap}.v971-mail-safety-head h2{margin:0}.v971-mail-safety-model{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px;margin:12px 0}.v971-mail-safety-model>div{border:1px solid var(--line);border-radius:9px;padding:9px;background:var(--card2)}.v971-mail-safety-model strong,.v971-mail-safety-model span{display:block}.v971-mail-safety-model span{margin-top:3px;color:var(--muted);font-size:11px}.v971-mail-safety-tabs{display:flex;gap:6px;flex-wrap:wrap}.v971-mail-safety-tabs a{border:1px solid var(--line);border-radius:999px;padding:6px 9px;text-decoration:none;color:var(--muted)}.v971-mail-safety-tabs a.active{border-color:var(--accent);background:color-mix(in srgb,var(--accent) 12%,var(--card));color:var(--text)}
@media(max-width:900px){.v971-mail-safety-model{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:560px){.v971-mail-safety-model{grid-template-columns:1fr}}
'''

SAFETY_VIEWS = ("mail-health", "suppressions", "domains", "recipients", "security")
_TAB_LABELS = (
    ("mail-health", "Reputation & Deliverability"),
    ("suppressions", "Suppressions"),
    ("domains", "Domain policy"),
    ("recipients", "Recipient policy"),
    ("security", "Security"),
)
_INSTALLED_FLAG = "_mail_safety_ia_v971_installed"
_SCRIPT_MARKER = "v971-mail-safety-primary"


def _consolidated_nav(nav: tuple[tuple[str, str], ...] | list[tuple[str, str]]) -> tuple[tuple[str, str], ...]:
    rows: list[tuple[str, str]] = []
    for view, label in nav:
        if view == "mail-health":
            rows.append((view, "Mail Safety"))
        elif view in set(SAFETY_VIEWS) - {"mail-health"}:
            continue
        else:
            rows.append((view, label))
    return tuple(rows)


def _safety_header(active: str) -> str:
    tabs = []
    for view, label in _TAB_LABELS:
        state = " active" if view == active else ""
        href = f"/?ui_view={view}#{view}"
        tabs.append(
            f'<a class="{state.strip()}" data-v960-fragment="{escape(view, quote=True)}" '
            f'href="{escape(href, quote=True)}">{escape(label)}</a>'
        )
    return (
        '<section class="card wide v971-mail-safety" data-v971-mail-safety="1">'
        '<div class="v971-mail-safety-head"><div><h2>Mail Safety &amp; Reputation</h2>'
        '<p class="small muted">One control area for sending safety, deliverability, authorization policy and security. Existing capabilities remain available in their compatibility views.</p></div>'
        '<span class="badge">Policy + health</span></div>'
        '<div class="v971-mail-safety-model">'
        '<div><strong>Mail safety</strong><span>Suppressions and recipient protection controls.</span></div>'
        '<div><strong>Reputation / deliverability</strong><span>Mail-health signals and deliverability diagnostics.</span></div>'
        '<div><strong>Authorization / policy</strong><span>Authorized sender domains and recipient policy.</span></div>'
        '<div><strong>Security</strong><span>Security posture and protective controls.</span></div>'
        '</div><nav class="v971-mail-safety-tabs" aria-label="Mail Safety sections">'
        + "".join(tabs)
        + "</nav></section>"
    )


def _inject_safety_header(html: str, active: str) -> str:
    if 'data-v971-mail-safety="1"' in html:
        return html
    pos = html.find(">")
    if pos < 0:
        return html
    return html[: pos + 1] + _safety_header(active) + html[pos + 1 :]


def _wrap_renderer(renderer: Callable[..., str], view: str) -> Callable[..., str]:
    def wrapped(*args: Any, **kwargs: Any) -> str:
        return _inject_safety_header(renderer(*args, **kwargs), view)

    wrapped.__name__ = getattr(renderer, "__name__", f"render_{view.replace('-', '_')}")
    wrapped.__doc__ = getattr(renderer, "__doc__", None)
    return wrapped


def _install_primary_nav_alias(webgui_v962: Any) -> None:
    if _SCRIPT_MARKER in str(webgui_v962.SCRIPT):
        return
    needle = "document.querySelectorAll('[data-v962-nav]').forEach(a => a.classList.toggle('active', a.dataset.v962Nav === view));"
    replacement = (
        "// v971-mail-safety-primary: compatibility subviews share one top-level navigation state.\n"
        "    const primaryView = ['mail-health','suppressions','domains','recipients','security'].includes(view) ? 'mail-health' : view;\n"
        "    document.querySelectorAll('[data-v962-nav]').forEach(a => a.classList.toggle('active', a.dataset.v962Nav === primaryView));"
    )
    if needle in webgui_v962.SCRIPT:
        webgui_v962.SCRIPT = webgui_v962.SCRIPT.replace(needle, replacement, 1)


def install_mail_safety_ia_v971(webgui_v962: Any, webgui_v962_views: Any) -> None:
    """Consolidate policy/health/security presentation while preserving every legacy view/backend."""
    if getattr(webgui_v962, _INSTALLED_FLAG, False):
        return

    # Keep every compatibility view in VIEWS/render_view. Only primary navigation is consolidated.
    webgui_v962.NAV = _consolidated_nav(webgui_v962.NAV)

    webgui_v962_views.v960.render_mail_health = _wrap_renderer(
        webgui_v962_views.v960.render_mail_health, "mail-health"
    )
    webgui_v962_views.render_suppressions = _wrap_renderer(
        webgui_v962_views.render_suppressions, "suppressions"
    )
    webgui_v962_views.render_domains = _wrap_renderer(webgui_v962_views.render_domains, "domains")
    webgui_v962_views.render_recipients = _wrap_renderer(
        webgui_v962_views.render_recipients, "recipients"
    )
    webgui_v962_views.v951.render_security = _wrap_renderer(
        webgui_v962_views.v951.render_security, "security"
    )

    if "post-v9.7.0 mail safety / reputation IA" not in webgui_v962.BASE_STYLE:
        webgui_v962.BASE_STYLE += STYLE
    _install_primary_nav_alias(webgui_v962)
    setattr(webgui_v962, _INSTALLED_FLAG, True)


__all__ = [
    "SAFETY_VIEWS",
    "_consolidated_nav",
    "_inject_safety_header",
    "_safety_header",
    "install_mail_safety_ia_v971",
]
