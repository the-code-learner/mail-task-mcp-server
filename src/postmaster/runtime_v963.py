from __future__ import annotations

import os
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Any

from .email_privacy_v963 import PrivacyProxyClient, PrivacyProxyStore
from .mailbox_cache_v963 import MailboxCacheStore, MailboxCacheSynchronizer, MailboxSyncService


def _runtime_data_path(env_name: str, filename: str) -> str:
    explicit = (os.getenv(env_name) or "").strip()
    if explicit:
        return explicit
    data = Path("/data")
    try:
        data.mkdir(parents=True, exist_ok=True)
        probe = data / ".postmaster-write-probe"
        with probe.open("a", encoding="utf-8"):
            pass
        try:
            probe.unlink()
        except OSError:
            pass
        return str(data / filename)
    except OSError:
        return str(Path(tempfile.gettempdir()) / f"postmaster-{os.getuid()}-{filename}")


@lru_cache(maxsize=1)
def mailbox_cache_store() -> MailboxCacheStore:
    return MailboxCacheStore(_runtime_data_path("MAILBOX_CACHE_DB_PATH", "mailbox_cache.db"))


@lru_cache(maxsize=1)
def mailbox_cache_synchronizer() -> MailboxCacheSynchronizer:
    return MailboxCacheSynchronizer(mailbox_cache_store())


@lru_cache(maxsize=1)
def privacy_proxy_store() -> PrivacyProxyStore:
    return PrivacyProxyStore(
        _runtime_data_path("PRIVACY_PROXY_DB_PATH", "privacy_proxy.db"),
        _runtime_data_path("PRIVACY_PROXY_KEY_PATH", "privacy_proxy.key"),
    )


@lru_cache(maxsize=1)
def privacy_proxy_client() -> PrivacyProxyClient:
    return PrivacyProxyClient(privacy_proxy_store())


_SYNC_SERVICE: MailboxSyncService | None = None


def onboarding_state(base: Any) -> dict[str, Any]:
    try:
        account_count = len(base.account_store().list_accounts())
    except Exception:
        return {
            "established_installation": True,
            "full_onboarding": False,
            "privacy_proxy_offer": False,
            "fresh_install_resumable": False,
            "ambiguous_installation_state": True,
        }
    try:
        state = privacy_proxy_store().onboarding(account_count)
    except Exception:
        return {
            "established_installation": account_count > 0,
            "full_onboarding": account_count == 0,
            "privacy_proxy_offer": False,
            "fresh_install_resumable": account_count == 0,
            "ambiguous_installation_state": True,
        }
    state["ambiguous_installation_state"] = False
    return state


def _install_mcp_onboarding_instructions(base: Any, core: Any) -> None:
    """Add client-agnostic guidance without ever placing credentials in initialize text."""
    try:
        current = str(getattr(core.mcp, "instructions", "") or "")
        state = onboarding_state(base)
        marker = "v9.6.3 onboarding policy:"
        if marker in current:
            return
        if state.get("full_onboarding"):
            addition = (
                " v9.6.3 onboarding policy: this appears to be a fresh installation with no "
                "configured email account. Offer a resumable, client-agnostic setup and ask for "
                "explicit consent before configuration changes. Never send email during onboarding. "
                "Privacy Proxy setup is optional and its shared secret must never appear in MCP "
                "initialize instructions."
            )
        else:
            addition = (
                " v9.6.3 onboarding policy: this is an established installation; do not run the "
                "full first-run wizard. Privacy Proxy setup may only be offered as an optional, "
                "dismissible upgrade step. Never send email during onboarding."
            )
        setattr(core.mcp, "instructions", current + addition)
    except Exception:
        return


def install_runtime_v963(base: Any, core: Any, legacy_build_status: Any):
    """Install v9.6.3 cache/privacy/onboarding contracts without adding MCP command names."""
    global _SYNC_SERVICE

    legacy_email_security_status = base.email_security_status
    legacy_set_amp_account_state = base.set_amp_account_state

    base.mailbox_cache_store = mailbox_cache_store
    base.mailbox_cache_synchronizer = mailbox_cache_synchronizer
    base.privacy_proxy_store = privacy_proxy_store
    base.privacy_proxy_client = privacy_proxy_client
    base.postmaster_onboarding_state = lambda: onboarding_state(base)

    def build_status():
        status = legacy_build_status()
        if not isinstance(status, dict):
            status = {"ok": True}
        proxy = privacy_proxy_store().status()
        status.update(
            {
                "version_capability": "9.6.3",
                "inbox_cache_first": True,
                "mailbox_cache_db": True,
                "mailbox_sync_incremental": True,
                "mailbox_sync_interval_seconds": 300,
                "mailbox_sync_send_capability": False,
                "safe_email_zero_network": True,
                "full_html_two_step_consent": True,
                "full_html_passive_only_proxy": True,
                "navigation_urls_auto_fetched": False,
                "privacy_proxy": {
                    "configured": bool(proxy.get("configured")),
                    "enabled": bool(proxy.get("enabled")),
                    "secret_configured": bool(proxy.get("secret_configured")),
                    "tracking_obfuscation": bool(proxy.get("tracking_obfuscation")),
                    "high_noise_decoy_enabled": bool(proxy.get("high_noise_decoy_enabled")),
                },
                "onboarding": onboarding_state(base),
                "new_mail_mcp_commands": 0,
                "mcp_command_count_expected": 90,
            }
        )
        return status

    core.mcp.remove_tool("build_status")
    core.mcp.add_tool(build_status, name="build_status")
    base.build_status = build_status
    core.build_status = build_status

    def email_security_status(account_id: str | None = None):
        result = legacy_email_security_status(account_id)
        if not isinstance(result, dict):
            result = {"ok": True}
        proxy = privacy_proxy_store().status()
        result["privacy_proxy"] = {
            "configured": bool(proxy.get("configured")),
            "worker_url": str(proxy.get("worker_url") or ""),
            "secret_configured": bool(proxy.get("secret_configured")),
            "secret": "configured" if proxy.get("secret_configured") else "not configured",
            "enabled": bool(proxy.get("enabled")),
            "tracking_obfuscation": bool(proxy.get("tracking_obfuscation")),
            "high_noise_decoy_enabled": bool(proxy.get("high_noise_decoy_enabled")),
            "last_test_at": str(proxy.get("last_test_at") or ""),
            "last_test_ok": proxy.get("last_test_ok"),
            "last_test_error": str(proxy.get("last_test_error") or ""),
        }
        result["onboarding"] = onboarding_state(base)
        return result

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
    ):
        """WRITE ACTION. Existing admin surface for AMP state plus optional v9.6.3 Privacy Proxy configuration.

        The MCP command name is intentionally reused to keep the public tool count unchanged. The
        Privacy Proxy secret is write-only: it is encrypted at rest and never echoed by this tool.
        High-noise decoy traffic is a distinct persisted opt-in policy and defaults to off.
        """
        result: dict[str, Any] = {"ok": True}
        amp_change = any(value is not None for value in (enabled, tested, registered, notes)) or bool(review_sent)
        if amp_change:
            if not account_id:
                return {"ok": False, "error": "account_id is required when changing AMP account state"}
            amp_result = legacy_set_amp_account_state(
                account_id,
                enabled=enabled,
                tested=tested,
                registered=registered,
                review_sent=review_sent,
                notes=notes,
            )
            result["amp"] = amp_result
            if isinstance(amp_result, dict) and amp_result.get("ok") is False:
                result["ok"] = False

        proxy_change = any(
            value is not None
            for value in (
                privacy_proxy_worker_url,
                privacy_proxy_secret,
                privacy_proxy_enabled,
                tracking_obfuscation,
                high_noise_decoy_enabled,
            )
        )
        if proxy_change:
            try:
                proxy_status = privacy_proxy_store().configure(
                    worker_url=privacy_proxy_worker_url,
                    secret=privacy_proxy_secret,
                    enabled=privacy_proxy_enabled,
                    tracking_obfuscation=tracking_obfuscation,
                    high_noise_decoy_enabled=high_noise_decoy_enabled,
                )
            except Exception as exc:
                return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            result["privacy_proxy"] = proxy_status
        else:
            result["privacy_proxy"] = privacy_proxy_store().status()

        if privacy_proxy_test:
            result["privacy_proxy_test"] = privacy_proxy_client().test_connection()
            if not result["privacy_proxy_test"].get("ok"):
                result["ok"] = False
        if privacy_proxy_dismiss_offer:
            privacy_proxy_store().set_onboarding("privacy_proxy_offer", "dismissed")
        result["onboarding"] = onboarding_state(base)
        for key in ("secret_value", "secret_enc", "privacy_proxy_secret"):
            result.pop(key, None)
            if isinstance(result.get("privacy_proxy"), dict):
                result["privacy_proxy"].pop(key, None)
        return result

    core.mcp.remove_tool("set_amp_account_state")
    core.mcp.add_tool(set_amp_account_state, name="set_amp_account_state")
    base.set_amp_account_state = set_amp_account_state
    core.set_amp_account_state = set_amp_account_state

    _install_mcp_onboarding_instructions(base, core)

    if _SYNC_SERVICE is None:
        _SYNC_SERVICE = MailboxSyncService(
            mailbox_cache_synchronizer(),
            list_accounts=lambda: base.account_store().list_accounts(),
            client_factory=lambda account_id: base.mail_client(account_id),
            interval_seconds=float(os.getenv("MAILBOX_CACHE_SYNC_INTERVAL_SECONDS", "300")),
        )
        if os.getenv("MAILBOX_CACHE_SYNC_ENABLED", "true").strip().casefold() in {"1", "true", "yes", "on"}:
            _SYNC_SERVICE.start()
    base.mailbox_sync_service = lambda: _SYNC_SERVICE
    return build_status


__all__ = [
    "install_runtime_v963", "mailbox_cache_store", "mailbox_cache_synchronizer",
    "privacy_proxy_store", "privacy_proxy_client", "onboarding_state",
]
