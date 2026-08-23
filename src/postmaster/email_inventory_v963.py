from __future__ import annotations

import re
from typing import Any

from .email_privacy_v963 import inventory_html
from .inbound_inspection_urls import inspect_url, urls_from_text


_ACTION_RE = re.compile(r"(?:unsubscribe|opt[-_]?out|reset|magic|login|signin|verify|confirm|approve|accept|activate|token=|action=)", re.I)


def _plain_classification(url: str) -> tuple[str, int, list[str], list[str]]:
    inspected = inspect_url(url)
    haystack = url.casefold()
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
    if "unsubscribe" in haystack or "opt-out" in haystack or "optout" in haystack:
        kind = "unsubscribe"
    elif inspected.get("redirector"):
        kind = "redirector"
        score += 12
        reasons.append("redirector-shaped URL")
        observed.append("redirect target embedded in query")
    elif _ACTION_RE.search(url):
        kind = "action URL"
    elif params or inspected.get("tracker_hint"):
        kind = "analytics link"
    else:
        kind = "normal link"
    return kind, min(100, score), reasons, observed


def inventory_message(body_html: str, body_text: str) -> dict[str, Any]:
    """Complete static inventory across HTML attributes/CSS and the plain-text MIME alternative."""
    result = inventory_html(body_html or "")
    rows = [dict(row) for row in result.get("urls") or []]
    for url in urls_from_text(body_text or ""):
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


__all__ = ["inventory_message"]
