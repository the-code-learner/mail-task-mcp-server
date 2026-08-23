from __future__ import annotations

import json
import os
import re
import secrets
import signal
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any


_CONTROL_PATH_ENV = "POSTMASTER_RUNTIME_CONTROL_PATH"
_DEFAULT_CONTROL_PATH = "/opt/postmaster/.postmaster-runtime-control.json"
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_SEMVER_RE = re.compile(r"^v?([0-9]+)\.([0-9]+)\.([0-9]+)$")
_RELEASE_CACHE_TTL_SECONDS = 60.0
_RELEASE_LOCK = threading.RLock()
_RELEASE_CACHE: dict[str, Any] = {
    "versions": [],
    "last_attempt_monotonic": None,
    "status": "never",
}
_APPROVAL_TTL_SECONDS = 600.0
_APPROVAL_LOCK = threading.RLock()
_APPROVAL_CACHE: dict[str, dict[str, Any]] = {}


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
    parsed = semver_tuple(text)
    if parsed is None:
        raise ValueError("version selector must be latest or a stable vX.Y.Z release")
    return "v" + ".".join(str(part) for part in parsed)


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


def _fetch_stable_release_tags() -> list[str]:
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
        return parse_stable_release_tags(json.load(response))


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
    return result


def write_control(
    *,
    selector: str,
    restart_ref_once: str | None = None,
    check_updates_once: bool = False,
    path: Path | None = None,
) -> dict[str, Any]:
    target = path or control_path()
    payload: dict[str, Any] = {"selector": canonical_selector(selector)}
    if restart_ref_once:
        payload["restart_ref_once"] = canonical_selector(restart_ref_once)
    if check_updates_once:
        payload["check_updates_once"] = True
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp")
    temporary.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(target)
    return payload


def current_release_tag(version: str | None) -> str:
    parsed = semver_tuple(version)
    if parsed is None:
        raise ValueError("running version is not a stable SemVer release")
    return "v" + ".".join(str(part) for part in parsed)


def _purge_expired_approvals(now: float) -> None:
    expired = [
        token
        for token, payload in _APPROVAL_CACHE.items()
        if now - float(payload.get("issued_monotonic") or 0.0) > _APPROVAL_TTL_SECONDS
    ]
    for token in expired:
        _APPROVAL_CACHE.pop(token, None)


def issue_version_change_approval(
    *,
    operation: str,
    target: str,
    current_selector: str,
    current_build: str,
) -> str:
    """Issue a short-lived, process-local, one-operation approval nonce.

    Issuance is only a preview step. It never means the user approved the operation.
    The MCP assistant must ask for explicit approval in the active chat before sending
    this nonce back to a mutating call.
    """
    now = time.monotonic()
    token = secrets.token_urlsafe(24)
    with _APPROVAL_LOCK:
        _purge_expired_approvals(now)
        _APPROVAL_CACHE[token] = {
            "operation": str(operation),
            "target": str(target),
            "current_selector": str(current_selector),
            "current_build": str(current_build),
            "issued_monotonic": now,
        }
    return token


def consume_version_change_approval(
    token: str | None,
    *,
    operation: str,
    target: str,
    current_selector: str,
    current_build: str,
) -> bool:
    """Consume and validate an exact, single-use version-change approval nonce."""
    candidate = str(token or "").strip()
    if not candidate:
        return False
    now = time.monotonic()
    with _APPROVAL_LOCK:
        _purge_expired_approvals(now)
        payload = _APPROVAL_CACHE.pop(candidate, None)
    if not payload:
        return False
    return all(
        str(payload.get(key) or "") == str(value)
        for key, value in {
            "operation": operation,
            "target": target,
            "current_selector": current_selector,
            "current_build": current_build,
        }.items()
    )


def clear_version_change_approvals() -> None:
    """Clear process-local approval nonces. Intended for deterministic tests."""
    with _APPROVAL_LOCK:
        _APPROVAL_CACHE.clear()


def terminate_current_process() -> None:
    """Terminate the app process after the response; Docker restart policy owns restart."""
    os.kill(os.getpid(), signal.SIGTERM)


def schedule_current_process_termination(*, delay_seconds: float = 0.25) -> None:
    """Schedule process termination so the MCP response can be serialized first."""
    timer = threading.Timer(max(0.01, float(delay_seconds)), terminate_current_process)
    timer.daemon = True
    timer.start()
