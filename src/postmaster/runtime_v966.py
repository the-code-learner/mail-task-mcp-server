from __future__ import annotations

from functools import lru_cache
from typing import Any

from .privacy_provisioning_v966 import PrivacyProxyProvisioning
from .runtime_v963 import privacy_proxy_store


@lru_cache(maxsize=1)
def privacy_proxy_provisioning() -> PrivacyProxyProvisioning:
    return PrivacyProxyProvisioning(privacy_proxy_store())


def _public_proxy_status(result: dict[str, Any]) -> dict[str, Any]:
    clean = dict(result)
    for key in (
        "secret", "secret_value", "secret_enc", "privacy_proxy_secret",
        "private_key", "private_key_enc", "pending_secret", "pending_secret_enc",
    ):
        clean.pop(key, None)
    for value in clean.values():
        if isinstance(value, dict):
            for key in (
                "secret_value", "secret_enc", "privacy_proxy_secret",
                "private_key", "private_key_enc", "pending_secret", "pending_secret_enc",
            ):
                value.pop(key, None)
    return clean


def install_runtime_v966(base: Any, core: Any, legacy_build_status: Any):
    """Install the frozen v9.6.6 legacy compatibility surface."""

    legacy_email_security_status = base.email_security_status
    legacy_set_amp_account_state = base.set_amp_account_state
    service = privacy_proxy_provisioning()

    def build_status(
        operation: str = "status",
        target_version: str | None = None,
        force_refresh: bool = False,
        confirm_version_change: str | None = None,
    ):
        """Legacy mixed v9.6.6 runtime compatibility surface.

        This command contains read-only status/discovery behavior plus guarded historical runtime
        version-change actions. v9.6.7 clients should use runtime_status,
        runtime_version_change_preview and runtime_version_change_execute so each command name has
        one stable lifecycle classification.
        """
        status = legacy_build_status(
            operation=operation,
            target_version=target_version,
            force_refresh=force_refresh,
            confirm_version_change=confirm_version_change,
        )
        if not isinstance(status, dict):
            status = {"ok": True}
        status.update(
            {
                "version_capability": "9.6.6",
                "privacy_proxy_mcp_native_provisioning": True,
                "privacy_proxy_provisioning_signature": "Ed25519",
                "privacy_proxy_shared_secret_exposed_to_mcp": False,
                "privacy_proxy_private_signing_key_exposed_to_mcp": False,
                "privacy_proxy_mutations_preview_first": True,
                "new_mail_mcp_commands": 0,
                "mcp_command_count_expected": 90,
                "privacy_proxy_provisioning": service.public_status(),
            }
        )
        return _public_proxy_status(status)

    core.mcp.remove_tool("build_status")
    core.mcp.add_tool(build_status, name="build_status")
    base.build_status = build_status
    core.build_status = build_status

    def email_security_status(account_id: str | None = None):
        result = legacy_email_security_status(account_id)
        if not isinstance(result, dict):
            result = {"ok": True}
        result["privacy_proxy_provisioning"] = service.public_status()
        return _public_proxy_status(result)

    core.mcp.remove_tool("email_security_status")
    core.mcp.add_tool(email_security_status, name="email_security_status")
    base.email_security_status = email_security_status
    core.email_security_status = email_security_status

    def set_amp_account_state(
        account_id: str | None = None,
        enabled: bool | None = None,
        tested: bool | None = None,
        registered: bool | None = None,
        review_sent: bool = False,
        notes: str | None = None,
        privacy_proxy_worker_url: str | None = None,
        privacy_proxy_secret: str | None = None,
        privacy_proxy_enabled: bool | None = None,
        tracking_obfuscation: bool | None = None,
        high_noise_decoy_enabled: bool | None = None,
        privacy_proxy_test: bool = False,
        privacy_proxy_dismiss_offer: bool = False,
        privacy_proxy_action: str | None = None,
        privacy_proxy_confirm: str | None = None,
    ):
        """Legacy mixed v9.6.6 AMP and Privacy Proxy compatibility surface.

        The frozen schema contains ordinary AMP/configuration writes, a read-only Privacy Proxy
        status action, and historical preview/execute provisioning behavior. v9.6.7 clients should
        use privacy_proxy_status, privacy_proxy_provisioning_preview and
        privacy_proxy_provisioning_execute for the MCP-native provisioning lifecycle.

        Existing mutating provisioning actions remain preview-first for backwards compatibility.
        The MCP-native flow never returns the generated proxy HMAC secret or Ed25519 private
        signing key. The legacy privacy_proxy_secret argument remains supported only for
        backwards-compatible manual deployments and is write-only.
        """
        action = str(privacy_proxy_action or "").strip().lower()
        if not action:
            return _public_proxy_status(
                legacy_set_amp_account_state(
                    account_id=account_id,
                    enabled=enabled,
                    tested=tested,
                    registered=registered,
                    review_sent=review_sent,
                    notes=notes,
                    privacy_proxy_worker_url=privacy_proxy_worker_url,
                    privacy_proxy_secret=privacy_proxy_secret,
                    privacy_proxy_enabled=privacy_proxy_enabled,
                    tracking_obfuscation=tracking_obfuscation,
                    high_noise_decoy_enabled=high_noise_decoy_enabled,
                    privacy_proxy_test=privacy_proxy_test,
                    privacy_proxy_dismiss_offer=privacy_proxy_dismiss_offer,
                )
            )

        if action == "status":
            if privacy_proxy_confirm:
                return {
                    "ok": False,
                    "error": "privacy_proxy_confirm is not accepted for the read-only status action",
                    "privacy_proxy_provisioning": service.public_status(),
                }
            mixed = any(
                value is not None
                for value in (
                    enabled, tested, registered, notes, privacy_proxy_secret,
                    privacy_proxy_enabled, tracking_obfuscation, high_noise_decoy_enabled,
                )
            ) or bool(review_sent or privacy_proxy_test or privacy_proxy_dismiss_offer)
            if mixed or privacy_proxy_worker_url is not None:
                return {
                    "ok": False,
                    "error": "Privacy Proxy status cannot be combined with mutating arguments",
                    "privacy_proxy_provisioning": service.public_status(),
                }
            return {
                "ok": True,
                "privacy_proxy_action": "status",
                "approval_required": False,
                "privacy_proxy": privacy_proxy_store().status(),
                "privacy_proxy_provisioning": service.public_status(),
            }

        allowed = {
            "prepare_provisioning", "provision", "rotate", "reconcile", "deprovision",
        }
        if action not in allowed:
            return {
                "ok": False,
                "error": f"unsupported Privacy Proxy action: {action}",
                "privacy_proxy_provisioning": service.public_status(),
            }

        mixed = any(
            value is not None
            for value in (
                enabled, tested, registered, notes, privacy_proxy_secret,
                privacy_proxy_enabled, tracking_obfuscation, high_noise_decoy_enabled,
            )
        ) or bool(review_sent or privacy_proxy_test or privacy_proxy_dismiss_offer)
        if mixed:
            return {
                "ok": False,
                "error": "Privacy Proxy provisioning actions cannot be combined with other mutations",
                "privacy_proxy_provisioning": service.public_status(),
            }
        if action != "prepare_provisioning" and privacy_proxy_worker_url is not None:
            return {
                "ok": False,
                "error": (
                    "privacy_proxy_worker_url may only be bound during prepare_provisioning; "
                    "later actions use the persisted Worker URL"
                ),
                "privacy_proxy_provisioning": service.public_status(),
            }

        worker_url = privacy_proxy_worker_url if action == "prepare_provisioning" else None
        if not privacy_proxy_confirm:
            try:
                return _public_proxy_status(service.preview(action, worker_url=worker_url))
            except Exception as exc:
                return {
                    "ok": False,
                    "error": str(exc),
                    "action_applied": False,
                    "privacy_proxy_provisioning": service.public_status(),
                }
        result = service.execute(
            action,
            confirmation_token=privacy_proxy_confirm,
            worker_url=worker_url,
        )
        return _public_proxy_status(result)

    core.mcp.remove_tool("set_amp_account_state")
    core.mcp.add_tool(set_amp_account_state, name="set_amp_account_state")
    base.set_amp_account_state = set_amp_account_state
    core.set_amp_account_state = set_amp_account_state
    base.privacy_proxy_provisioning = privacy_proxy_provisioning
    return build_status


__all__ = ["install_runtime_v966", "privacy_proxy_provisioning"]
