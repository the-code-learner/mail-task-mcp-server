from __future__ import annotations

from urllib.parse import parse_qsl, unquote, urlencode, urlparse, urlunparse
from typing import Any

from .inbound_inspection_rules import (
    REDIRECT_PARAMETER_NAMES,
    is_tracking_parameter,
    tracker_host_hint,
    tracker_path_hint,
)


def _candidate_redirect(value: str) -> str:
    candidate = unquote(value or "").strip()
    parsed = urlparse(candidate)
    return candidate if parsed.scheme.casefold() in {"http", "https"} and parsed.netloc else ""


def inspect_url(url: str, *, visible_text: str = "") -> dict[str, Any]:
    raw = (url or "").strip()
    parsed = urlparse(raw)
    scheme = parsed.scheme.casefold()
    host = (parsed.hostname or "").casefold()
    query = parse_qsl(parsed.query, keep_blank_values=True)
    tracking = sorted({name for name, _ in query if is_tracking_parameter(name)})
    redirect_target = ""
    redirect_parameter = ""
    for name, value in query:
        if name.casefold() in REDIRECT_PARAMETER_NAMES:
            target = _candidate_redirect(value)
            if target:
                redirect_target = target
                redirect_parameter = name
                break

    clean_query = [(name, value) for name, value in query if not is_tracking_parameter(name)]
    canonical = urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            parsed.params,
            urlencode(clean_query, doseq=True),
            "",
        )
    ) if scheme in {"http", "https"} else raw
    if redirect_target:
        target_clean = inspect_url(redirect_target)
        canonical = str(target_clean.get("canonical_destination") or redirect_target)

    visible = (visible_text or "").strip()
    visible_host = ""
    if visible:
        probe = visible if "://" in visible else ("https://" + visible if "." in visible and " " not in visible else "")
        if probe:
            visible_host = (urlparse(probe).hostname or "").casefold()
    mismatch = bool(visible_host and host and visible_host != host and not visible_host.endswith("." + host) and not host.endswith("." + visible_host))

    return {
        "original_url": raw,
        "scheme": scheme,
        "host": host,
        "path": parsed.path or "",
        "query_parameter_count": len(query),
        "tracking_parameters": tracking,
        "has_tracking_parameters": bool(tracking),
        "redirector": bool(redirect_target),
        "redirect_parameter": redirect_parameter,
        "redirect_target": redirect_target,
        "tracker_hint": bool(tracker_host_hint(host) or tracker_path_hint(parsed.path or "")),
        "canonical_destination": canonical,
        "visible_text": visible,
        "visible_host": visible_host,
        "anchor_href_mismatch": mismatch,
    }
