from __future__ import annotations

from typing import Any

from . import mailbox_cache_v963 as mailbox_cache


_ORIGINAL_FLAGS_FROM_FETCH = mailbox_cache._flags_from_fetch
_ORIGINAL_SYNC_SERVICE = mailbox_cache.MailboxSyncService


def _normalized_flags_from_fetch(value: bytes) -> list[str]:
    """Normalize IMAP system flags to one leading backslash without changing custom flags."""
    flags = _ORIGINAL_FLAGS_FROM_FETCH(value)
    return ["\\" + flag.lstrip("\\") if flag.startswith("\\") else flag for flag in flags]


class MailboxSyncService(_ORIGINAL_SYNC_SERVICE):
    """Five-minute read-only cache synchronizer isolated from task execution infrastructure."""


def install_runtime_v963_release_contracts() -> None:
    """Install narrow v9.6.3 compatibility shims before runtime services are instantiated."""
    if getattr(mailbox_cache, "_v963_release_contracts_installed", False):
        return
    mailbox_cache._flags_from_fetch = _normalized_flags_from_fetch
    mailbox_cache.MailboxSyncService = MailboxSyncService
    mailbox_cache._v963_release_contracts_installed = True

    # runtime_v963 imported its class binding while this module was being composed; update that
    # binding too so the real background cache service uses the same compatibility class.
    from . import runtime_v963

    runtime_v963.MailboxSyncService = MailboxSyncService


__all__ = ["install_runtime_v963_release_contracts", "MailboxSyncService"]
