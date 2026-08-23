from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qsl, unquote, urlparse

from .email_privacy_v963 import inventory_html
from .inbound_inspection_html import urls_from_text
from .inbound_inspection_urls import inspect_url


_UNSUBSCRIBE_PATH_RE = re.compile(
    r"(?:^|[/_.-])(?:unsubscribe|opt[-_]?out|list[-_]?unsubscribe)(?:$|[/_.-])",
    re.I,
)
_ACTION_PATH_RE = re.compile(
    r"(?:^|[/_.-])(?:action|reset|magic(?:[-_]?link)?|login|signin|verify|verification|confirm|confirmation|approve|accept|activate)(?:$|[/_.-])",
    re.I,
)
_ACTION_VALUE_RE = re.compile(
    r"(?:unsubscribe|opt[-_]?out|reset|magic|login|signin|verify|confirm|approve|accept|activate)",
    re.I,
)
_ACTION_TOKEN_KEY_RE = re.compile(
    r"^(?:reset|magic|login|verify|verification|confirm|confirmation|approve|accept|activate)[_-]?token$",
    re.I,
)


def semantic_navigation_kind(url: str) -> str | None:
    """Conservatively identify unsubscribe/action semantics independently of HTML source type."""
    try:
        parsed = urlparse(url)
    except Exception:
        return None
    path = unquote(parsed.path or "")
    if _UNSUBSCRIBE_PATH_RE.search(path):
        return "unsubscribe"
    if _ACTION_PATH_RE.search(path):
        return "action URL"
    try:
        pairs = parse_qsl(parsed.query, keep_blank_values=True)
    except Exception:
        pairs = []
    for raw_key, raw_value in pairs:
        key = raw_key.casefold()
        value = raw_value.casefold()
        if key in {"unsubscribe", "optout", "opt_out", "opt-out", "one_click", "one-click", "list_unsubscribe", "list-unsubscribe"}:
            return "unsubscribe"
        if key == "action" and _ACTION_VALUE_RE.search(value):
            return "unsubscribe" if re.search(r"unsubscribe|opt[-_]?out", value, re.I) else "action URL"
        if key in {"one_time_token", "one-time-token", "magic_token", "magic-token", "otp"} or _ACTION_TOKEN_KEY_RE.match(key):
            return "action URL"
    return None


def _plain_classification(url: str) -> tuple[str, int, list[str], list[str]]:
    inspected = inspect_url(url)
    reasons: list[str] = []
    observed: list[str] = []
    score = 0
    params = list(inspected.get("tracking_parameters") or [])
    if inspected.get("tracker_hint"):
        score += 30
        reasons.append("tracker host/path hint")
        observed.append("tracker-like host/path")
    if params:
        score += min(25, 8 + 4 * len(params))
        reasons.append("tracking query parameters")
        observed.append("query parameters: " + ", ".join(params[:8]))
    semantic = semantic_navigation_kind(url)
    if semantic:
        kind = semantic
        reasons.append("action/navigation URL semantics")
        observed.append("action-like path or query marker")
    elif inspected.get("redirector"):
        kind = "redirector"
        score += 12
        reasons.append("redirector-shaped URL")
        observed.append("redirect target embedded in query")
    elif params or inspected.get("tracker_hint"):
        kind = "analytics link"
    else:
        kind = "normal link"
    return kind, min(100, score), reasons, observed


def inventory_message(body_html: str, body_text: str) -> dict[str, Any]:
    """Complete static inventory across HTML attributes/CSS and the plain-text MIME alternative."""
    result = inventory_html(body_html or "")
    rows = [dict(row) for row in result.get("urls") or []]
    for row in rows:
        semantic = semantic_navigation_kind(str(row.get("url") or ""))
        if not semantic:
            continue
        row["classification"] = semantic
        row["passive_resource"] = False
        reasons = list(row.get("tracking_reasons") or [])
        observed = list(row.get("observed_evidence") or [])
        if "action/navigation URL semantics" not in reasons:
            reasons.append("action/navigation URL semantics")
        if "action-like path or query marker" not in observed:
            observed.append("action-like path or query marker")
        row["tracking_reasons"] = reasons
        row["observed_evidence"] = observed

    for text_record in urls_from_text(body_text or ""):
        url = str(text_record.get("original_url") or "").strip()
        if not url:
            continue
        inspected = inspect_url(url)
        kind, score, reasons, observed = _plain_classification(url)
        rows.append(
            {
                "index": len(rows),
                "url": url,
                "domain": str(inspected.get("host") or ""),
                "scheme": str(inspected.get("scheme") or ""),
                "source_type": "plain text",
                "source_snippet": url[:1500],
                "anchor_text": "",
                "width": "",
                "height": "",
                "hidden": False,
                "classification": kind,
                "tracking_score": score,
                "tracking_reasons": reasons,
                "observed_evidence": observed,
                "inference": "heuristic classification; not a certainty",
                "passive_resource": False,
                "network_contacted": False,
                "redirector_hint": bool(inspected.get("redirector")),
                "redirect_target_hint": str(inspected.get("redirect_target") or ""),
            }
        )
    remote = [row for row in rows if row.get("scheme") in {"http", "https"}]
    domains = sorted({str(row.get("domain") or "") for row in remote if row.get("domain")})
    remote_images = [row for row in remote if row.get("classification") in {"remote image", "tracking pixel"}]
    pixels = [row for row in remote if row.get("classification") == "tracking pixel"]
    score = max([int(row.get("tracking_score") or 0) for row in rows] or [0])
    result.update(
        {
            "urls": rows,
            "url_count": len(rows),
            "external_url_count": len(remote),
            "external_domains": domains,
            "remote_image_count": len(remote_images),
            "tracking_pixel_count": len(pixels),
            "tracking_score": score,
            "tracking_verdict": "Tracking probabile" if score >= 65 else "Tracking possibile" if score >= 25 else "Nessun tracking evidente",
            "warning": {
                "remote_images": len(remote_images),
                "possible_tracking_pixels": len(pixels),
                "external_domains": len(domains),
            },
        }
    )
    return result


__all__ = ["inventory_message", "semantic_navigation_kind"]
