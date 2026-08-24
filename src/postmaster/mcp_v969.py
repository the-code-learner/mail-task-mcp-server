from __future__ import annotations

from typing import Any

from mcp.types import ToolAnnotations

from . import runtime_control
from . import runtime_v967 as v967
from .pending_approval_v969 import PENDING_APPROVAL_TTL_SECONDS, PendingApprovalStore, PendingConfirmationAdapter
from .privacy_cache_v969 import PassiveContentService

MCP_COMMAND_COUNT_V968 = 96
MCP_COMMAND_COUNT_V969 = 97

def _runtime_version_binding(
    status: dict[str, Any],
    *,
    operation: str,
    target: str,
) -> dict[str, Any]:
    return {
        "operation": str(operation),
        "target": str(target),
        "current_selector": v967._runtime_selector(status),
        "current_build": str(status.get("build") or "unknown"),
        "current_version": str(status.get("version") or "unknown"),
    }


def install_runtime_v969_mcp(
    base: Any,
    core: Any,
    legacy_runtime_status: Any,
    *,
    pending_db_path: str | None = None,
) -> Any:
    """Install the one explicit networked mail command and server-side lifecycle approvals."""
    pending = PendingApprovalStore(
        pending_db_path,
        ttl_seconds=PENDING_APPROVAL_TTL_SECONDS,
    )
    service = base.passive_content_service_v969()
    provisioning = (
        base.privacy_proxy_provisioning()
        if callable(getattr(base, "privacy_proxy_provisioning", None))
        else None
    )
    if provisioning is not None:
        provisioning.confirmations = PendingConfirmationAdapter(
            pending,
            scope="privacy_proxy_provisioning",
            state_provider=lambda: v967._privacy_confirmation_state(provisioning),
        )

    def runtime_status():
        status = legacy_runtime_status()
        if not isinstance(status, dict):
            status = {"ok": True}
        status.update(
            {
                "version_capability": "9.6.9",
                "mcp_command_count_expected": MCP_COMMAND_COUNT_V969,
                "mcp_command_count_delta_from_v968": (
                    MCP_COMMAND_COUNT_V969 - MCP_COMMAND_COUNT_V968
                ),
                "full_html_shared_webgui_mcp_pipeline": True,
                "passive_resource_persistent_cache": True,
                "logical_sent_single_append": True,
                "sender_private_bcc_metadata": True,
                "mcp_pending_preview_server_side": True,
                "confirmation_bearer_token_required": False,
            }
        )
        return status

    def fetch_email_remote_content(
        mailbox: str,
        uid: str,
        account_id: str | None = None,
        authorize_remote_fetch: bool = False,
        refresh: bool = False,
    ):
        """NETWORK WRITE/CACHE ACTION for Full HTML passive resources.

        Safe Email and get_email remain zero-origin-network. Call this command only after the
        user explicitly approves remote passive-resource fetching. refresh=True intentionally
        discards this message's passive-resource cache/tombstones and performs a new origin round.
        Navigation/action/form URLs are never eligible.
        """
        if not authorize_remote_fetch:
            return {
                "ok": False,
                "approval_required": True,
                "remote_fetch_performed": False,
                "network_requests_performed": 0,
                "next_step": (
                    "Obtain fresh explicit user approval for remote passive-resource fetching, "
                    "then call again with authorize_remote_fetch=true."
                ),
            }
        selected_account = str(account_id or "").strip()
        if not selected_account:
            try:
                selected_account = str(base.account_store().resolve_id(None))
            except Exception:
                selected_account = ""
        if not selected_account:
            return {
                "ok": False,
                "approval_required": False,
                "remote_fetch_performed": False,
                "error": "account_id is required",
            }
        try:
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
                "render_state": result.get("render_state"),
                "cache_only": bool(result.get("cache_only")),
                "refresh": bool(result.get("refresh")),
                "diagnostics": diag,
                "shared_with_webgui": True,
                "navigation_action_urls_auto_fetched": False,
            }
        except Exception as exc:
            return {
                "ok": False,
                "approval_required": False,
                "remote_fetch_performed": False,
                "render_state": "failure",
                "error": type(exc).__name__,
            }

    def runtime_version_change_preview(
        operation: str,
        target_version: str | None = None,
    ):
        try:
            target, requested_selector, versions = v967._release_target(
                operation, target_version, execute=False
            )
            status = legacy_runtime_status()
            if not isinstance(status, dict):
                status = {"ok": True}
            op = str(operation or "").strip().lower()
            binding = _runtime_version_binding(status, operation=op, target=target)
            preview_id = pending.issue("runtime_version_change", binding)
            return {
                "ok": True,
                "runtime_version_action": op,
                "action_preview": {
                    "operation": op,
                    "current_version": str(status.get("version") or "unknown"),
                    "current_build": str(status.get("build") or "unknown"),
                    "current_selector": v967._runtime_selector(status),
                    "requested_selector": requested_selector,
                    "target_version_ref": target,
                    "operation_destructive": op == "rollback-version",
                },
                "approval_required": True,
                "version_change_applied": False,
                "preview_id": preview_id,
                "preview_id_is_authorization": False,
                "confirmation_expires_in_seconds": pending.ttl_seconds,
                "stable_release_count": len(versions),
                "next_step": (
                    "Show the exact preview to the user. After fresh explicit approval, call "
                    "runtime_version_change_execute with the same operation and target. "
                    "preview_id is optional correlation metadata and is not bearer authorization."
                ),
            }
        except Exception as exc:
            return {
                "ok": False,
                "error": str(exc),
                "approval_required": False,
                "version_change_applied": False,
            }

    def runtime_version_change_execute(
        operation: str,
        target_version: str,
        preview_id: str | None = None,
    ):
        op = str(operation or "").strip().lower()
        try:
            target, requested_selector, _ = v967._release_target(
                op, target_version, execute=True
            )
            status = legacy_runtime_status()
            if not isinstance(status, dict):
                status = {"ok": True}
            binding = _runtime_version_binding(status, operation=op, target=target)
            if not pending.consume_matching(
                "runtime_version_change",
                binding,
                preview_id=preview_id,
            ):
                return {
                    "ok": False,
                    "runtime_version_action": op,
                    "approval_required": True,
                    "version_change_applied": False,
                    "error": (
                        "missing, expired, already-used, stale, or mismatched server-side "
                        "preview; request a new preview and fresh explicit approval"
                    ),
                }
            if op == "update-latest":
                control = runtime_control.write_control(
                    selector="latest",
                    restart_ref_once=target,
                    check_updates_once=False,
                )
            else:
                control = runtime_control.write_control(selector=target)
            runtime_control.schedule_current_process_termination()
            return {
                "ok": True,
                "runtime_version_action": op,
                "approval_required": False,
                "version_change_applied": True,
                "target_version_ref": target,
                "requested_selector": requested_selector,
                "runtime_control": control,
                "restart_scheduled": True,
                "confirmation_token_transmitted": False,
            }
        except Exception as exc:
            return {
                "ok": False,
                "runtime_version_action": op,
                "approval_required": True,
                "version_change_applied": False,
                "error": str(exc),
            }

    def privacy_proxy_provisioning_preview(
        action: str,
        worker_url: str | None = None,
    ):
        if provisioning is None:
            return {"ok": False, "error": "Privacy Proxy provisioning unavailable"}
        normalized = str(action or "").strip().lower()
        if normalized not in v967.PRIVACY_PROXY_ACTIONS:
            return {
                "ok": False,
                "error": f"unsupported Privacy Proxy action: {normalized or '<empty>'}",
                "action_applied": False,
            }
        if normalized == "prepare_provisioning" and not str(worker_url or "").strip():
            return {
                "ok": False,
                "error": "prepare_provisioning requires the exact HTTPS worker_url",
                "action_applied": False,
            }
        if normalized != "prepare_provisioning" and worker_url is not None:
            return {
                "ok": False,
                "error": "worker_url is accepted only for prepare_provisioning",
                "action_applied": False,
            }
        try:
            result = provisioning.preview(
                normalized,
                worker_url=worker_url if normalized == "prepare_provisioning" else None,
            )
            preview_id = str(result.pop("confirmation_token", "") or "")
            result["preview_id"] = preview_id
            result["preview_id_is_authorization"] = False
            result["confirmation_token_transmitted"] = False
            result["next_step"] = (
                "Show the exact Privacy Proxy preview to the user. After fresh explicit "
                "approval, call privacy_proxy_provisioning_execute with the same action/target. "
                "preview_id is optional correlation metadata and is not bearer authorization."
            )
            return v967._public_proxy_status(result)
        except Exception as exc:
            return {
                "ok": False,
                "error": str(exc),
                "approval_required": False,
                "action_applied": False,
                "privacy_proxy_provisioning": provisioning.public_status(),
            }

    def privacy_proxy_provisioning_execute(
        action: str,
        worker_url: str | None = None,
        preview_id: str | None = None,
    ):
        if provisioning is None:
            return {"ok": False, "error": "Privacy Proxy provisioning unavailable"}
        normalized = str(action or "").strip().lower()
        if normalized not in v967.PRIVACY_PROXY_ACTIONS:
            return {
                "ok": False,
                "error": f"unsupported Privacy Proxy action: {normalized or '<empty>'}",
            }
        if normalized == "prepare_provisioning" and not str(worker_url or "").strip():
            return {
                "ok": False,
                "error": "prepare_provisioning execute requires the exact worker_url",
                "action_applied": False,
            }
        if normalized != "prepare_provisioning" and worker_url is not None:
            return {
                "ok": False,
                "error": "worker_url is accepted only for prepare_provisioning",
                "action_applied": False,
            }
        try:
            result = provisioning.execute(
                normalized,
                confirmation_token=str(preview_id or ""),
                worker_url=worker_url if normalized == "prepare_provisioning" else None,
            )
            if isinstance(result, dict):
                result["confirmation_token_transmitted"] = False
            return v967._public_proxy_status(result)
        except Exception as exc:
            return {
                "ok": False,
                "error": type(exc).__name__,
                "approval_required": True,
                "action_applied": False,
            }

    for name in (
        "runtime_status",
        "runtime_version_change_preview",
        "runtime_version_change_execute",
        "privacy_proxy_provisioning_preview",
        "privacy_proxy_provisioning_execute",
    ):
        core.mcp.remove_tool(name)

    core.mcp.add_tool(
        runtime_status,
        name="runtime_status",
        annotations=ToolAnnotations(
            read_only_hint=True,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=False,
        ),
    )
    core.mcp.add_tool(
        runtime_version_change_preview,
        name="runtime_version_change_preview",
        annotations=ToolAnnotations(
            read_only_hint=True,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=True,
        ),
    )
    core.mcp.add_tool(
        runtime_version_change_execute,
        name="runtime_version_change_execute",
        annotations=ToolAnnotations(
            read_only_hint=False,
            destructive_hint=True,
            idempotent_hint=False,
            open_world_hint=True,
        ),
    )
    core.mcp.add_tool(
        privacy_proxy_provisioning_preview,
        name="privacy_proxy_provisioning_preview",
        annotations=ToolAnnotations(
            read_only_hint=True,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=False,
        ),
    )
    core.mcp.add_tool(
        privacy_proxy_provisioning_execute,
        name="privacy_proxy_provisioning_execute",
        annotations=ToolAnnotations(
            read_only_hint=False,
            destructive_hint=True,
            idempotent_hint=False,
            open_world_hint=True,
        ),
    )
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

    base.runtime_status = runtime_status
    base.runtime_version_change_preview = runtime_version_change_preview
    base.runtime_version_change_execute = runtime_version_change_execute
    base.privacy_proxy_provisioning_preview = privacy_proxy_provisioning_preview
    base.privacy_proxy_provisioning_execute = privacy_proxy_provisioning_execute
    base.fetch_email_remote_content = fetch_email_remote_content
    base.pending_approval_store_v969 = lambda: pending
    core.runtime_status = runtime_status
    core.runtime_version_change_preview = runtime_version_change_preview
    core.runtime_version_change_execute = runtime_version_change_execute
    core.privacy_proxy_provisioning_preview = privacy_proxy_provisioning_preview
    core.privacy_proxy_provisioning_execute = privacy_proxy_provisioning_execute
    core.fetch_email_remote_content = fetch_email_remote_content
    return runtime_status


__all__ = ["MCP_COMMAND_COUNT_V969", "install_runtime_v969_mcp"]
