"""Safe Full HTML passive-resource failure taxonomy (issue #97).

The MCP/UI diagnostics must never include requested URLs, tokenized pixel paths,
worker response bodies, raw exception strings, or network host identifiers.
"""
from __future__ import annotations

from collections import Counter
from typing import Any
from urllib.parse import urlsplit

import httpx


class PrivacyFetchFailure(RuntimeError):
    """Sanitized failure from the authenticated Privacy Proxy HTTP boundary."""

    def __init__(self, category: str) -> None:
        self.category = category
        super().__init__(category)


class RemoteMessageLoadFailure(RuntimeError):
    """Message body could not be prepared; the proxy fetch stage was not entered."""


_WORKER_REASONS = {
    "blocked_target_host": "ssrf_denied",
    "dns_resolved_to_blocked_address": "ssrf_denied",
    "scheme_not_allowed": "ssrf_denied",
    "embedded_credentials_not_allowed": "ssrf_denied",
    "target_has_no_public_dns_answer": "origin_dns_failed",
    "dns_preflight_failed": "proxy_dns_preflight_failed",
    "navigation_or_action_url_not_proxyable": "policy_denied",
    "request_kind_not_allowed": "policy_denied",
    "decoy_requires_tracking_obfuscation": "policy_denied",
    "decoy_target_scope_violation": "policy_denied",
    "content_type_not_allowed": "content_type_rejected",
    "response_too_large": "resource_size_limit",
    "origin_fetch_failed": "origin_network_failed",
    "origin_timeout": "origin_timeout",
}

_ALLOWED_STATES = frozenset({
    "ssrf_denied", "origin_dns_failed", "proxy_dns_preflight_failed",
    "policy_denied", "content_type_rejected", "resource_size_limit",
    "proxy_auth_failed", "proxy_worker_failure", "proxy_policy_rejected",
    "proxy_invalid_request", "proxy_rate_limited", "proxy_http_error",
    "proxy_timeout", "proxy_transport_error", "proxy_error",
    "origin_network_failed", "origin_timeout",
    "origin_http_error", "origin_redirect", "origin_missing_status",
    "resource_size_limit", "resource_content_type_rejected",
    "nested_resource_size_limit", "nested_resource_content_type_rejected",
    "nested_cumulative_size_limit",
})


def worker_failure_category(status: int, error_code: Any = "") -> str:
    """Classify worker rejection using only strict allowlisted reason codes."""
    reason = str(error_code or "")
    if reason in _WORKER_REASONS:
        return _WORKER_REASONS[reason]
    if status in {401, 403}:
        return "proxy_auth_failed"
    if status == 429:
        return "proxy_rate_limited"
    if status >= 500:
        return "proxy_worker_failure"
    if status == 422:
        return "proxy_policy_rejected"
    if status == 400:
        return "proxy_invalid_request"
    return "proxy_http_error"


def safe_exception_state(exc: BaseException) -> str:
    if isinstance(exc, PrivacyFetchFailure):
        return exc.category
    if isinstance(exc, (httpx.TimeoutException, TimeoutError)):
        return "proxy_timeout"
    if isinstance(exc, (httpx.TransportError, ConnectionError)):
        return "proxy_transport_error"
    # A legacy negative tombstone may contain a type name. Unknown types are
    # stored as type-only, never raw exception text.
    return type(exc).__name__


def safe_worker_response_error(value: Any) -> str:
    return _WORKER_REASONS.get(str(value or ""), "proxy_error") if value else ""


def failure_category(row: dict[str, Any]) -> str:
    raw = str(row.get("error_state") or "")
    if raw in _ALLOWED_STATES:
        return raw
    if raw in {"TimeoutError", "ConnectTimeout", "ReadTimeout", "WriteTimeout", "PoolTimeout"}:
        return "proxy_timeout"
    if raw in {"ConnectError", "NetworkError", "RemoteProtocolError", "TransportError"}:
        return "proxy_transport_error"
    if raw:
        return "unknown_failure"
    try:
        status = int(row.get("http_status") or row.get("status") or 0)
    except (TypeError, ValueError):
        status = 0
    if status == 200 and row.get("body") is not None:
        return ""
    if 300 <= status < 400:
        return "origin_redirect"
    if status >= 400:
        return "origin_http_error"
    if status == 200:
        return "origin_empty_body"
    return "origin_missing_status"


def failure_details(row: dict[str, Any]) -> dict[str, Any]:
    category = failure_category(row)
    result: dict[str, Any] = {"category": category}
    try:
        status = int(row.get("http_status") or row.get("status") or 0)
    except (TypeError, ValueError):
        status = 0
    if 300 <= status <= 599:
        result["origin_http_status"] = status
    return result


def failure_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = Counter(failure_category(row) for row in rows)
    counts.pop("", None)
    return dict(sorted(counts.items()))


def resource_failure_summaries(
    candidates: list[dict[str, Any]], cached_rows: list[dict[str, Any] | None],
    *,
    already_cached: set[int] | None = None,
) -> list[dict[str, Any]]:
    """Index-only per-resource diagnostics, never URLs or tracking identifiers."""
    summaries = []
    for index, (candidate, row) in enumerate(zip(candidates, cached_rows)):
        info = failure_details(row) if row is not None else {"category": "not_cached"}
        if not info["category"]:
            continue
        item = {"index": index, **info}
        if already_cached is not None:
            item["negative_cache_hit"] = index in already_cached
        summaries.append(item)
    return summaries


def is_postmaster_tracking_pixel(url: str, public_base: str | None) -> bool:
    if not public_base:
        return False
    try:
        source = urlsplit(url)
        local = urlsplit(public_base)
        same_origin = (
            source.scheme.lower() == local.scheme.lower()
            and source.netloc.lower() == local.netloc.lower()
        )
        path_prefix = (local.path or "").rstrip("/")
        path = source.path
        return same_origin and (
            path.startswith(path_prefix + "/track/open/")
            and path.endswith(".gif")
            or path.startswith(path_prefix + "/t/i/")
        )
    except (ValueError, TypeError):
        return False
