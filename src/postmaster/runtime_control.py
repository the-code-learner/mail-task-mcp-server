from __future__ import annotations

import json
import os
import re
import signal
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any

from . import release_channels_v986 as channels
from .confirmation_v967 import (
    CONFIRMATION_TTL_SECONDS,
    PersistentConfirmationTokens,
    resolve_confirmation_paths,
)


_CONTROL_PATH_ENV = "POSTMASTER_RUNTIME_CONTROL_PATH"
_DEFAULT_CONTROL_PATH = "/opt/postmaster/.postmaster-runtime-control.json"
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_SEMVER_RE = re.compile(r"^v?([0-9]+)\.([0-9]+)\.([0-9]+)$")
_RELEASE_CACHE_TTL_SECONDS = 60.0
_RELEASE_LOCK = threading.RLock()
_RELEASE_CACHE: dict[str, Any] = {
    "versions": [],
    "beta_versions": [],
    "beta_last_attempt_monotonic": None,
    "beta_status": "never",
    "last_attempt_monotonic": None,
    "status": "never",
}
_APPROVAL_TTL_SECONDS = CONFIRMATION_TTL_SECONDS
_APPROVAL_SERVICE_LOCK = threading.RLock()
_APPROVAL_SERVICE: PersistentConfirmationTokens | None = None


def control_path() -> Path:
    return Path(os.getenv(_CONTROL_PATH_ENV, _DEFAULT_CONTROL_PATH))


def semver_tuple(value: str | None) -> tuple[int, int, int] | None:
    match = _SEMVER_RE.fullmatch(str(value or "").strip())
    if not match:
        return None
    return tuple(int(part) for part in match.groups())


def canonical_selector(value: str | None) -> str:
    text = str(value or "").strip()
    if text == "latest":
        return text
    return channels.canonical_tag(text)


def parse_stable_release_tags(payload: Any) -> list[str]:
    candidates: dict[tuple[int, int, int], str] = {}
    for release in payload if isinstance(payload, list) else []:
        if not isinstance(release, dict) or release.get("draft") or release.get("prerelease"):
            continue
        tag = str(release.get("tag_name") or "").strip()
        parsed = semver_tuple(tag)
        if parsed is not None:
            candidates[parsed] = "v" + ".".join(str(part) for part in parsed)
    return [candidates[key] for key in sorted(candidates, reverse=True)]


def _fetch_release_catalog() -> Any:
    repo = os.getenv("POSTMASTER_REPO", "the-code-learner/mail-task-mcp-server").strip()
    if not _REPO_RE.fullmatch(repo):
        raise RuntimeError("invalid repository identifier")
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/releases?per_page=100",
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "Postmaster-MCP-runtime-control",
        },
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response)


def _fetch_stable_release_tags() -> list[str]:
    return parse_stable_release_tags(_fetch_release_catalog())


def _fetch_beta_release_tags() -> list[str]:
    return channels.release_tags(_fetch_release_catalog(),channel=channels.STABLE_AND_BETA)


def available_release_tags(*,channel: str=channels.STABLE, force: bool=False) -> tuple[list[str],str]:
    selected=channels.valid_channel(channel)
    if selected==channels.STABLE:
        return stable_release_tags(force=force)
    now=time.monotonic()
    with _RELEASE_LOCK:
        last=_RELEASE_CACHE.get("beta_last_attempt_monotonic")
        fresh=last is not None and now-float(last)<_RELEASE_CACHE_TTL_SECONDS
        if force or not fresh:
            _RELEASE_CACHE["beta_last_attempt_monotonic"]=now
            try:
                versions=_fetch_beta_release_tags()
                if not versions:
                    raise RuntimeError("no eligible stable or beta releases found")
                _RELEASE_CACHE["beta_versions"]=versions
                _RELEASE_CACHE["beta_status"]="ok"
            except Exception:
                _RELEASE_CACHE["beta_status"]="error"
        return (
            list(_RELEASE_CACHE.get("beta_versions") or []),
            str(_RELEASE_CACHE.get("beta_status") or "never"),
        )


def stable_release_tags(*, force: bool = False) -> tuple[list[str], str]:
    """Return stable application SemVer releases with a small last-known-good cache."""
    now = time.monotonic()
    with _RELEASE_LOCK:
        last = _RELEASE_CACHE.get("last_attempt_monotonic")
        fresh = last is not None and now - float(last) < _RELEASE_CACHE_TTL_SECONDS
        if force or not fresh:
            _RELEASE_CACHE["last_attempt_monotonic"] = now
            try:
                versions = _fetch_stable_release_tags()
                if not versions:
                    raise RuntimeError("no stable SemVer application releases found")
                _RELEASE_CACHE["versions"] = versions
                _RELEASE_CACHE["status"] = "ok"
            except Exception:
                _RELEASE_CACHE["status"] = "error"
        return list(_RELEASE_CACHE.get("versions") or []), str(_RELEASE_CACHE.get("status") or "never")


def read_control(path: Path | None = None) -> dict[str, Any]:
    target = path or control_path()
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    result: dict[str, Any] = {}
    try:
        if raw.get("selector"):
            result["selector"] = canonical_selector(str(raw["selector"]))
    except ValueError:
        pass
    try:
        if raw.get("restart_ref_once"):
            result["restart_ref_once"] = canonical_selector(str(raw["restart_ref_once"]))
    except ValueError:
        pass
    if raw.get("check_updates_once") is True:
        result["check_updates_once"] = True
    try:
        if "release_channel" in raw:
            result["release_channel"] = channels.valid_channel(str(raw["release_channel"]))
    except ValueError:
        pass
    return result


def preferred_release_channel(path: Path | None = None) -> str:
    return str(read_control(path).get("release_channel") or channels.STABLE)


def write_control(
    *,
    selector: str,
    restart_ref_once: str | None = None,
    check_updates_once: bool = False,
    path: Path | None = None,
    release_channel: str | None = None,
) -> dict[str, Any]:
    target = path or control_path()
    previous=read_control(target)
    selected_channel=(
        channels.valid_channel(release_channel)
        if release_channel is not None else previous.get("release_channel")
    )
    payload: dict[str, Any] = {"selector": canonical_selector(selector)}
    if selected_channel:
        payload["release_channel"]=selected_channel
    if restart_ref_once:
        payload["restart_ref_once"] = canonical_selector(restart_ref_once)
    if check_updates_once:
        payload["check_updates_once"] = True
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp")
    temporary.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(target)
    return payload


def save_release_channel(channel: str, *,path: Path | None=None) -> dict[str,Any]:
    """Persist an update visibility preference without restarting or changing source."""
    selected=channels.valid_channel(channel)
    previous=read_control(path)
    # Do not silently retain a prerelease pin that would conflict with opting
    # out of beta: require an explicit version switch first.
    pinned=str(previous.get("selector") or "latest")
    if selected==channels.STABLE and not channels.release_is_allowed(pinned,selected) and pinned!="latest":
        raise ValueError("switch to a stable version before disabling beta updates")
    return write_control(
        selector=pinned,
        restart_ref_once=previous.get("restart_ref_once"),
        check_updates_once=bool(previous.get("check_updates_once")),
        path=path,
        release_channel=selected,
    )


def current_release_tag(version: str | None) -> str:
    return channels.canonical_tag(version)


def _running_version() -> str:
    try:
        return (Path(__file__).resolve().parents[2] / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        return "unknown"


def initialize_version_change_approvals(
    *,
    key_path: str | Path | None = None,
    db_path: str | Path | None = None,
    replace: bool = False,
) -> PersistentConfirmationTokens:
    """Initialize the persistent approval backend outside any MCP preview invocation."""
    global _APPROVAL_SERVICE
    with _APPROVAL_SERVICE_LOCK:
        if (
            _APPROVAL_SERVICE is not None
            and not replace
            and key_path is None
            and db_path is None
        ):
            return _APPROVAL_SERVICE

        resolved_key_path, resolved_db_path = resolve_confirmation_paths(
            key_path=key_path,
            db_path=db_path,
        )
        paths_changed = (
            _APPROVAL_SERVICE is not None
            and (
                _APPROVAL_SERVICE.key_path != resolved_key_path
                or _APPROVAL_SERVICE.db_path != resolved_db_path
            )
        )
        if _APPROVAL_SERVICE is None or replace or paths_changed:
            _APPROVAL_SERVICE = PersistentConfirmationTokens(
                scope="runtime_version_change",
                ttl_seconds=_APPROVAL_TTL_SECONDS,
                key_path=resolved_key_path,
                db_path=resolved_db_path,
            )
        return _APPROVAL_SERVICE


def version_change_confirmation_ttl_seconds() -> int:
    return _APPROVAL_TTL_SECONDS


def _version_binding(
    *,
    operation: str,
    target: str,
    current_selector: str,
    current_build: str,
) -> dict[str, str]:
    return {
        "operation": str(operation),
        "target": str(target),
        "current_selector": str(current_selector),
        "current_build": str(current_build),
        "current_version": _running_version(),
    }


def issue_version_change_approval(
    *,
    operation: str,
    target: str,
    current_selector: str,
    current_build: str,
) -> str:
    """Issue a stateless authenticated, exact-state version-change confirmation token.

    Issuance is a preview step and performs no persistent write after application startup.
    It never means the user approved the operation. The MCP assistant must show the preview
    and obtain explicit approval in the active chat before execute sends this token back.
    """
    service = initialize_version_change_approvals()
    return service.issue(
        _version_binding(
            operation=operation,
            target=target,
            current_selector=current_selector,
            current_build=current_build,
        )
    )


def consume_version_change_approval(
    token: str | None,
    *,
    operation: str,
    target: str,
    current_selector: str,
    current_build: str,
) -> bool:
    """Consume an exact, persistent one-time version-change confirmation token."""
    service = initialize_version_change_approvals()
    return service.consume(
        token,
        _version_binding(
            operation=operation,
            target=target,
            current_selector=current_selector,
            current_build=current_build,
        ),
    )


def clear_version_change_approvals() -> None:
    """Clear persisted consumed nonces for deterministic tests."""
    initialize_version_change_approvals().clear_consumed()


def terminate_current_process() -> None:
    """Terminate the app process after the response; Docker restart policy owns restart."""
    os.kill(os.getpid(), signal.SIGTERM)


def schedule_current_process_termination(*, delay_seconds: float = 0.25) -> None:
    """Schedule process termination so the MCP response can be serialized first."""
    timer = threading.Timer(max(0.01, float(delay_seconds)), terminate_current_process)
    timer.daemon = True
    timer.start()
