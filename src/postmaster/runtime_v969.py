from __future__ import annotations

from typing import Any

from mcp.types import ToolAnnotations

from .mcp_v969 import (
    MCP_COMMAND_COUNT_V969,
    install_runtime_v969_mcp as _install_runtime_v969_mcp,
)
from .outbound_archive_v969 import _install_outbound_archive_boundary
from .privacy_cache_v969 import (
    PassiveContentService,
    install_hashed_resource_keys,
    rewrite_full_html_v969,
)
from .privacy_css_cache_v969 import CacheAwareBoundedPassiveContentService
from .privacy_css_v969 import BoundedPassiveContentService
from .webgui_v969 import _install_webgui_v969


def install_runtime_v969_pre_webgui(
    base: Any,
    core: Any,
    webgui_v963: Any,
) -> PassiveContentService:
    cache = base.mailbox_cache_store()
    install_hashed_resource_keys(cache)
    _install_outbound_archive_boundary()
    service = CacheAwareBoundedPassiveContentService(base)
    base.passive_content_service_v969 = lambda: service
    core.passive_content_service_v969 = lambda: service

    # v9.6.3 already owns the iframe/resource endpoint hardening. Replace only its
    # rewrite function so the exact same v9.6.9 renderer is used by WebGUI and MCP.
    webgui_v963.rewrite_full_html = rewrite_full_html_v969
    _install_webgui_v969(base, webgui_v963, service)
    return service


def install_runtime_v969_mcp(
    base: Any,
    core: Any,
    legacy_runtime_status: Any,
    *,
    pending_db_path: str | None = None,
) -> Any:
    """Install v9.6.9 MCP then tighten Full HTML parity without adding a command name."""

    runtime_status = _install_runtime_v969_mcp(
        base,
        core,
        legacy_runtime_status,
        pending_db_path=pending_db_path,
    )
    service = base.passive_content_service_v969()

    def _account(value: str | None) -> str:
        selected = str(value or "").strip()
        if selected:
            return selected
        try:
            return str(base.account_store().resolve_id(None))
        except Exception:
            return ""

    def fetch_email_remote_content(
        mailbox: str,
        uid: str,
        account_id: str | None = None,
        authorize_remote_fetch: bool = False,
        refresh: bool = False,
        cache_only: bool = False,
    ):
        """Explicit remote fetch OR zero-origin-network cached Full HTML read.

        cache_only=true never contacts passive-resource origins and never runs High-Noise.
        refresh=true is valid only on an explicitly authorized remote fetch cycle.
        Safe Email/get_email remain unchanged and zero-origin-network.
        """

        if cache_only and refresh:
            return {
                "ok": False,
                "approval_required": False,
                "remote_fetch_performed": False,
                "cache_only": True,
                "network_requests_performed": 0,
                "error": "refresh is incompatible with cache_only",
            }
        if not cache_only and not authorize_remote_fetch:
            return {
                "ok": False,
                "approval_required": True,
                "remote_fetch_performed": False,
                "cache_only": False,
                "network_requests_performed": 0,
                "next_step": (
                    "Obtain fresh explicit user approval for remote passive-resource fetching, "
                    "then call again with authorize_remote_fetch=true; use cache_only=true for "
                    "a zero-origin-network Full HTML read after cache population."
                ),
            }

        selected_account = _account(account_id)
        if not selected_account:
            return {
                "ok": False,
                "approval_required": False,
                "remote_fetch_performed": False,
                "cache_only": bool(cache_only),
                "network_requests_performed": 0,
                "error": "account_id is required",
            }

        try:
            if cache_only:
                result = service.render_cached_message(
                    account_id=selected_account,
                    mailbox=str(mailbox),
                    uid=str(uid),
                )
            else:
                result = service.fetch_message(
                    account_id=selected_account,
                    mailbox=str(mailbox),
                    uid=str(uid),
                    refresh=bool(refresh),
                )
            diag = dict(result.get("diagnostics") or {})
            return {
                "ok": bool(result.get("ok")),
                "approval_required": False,
                "remote_fetch_performed": bool(
                    int(diag.get("genuine_attempted") or 0)
                ),
                "network_requests_performed": int(
                    result.get("network_requests_performed") or 0
                ),
                "render_state": result.get("render_state"),
                "full_html_available": bool(result.get("full_html_available")),
                "rendered_html": str(result.get("rendered_html") or ""),
                "cache_only": bool(result.get("cache_only")),
                "refresh": bool(result.get("refresh")),
                "content_mode": (
                    "cache_only_full" if cache_only else "explicit_remote_full"
                ),
                "diagnostics": diag,
                "shared_with_webgui": True,
                "navigation_action_urls_auto_fetched": False,
                "cached_resource_contract": {
                    "representation": "postmaster-local",
                    "reference_prefix": "/dashboard/inbox/resource?key=",
                    "resource_bytes_embedded": False,
                    "bounded_css_nested_resources": True,
                },
            }
        except Exception as exc:
            return {
                "ok": False,
                "approval_required": False,
                "remote_fetch_performed": False,
                "network_requests_performed": 0,
                "render_state": "failure",
                "full_html_available": False,
                "rendered_html": "",
                "cache_only": bool(cache_only),
                "error": type(exc).__name__,
            }

    core.mcp.remove_tool("fetch_email_remote_content")
    core.mcp.add_tool(
        fetch_email_remote_content,
        name="fetch_email_remote_content",
        annotations=ToolAnnotations(
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=False,
            open_world_hint=True,
        ),
    )
    base.fetch_email_remote_content = fetch_email_remote_content
    core.fetch_email_remote_content = fetch_email_remote_content
    return runtime_status


__all__ = [
    "BoundedPassiveContentService",
    "CacheAwareBoundedPassiveContentService",
    "MCP_COMMAND_COUNT_V969",
    "PassiveContentService",
    "install_hashed_resource_keys",
    "install_runtime_v969_mcp",
    "install_runtime_v969_pre_webgui",
]
