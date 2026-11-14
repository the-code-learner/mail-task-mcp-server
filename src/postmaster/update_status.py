from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.request
from datetime import datetime, timezone
from typing import Any

from . import release_channels_v986 as channels


UPDATE_CACHE_TTL_SECONDS = 60.0
_REPO_RE = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
_SEMVER_RE = re.compile(r"^v?([0-9]+)\.([0-9]+)\.([0-9]+)$")
_UPDATE_LOCK = threading.RLock()
def _new_cache() -> dict[str,Any]:
    return {
        "latest_version": None,
        "last_attempt_monotonic": None,
        "update_checked_at": None,
        "update_last_attempt_at": None,
        "update_check_status": "never",
    }


_UPDATE_CACHE: dict[str, Any] = _new_cache()
_BETA_UPDATE_CACHE: dict[str, Any] = _new_cache()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _semver_tuple(value: str | None) -> tuple[int, int, int] | None:
    match = _SEMVER_RE.fullmatch(str(value or "").strip())
    if not match:
        return None
    return tuple(int(part) for part in match.groups())


def _fetch_latest_stable_version() -> str:
    repo = os.getenv("POSTMASTER_REPO", "the-code-learner/mail-task-mcp-server").strip()
    if not _REPO_RE.fullmatch(repo):
        raise RuntimeError("invalid repository identifier")
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/releases?per_page=100",
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "Postmaster-MCP-runtime-update-check",
        },
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        payload = json.load(response)
    candidates: list[tuple[tuple[int, int, int], str]] = []
    for release in payload if isinstance(payload, list) else []:
        if release.get("draft") or release.get("prerelease"):
            continue
        tag = str(release.get("tag_name") or "").strip()
        parsed = _semver_tuple(tag)
        if parsed is not None:
            candidates.append((parsed, tag.removeprefix("v")))
    if not candidates:
        raise RuntimeError("no stable SemVer release found")
    return max(candidates, key=lambda item: item[0])[1]


def _fetch_latest_beta_version() -> str:
    from . import runtime_control
    versions,status=runtime_control.available_release_tags(channel=channels.STABLE_AND_BETA)
    if status!="ok" or not versions:
        raise RuntimeError("eligible stable and beta release discovery unavailable")
    return versions[0].removeprefix("v")


def reset_update_cache() -> None:
    """Reset the process-local lazy cache. Primarily useful for deterministic tests."""
    with _UPDATE_LOCK:
        _UPDATE_CACHE.update(_new_cache())
        _BETA_UPDATE_CACHE.update(_new_cache())


def latest_version_status(current_version: str, *, channel: str | None = None) -> dict[str, Any]:
    """Return the latest release eligible for the user's saved update channel.

    A failed refresh never discards the last known good latest version. Unknown latest
    state is represented with update_available=None rather than a false negative.
    """
    from . import runtime_control
    selected=channels.valid_channel(channel or runtime_control.preferred_release_channel())
    cache=_UPDATE_CACHE if selected==channels.STABLE else _BETA_UPDATE_CACHE
    fetch=(
        _fetch_latest_stable_version
        if selected==channels.STABLE else _fetch_latest_beta_version
    )
    now_monotonic = time.monotonic()
    with _UPDATE_LOCK:
        last_attempt = cache.get("last_attempt_monotonic")
        cache_fresh = (
            last_attempt is not None
            and now_monotonic - float(last_attempt) < UPDATE_CACHE_TTL_SECONDS
        )
        if not cache_fresh:
            attempt_at = _utc_now()
            cache["last_attempt_monotonic"] = now_monotonic
            cache["update_last_attempt_at"] = attempt_at
            try:
                latest = fetch()
                cache["latest_version"] = latest
                cache["update_checked_at"] = attempt_at
                cache["update_check_status"] = "ok"
            except Exception:
                cache["update_check_status"] = "error"

        latest = cache.get("latest_version")
        update_available=channels.newer_than(str(latest) if latest else None,current_version)

        return {
            "latest_version": str(latest) if latest else None,
            "update_available": update_available,
            "update_checked_at": cache.get("update_checked_at"),
            "update_last_attempt_at": cache.get("update_last_attempt_at"),
            "update_check_status": str(cache.get("update_check_status") or "never"),
            "release_channel": selected,
            "update_cache_ttl_seconds": int(UPDATE_CACHE_TTL_SECONDS),
        }
