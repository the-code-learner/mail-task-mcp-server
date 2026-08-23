from __future__ import annotations

import hashlib
from html import escape
from typing import Any

from starlette.requests import Request

from . import webgui_v951 as v951
from . import webgui_v960 as v960


_BASE_RENDER_INBOX = v960.render_inbox
_BASE_AUGMENT_DASHBOARD = v960.augment_dashboard
_PROJECT_PALETTE = (
    "#64b5f6", "#81c784", "#ffb74d", "#ba68c8", "#4dd0e1", "#f06292",
    "#aed581", "#ffd54f", "#7986cb", "#4db6ac", "#ff8a65", "#90a4ae",
)

STYLE = r'''
/* webgui-v961-hotfix */
.v961-project-chip { display:inline-flex;align-items:center;gap:5px;border:1px solid var(--project-color);border-radius:999px;padding:2px 7px;font-size:11px;font-weight:650;color:var(--text); }
.v961-project-dot { width:7px;height:7px;border-radius:50%;background:var(--project-color);display:inline-block;flex:0 0 auto; }
.v960-scope-chip.v961-project-filter { border-color:var(--project-color); }
.v960-scope-chip.v961-project-filter::before { content:"";width:7px;height:7px;border-radius:50%;background:var(--project-color);display:inline-block;margin-right:5px;vertical-align:1px; }
'''


def project_color(project_id: str, owner_id: str = "") -> str:
    """Stable project color independent of list/query ordering."""
    key = f"{owner_id}\x1f{project_id}".encode("utf-8")
    slot = int.from_bytes(hashlib.sha256(key).digest()[:2], "big") % len(_PROJECT_PALETTE)
    return _PROJECT_PALETTE[slot]


def _scope_labels_v961(item: dict[str, Any], names: dict[str, str]) -> str:
    scopes = [scope for scope in (item.get("scopes") or []) if isinstance(scope, dict)]
    if not scopes:
        return '<span class="v961-project-chip" style="--project-color:var(--muted)"><span class="v961-project-dot" aria-hidden="true"></span>Unscoped</span>'

    def sort_key(scope: dict[str, Any]) -> tuple[int, str, str]:
        owner = str(scope.get("owner_id") or "")
        pid = str(scope.get("project_id") or "")
        return (0 if scope.get("is_primary") else 1, owner.casefold(), pid.casefold())

    labels: list[str] = []
    for scope in sorted(scopes, key=sort_key):
        owner = str(scope.get("owner_id") or "")
        pid = str(scope.get("project_id") or "")
        label = names.get(pid, pid) if pid else "Global"
        suffix = " · primary" if scope.get("is_primary") else ""
        color = project_color(pid, owner) if pid else "var(--muted)"
        text = f"{owner} / {label}{suffix}" if owner else f"{label}{suffix}"
        labels.append(
            f'<span class="v961-project-chip" style="--project-color:{escape(color, quote=True)}">'
            '<span class="v961-project-dot" aria-hidden="true"></span>'
            f'{escape(text)}</span>'
        )
    return "".join(labels)


def _scope_chips_v961(
    request: Request,
    projects: list[dict[str, Any]],
    selected: list[str],
    global_selected: bool,
) -> str:
    all_active = not selected and not global_selected
    chips = [
        f'<a data-v960-fragment="knowledge" class="v960-scope-chip {"active" if all_active else ""}" '
        f'href="{escape(v960._scope_url(request, selected=[], global_selected=False), quote=True)}">Tutti</a>'
    ]
    chips.append(
        f'<a data-v960-fragment="knowledge" class="v960-scope-chip {"active" if global_selected else ""}" '
        f'href="{escape(v960._scope_url(request, selected=selected, global_selected=not global_selected), quote=True)}">Global</a>'
    )
    ordered = sorted(
        (row for row in projects if isinstance(row, dict)),
        key=lambda row: (
            str(row.get("owner_id") or "").casefold(),
            str(row.get("name") or row.get("id") or "").casefold(),
            str(row.get("id") or "").casefold(),
        ),
    )
    for row in ordered:
        pid = str(row.get("id") or "").strip()
        if not pid:
            continue
        owner = str(row.get("owner_id") or "")
        label = str(row.get("name") or pid)
        next_selected = [value for value in selected if value != pid] if pid in selected else selected + [pid]
        color = project_color(pid, owner)
        chips.append(
            f'<a data-v960-fragment="knowledge" class="v960-scope-chip v961-project-filter {"active" if pid in selected else ""}" '
            f'style="--project-color:{escape(color, quote=True)}" '
            f'href="{escape(v960._scope_url(request, selected=next_selected, global_selected=global_selected), quote=True)}">'
            f'{escape(owner + " / " + label)}</a>'
        )
    return '<nav class="v960-scope-chips" aria-label="Knowledge project filters">' + "".join(chips) + "</nav>"


class _InboxBaseProxy:
    """Limit WebGUI list enrichment without changing the MCP search contract."""

    def __init__(self, base: Any, limit: int) -> None:
        self._base = base
        self._limit = limit

    def __getattr__(self, name: str) -> Any:
        return getattr(self._base, name)

    def search_emails(self, *args: Any, **kwargs: Any) -> Any:
        requested = int(kwargs.get("limit") or self._limit)
        kwargs["limit"] = min(requested, self._limit)
        return self._base.search_emails(*args, **kwargs)


def inbox_prefetch_limit(request: Request) -> int:
    page = max(1, v951._bounded_int(request.query_params.get("page"), 1, 1, 1000))
    return min(100, page * 25 + 1)


def render_inbox_v961(base: Any, request: Request) -> str:
    """Delegate to the v9.6 Safe Reader contract: inspection="full", content_mode="safe"."""
    return _BASE_RENDER_INBOX(_InboxBaseProxy(base, inbox_prefetch_limit(request)), request)


def augment_dashboard_v961(body: str) -> str:
    return _BASE_AUGMENT_DASHBOARD(body).replace(
        "<small>Mail client · v9.6</small>",
        "<small>Mail client · v9.6.1</small>",
        1,
    )


def _patch_fragment_visibility() -> None:
    old = "    target.replaceWith(next);"
    new = (
        "    // v9.6.1: replacement fragments must inherit the visible tab state.\n"
        "    if (target.classList.contains('active')) next.classList.add('active');\n"
        "    target.replaceWith(next);"
    )
    if "replacement fragments must inherit" not in v960.SCRIPT and old in v960.SCRIPT:
        v960.SCRIPT = v960.SCRIPT.replace(old, new, 1)


def install_webgui_v961() -> None:
    """Install the v9.6.1 WebGUI hotfix after the v9.6 scope editor layer."""
    _patch_fragment_visibility()
    if "webgui-v961-hotfix" not in v960.STYLE:
        v960.STYLE += STYLE
    v960._scope_labels = _scope_labels_v961
    v960._scope_chips = _scope_chips_v961
    v960.render_inbox = render_inbox_v961
    v951.render_inbox = render_inbox_v961
    v960.augment_dashboard = augment_dashboard_v961
