from __future__ import annotations

from typing import Any

from mcp.types import ToolAnnotations

from .confirmation_v967 import (
    CONFIRMATION_TTL_SECONDS,
    PersistentConfirmationTokens,
    StateBoundConfirmationTokens,
)
from . import runtime_control
from .runtime_v966 import _public_proxy_status, privacy_proxy_provisioning


RUNTIME_OPERATIONS = {
    "update-latest",
    "switch-version",
    "pin-version",
    "rollback-version",
}
PRIVACY_PROXY_ACTIONS = {
    "prepare_provisioning",
    "provision",
    "rotate",
    "reconcile",
    "deprovision",
}
MCP_COMMAND_COUNT_V966 = 90
MCP_COMMAND_COUNT_V967 = 96


def _runtime_selector(status: dict[str, Any]) -> str:
    control = runtime_control.read_control()
    if control.get("selector"):
        return str(control["selector"])
    requested = str(status.get("requested_version") or "").strip()
    if requested == "latest":
        return "latest"
    try:
        return runtime_control.canonical_selector(requested)
    except ValueError:
        return "latest"


def _local_runtime_status(legacy_build_status: Any) -> dict[str, Any]:
    try:
        status = legacy_build_status(operation="status")
    except TypeError:
        status = legacy_build_status()
    if not isinstance(status, dict):
        status = {"ok": True}
    result = dict(status)
    result.update(
        {
            "version_capability": "9.6.7",
            "mcp_lifecycle_hardening": True,
            "mcp_command_count_expected": MCP_COMMAND_COUNT_V967,
            "mcp_command_count_delta_from_v966": MCP_COMMAND_COUNT_V967 - MCP_COMMAND_COUNT_V966,
            "runtime_selector": _runtime_selector(result),
            "runtime_version_change_preview_first": True,
            "runtime_version_change_confirmation_ttl_seconds": CONFIRMATION_TTL_SECONDS,
            "legacy_build_status_contract": "v9.6.6-compatible-deprecated-for-version-change",
            "production_activation_separate_from_source_release": True,
        }
    )
    return result


def _release_target(
    operation: str,
    target_version: str | None,
    *,
    execute: bool,
) -> tuple[str, str, list[str]]:
    op = str(operation or "").strip().lower()
    if op not in RUNTIME_OPERATIONS:
        raise ValueError(f"unsupported runtime version operation: {op or '<empty>'}")
    versions, release_status = runtime_control.stable_release_tags(force=False)
    if release_status != "ok" or not versions:
        raise RuntimeError("stable release discovery is unavailable; retry the preview later")

    raw_target = str(target_version or "").strip()
    if op == "update-latest":
        if execute:
            if not raw_target:
                raise ValueError("target_version must be the exact vX.Y.Z target returned by preview")
            target = runtime_control.canonical_selector(raw_target)
            if target == "latest":
                raise ValueError("execute requires the exact vX.Y.Z target returned by preview")
            if target != versions[0]:
                raise ValueError(
                    "the stable latest release changed after preview; request a new preview and approval"
                )
        else:
            if raw_target not in {"", "latest"}:
                raise ValueError("update-latest preview does not accept an explicit version target")
            target = versions[0]
        requested_selector = "latest"
    else:
        if not raw_target:
            raise ValueError(f"{op} requires target_version=vX.Y.Z")
        target = runtime_control.canonical_selector(raw_target)
        if target == "latest":
            raise ValueError(f"{op} requires an explicit stable vX.Y.Z target")
        requested_selector = target

    if target not in versions:
        raise ValueError("target version is not a verified stable GitHub release")
    return target, requested_selector, versions


def _privacy_confirmation_state(service: Any) -> dict[str, Any]:
    provisioning = service.public_status()
    proxy = service.store.status()
    return {
        "worker_url": str(proxy.get("worker_url") or ""),
        "configured": bool(proxy.get("configured")),
        "secret_configured": bool(proxy.get("secret_configured")),
        "enabled": bool(proxy.get("enabled")),
        "phase": str(provisioning.get("phase") or ""),
        "prepared": bool(provisioning.get("prepared")),
        "key_id": str(provisioning.get("key_id") or ""),
        "fingerprint": str(provisioning.get("fingerprint") or ""),
        "generation": int(provisioning.get("generation") or 0),
        "provisioned": bool(provisioning.get("provisioned")),
        "pending": bool(provisioning.get("pending")),
        "pending_generation": provisioning.get("pending_generation"),
        "pending_operation": str(provisioning.get("pending_operation") or ""),
    }


def configure_privacy_proxy_confirmations(
    service: Any,
    *,
    key_path: str | None = None,
    db_path: str | None = None,
) -> PersistentConfirmationTokens:
    tokens = PersistentConfirmationTokens(
        scope="privacy_proxy_provisioning",
        ttl_seconds=CONFIRMATION_TTL_SECONDS,
        key_path=key_path,
        db_path=db_path,
    )
    service.confirmations = StateBoundConfirmationTokens(
        tokens,
        lambda: _privacy_confirmation_state(service),
    )
    return tokens


def install_runtime_v967(
    base: Any,
    core: Any,
    legacy_build_status: Any,
    *,
    provisioning_service: Any | None = None,
    confirmation_key_path: str | None = None,
    confirmation_db_path: str | None = None,
    replace_runtime_confirmation_backend: bool = False,
):
    """Install v9.6.7 stable lifecycle tools without re-registering any legacy command name."""

    service = provisioning_service or privacy_proxy_provisioning()
    runtime_control.initialize_version_change_approvals(
        key_path=confirmation_key_path,
        db_path=confirmation_db_path,
        replace=replace_runtime_confirmation_backend,
    )
    privacy_confirmation_tokens = configure_privacy_proxy_confirmations(
        service,
        key_path=confirmation_key_path,
        db_path=confirmation_db_path,
    )

    def runtime_status():
        """Read-only local runtime identity/state. Performs no release-network lookup and no mutation."""
        return _local_runtime_status(legacy_build_status)

    def runtime_version_change_preview(
        operation: str,
        target_version: str | None = None,
    ):
        """Read-only preview for one exact runtime version change; explicit user approval is required next."""
        try:
            target, requested_selector, versions = _release_target(
                operation,
                target_version,
                execute=False,
            )
            status = _local_runtime_status(legacy_build_status)
            current_selector = _runtime_selector(status)
            current_build = str(status.get("build") or "unknown")
            token = runtime_control.issue_version_change_approval(
                operation=str(operation).strip().lower(),
                target=target,
                current_selector=current_selector,
                current_build=current_build,
            )
            destructive = str(operation).strip().lower() == "rollback-version"
            return {
                "ok": True,
                "runtime_version_action": str(operation).strip().lower(),
                "action_preview": {
                    "operation": str(operation).strip().lower(),
                    "current_version": str(status.get("version") or "unknown"),
                    "current_build": current_build,
                    "current_selector": current_selector,
                    "requested_selector": requested_selector,
                    "target_version_ref": target,
                    "operation_destructive": destructive,
                },
                "approval_required": True,
                "version_change_applied": False,
                "confirmation_token": token,
                "confirmation_expires_in_seconds": runtime_control.version_change_confirmation_ttl_seconds(),
                "stable_release_count": len(versions),
                "next_step": (
                    "Show the exact operation, current version/build/selector and target_version_ref to the "
                    "user. After explicit approval in the active chat, call runtime_version_change_execute "
                    "with this exact target and one-time confirmation token."
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
        confirmation_token: str,
    ):
        """Execute one explicitly approved runtime version change bound to the previewed target/state."""
        op = str(operation or "").strip().lower()
        try:
            target, requested_selector, _ = _release_target(
                op,
                target_version,
                execute=True,
            )
            status = _local_runtime_status(legacy_build_status)
            current_selector = _runtime_selector(status)
            current_build = str(status.get("build") or "unknown")
            if not runtime_control.consume_version_change_approval(
                confirmation_token,
                operation=op,
                target=target,
                current_selector=current_selector,
                current_build=current_build,
            ):
                return {
                    "ok": False,
                    "runtime_version_action": op,
                    "approval_required": True,
                    "version_change_applied": False,
                    "error": (
                        "missing, expired, already-used, or mismatched runtime confirmation; "
                        "request a new preview and explicit user approval"
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
                "source_release_publication_triggered": False,
                "production_activation_automatic": False,
                "next_step": "Reconnect after restart and verify runtime_status before relying on the new runtime.",
            }
        except Exception as exc:
            return {
                "ok": False,
                "runtime_version_action": op,
                "approval_required": True,
                "version_change_applied": False,
                "error": str(exc),
            }

    def privacy_proxy_status():
        """Read-only local Privacy Proxy configuration/provisioning status; private material is never returned."""
        return _public_proxy_status(
            {
                "ok": True,
                "privacy_proxy": service.store.status(),
                "privacy_proxy_provisioning": service.public_status(),
                "legacy_set_amp_account_state_contract": "v9.6.6-compatible-deprecated-for-provisioning",
            }
        )

    def privacy_proxy_provisioning_preview(
        action: str,
        worker_url: str | None = None,
    ):
        """Read-only preview for one exact Privacy Proxy provisioning action; no network request or mutation."""
        normalized = str(action or "").strip().lower()
        if normalized not in PRIVACY_PROXY_ACTIONS:
            return {
                "ok": False,
                "error": f"unsupported Privacy Proxy action: {normalized or '<empty>'}",
                "action_applied": False,
            }
        if normalized == "prepare_provisioning" and not str(worker_url or "").strip():
            return {
                "ok": False,
                "error": "prepare_provisioning requires the exact HTTPS worker_url to bind in the preview",
                "action_applied": False,
            }
        if normalized != "prepare_provisioning" and worker_url is not None:
            return {
                "ok": False,
                "error": (
                    "worker_url may only be bound during prepare_provisioning; later actions use the "
                    "persisted Worker URL"
                ),
                "action_applied": False,
            }
        effective_worker_url = worker_url if normalized == "prepare_provisioning" else None
        try:
            result = service.preview(normalized, worker_url=effective_worker_url)
            result["action_destructive"] = normalized == "deprovision"
            result["confirmation_expires_in_seconds"] = CONFIRMATION_TTL_SECONDS
            result["next_step"] = (
                "Show the exact Privacy Proxy action, Worker origin, public key identity/generation and "
                "current state to the user. After explicit approval in the active chat, call "
                "privacy_proxy_provisioning_execute with this one-time token."
            )
            return _public_proxy_status(result)
        except Exception as exc:
            return {
                "ok": False,
                "error": str(exc),
                "approval_required": False,
                "action_applied": False,
                "privacy_proxy_provisioning": service.public_status(),
            }

    def privacy_proxy_provisioning_execute(
        action: str,
        confirmation_token: str,
        worker_url: str | None = None,
    ):
        """Execute one explicitly approved Privacy Proxy provisioning action using the preview token."""
        normalized = str(action or "").strip().lower()
        if normalized not in PRIVACY_PROXY_ACTIONS:
            return {"ok": False, "error": f"unsupported Privacy Proxy action: {normalized or '<empty>'}"}
        if normalized == "prepare_provisioning" and not str(worker_url or "").strip():
            return {
                "ok": False,
                "error": "prepare_provisioning execute requires the exact worker_url returned by preview",
                "action_applied": False,
            }
        if normalized != "prepare_provisioning" and worker_url is not None:
            return {
                "ok": False,
                "error": (
                    "worker_url may only be bound during prepare_provisioning; later actions use the "
                    "persisted Worker URL"
                ),
                "action_applied": False,
            }
        result = service.execute(
            normalized,
            confirmation_token=confirmation_token,
            worker_url=worker_url if normalized == "prepare_provisioning" else None,
        )
        if isinstance(result, dict):
            result["action_destructive"] = normalized == "deprovision"
        return _public_proxy_status(result)

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
        privacy_proxy_status,
        name="privacy_proxy_status",
        annotations=ToolAnnotations(
            read_only_hint=True,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=False,
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

    base.runtime_status = runtime_status
    base.runtime_version_change_preview = runtime_version_change_preview
    base.runtime_version_change_execute = runtime_version_change_execute
    base.privacy_proxy_status = privacy_proxy_status
    base.privacy_proxy_provisioning_preview = privacy_proxy_provisioning_preview
    base.privacy_proxy_provisioning_execute = privacy_proxy_provisioning_execute
    base.privacy_proxy_confirmation_tokens_v967 = privacy_confirmation_tokens
    core.runtime_status = runtime_status
    core.runtime_version_change_preview = runtime_version_change_preview
    core.runtime_version_change_execute = runtime_version_change_execute
    core.privacy_proxy_status = privacy_proxy_status
    core.privacy_proxy_provisioning_preview = privacy_proxy_provisioning_preview
    core.privacy_proxy_provisioning_execute = privacy_proxy_provisioning_execute
    return runtime_status


__all__ = [
    "MCP_COMMAND_COUNT_V967",
    "PRIVACY_PROXY_ACTIONS",
    "RUNTIME_OPERATIONS",
    "configure_privacy_proxy_confirmations",
    "install_runtime_v967",
]
