from __future__ import annotations

from email.message import Message
from typing import Any

from .inbound_inspection_html import inspect_html, sanitize_html, urls_from_text
from .inbound_inspection_rules import unique_strings


def _mime_summary(msg: Message) -> dict[str, Any]:
    content_types: list[str] = []
    dispositions: list[str] = []
    content_ids: list[str] = []
    attachment_names: list[str] = []
    part_count = 0
    for part in msg.walk():
        part_count += 1
        content_types.append(str(part.get_content_type() or ""))
        disposition = str(part.get_content_disposition() or "")
        if disposition:
            dispositions.append(disposition)
        cid = str(part.get("Content-ID") or "").strip()
        if cid:
            content_ids.append(cid)
        filename = part.get_filename()
        if filename:
            attachment_names.append(str(filename))
    return {
        "part_count": part_count,
        "content_types": unique_strings(content_types),
        "dispositions": unique_strings(dispositions),
        "content_ids": unique_strings(content_ids),
        "attachment_names": unique_strings(attachment_names),
        "multipart": bool(msg.is_multipart()),
    }


def _header_summary(msg: Message) -> dict[str, Any]:
    received = msg.get_all("Received", []) or []
    authentication = msg.get_all("Authentication-Results", []) or []
    return {
        "received_hops": len(received),
        "authentication_results": [str(x)[:2000] for x in authentication],
        "return_path": str(msg.get("Return-Path") or ""),
        "reply_to": str(msg.get("Reply-To") or ""),
        "list_unsubscribe": str(msg.get("List-Unsubscribe") or ""),
        "list_unsubscribe_post": str(msg.get("List-Unsubscribe-Post") or ""),
        "list_id": str(msg.get("List-ID") or ""),
        "precedence": str(msg.get("Precedence") or ""),
        "auto_submitted": str(msg.get("Auto-Submitted") or ""),
        "x_mailer": str(msg.get("X-Mailer") or ""),
        "content_type": str(msg.get("Content-Type") or ""),
    }


def _risk_flags(html_findings: dict[str, Any], links: list[dict[str, Any]]) -> list[str]:
    flags: list[str] = []
    if html_findings.get("remote_image_count"):
        flags.append("remote_images")
    if html_findings.get("tracking_pixel_count"):
        flags.append("tracking_pixels")
    if html_findings.get("remote_css"):
        flags.append("remote_css")
    if html_findings.get("remote_background_images"):
        flags.append("remote_background_images")
    if html_findings.get("embedded_resources"):
        flags.append("embedded_cid_or_data_resources")
    if any(row.get("tracker_hint") for row in links):
        flags.append("tracker_or_redirector_hints")
    if any(row.get("redirector") for row in links):
        flags.append("redirectors")
    if any(row.get("has_tracking_parameters") for row in links):
        flags.append("tracking_parameters")
    if any(row.get("anchor_href_mismatch") for row in links):
        flags.append("anchor_href_mismatch")
    return unique_strings(flags)


def inspect_message(
    msg: Message,
    *,
    body_html: str = "",
    body_text: str = "",
    mode: str = "full",
) -> dict[str, Any]:
    """Inspect only already-received content; never resolve or request message URLs."""
    selected = (mode or "full").strip().casefold()
    if selected not in {"summary", "full"}:
        raise ValueError("inspection mode must be 'summary' or 'full'")
    html_findings = inspect_html(body_html or "")
    text_links = urls_from_text(body_text or "")
    combined_links: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for row in list(html_findings.get("links") or []) + text_links:
        key = (str(row.get("original_url") or ""), str(row.get("visible_text") or ""))
        if key in seen:
            continue
        seen.add(key)
        combined_links.append(row)

    domains = unique_strings(str(row.get("host") or "") for row in combined_links if row.get("host"))
    flags = _risk_flags(html_findings, combined_links)
    result: dict[str, Any] = {
        "mode": selected,
        "static_only": True,
        "network_requests_performed": 0,
        "risk_flags": flags,
        "risk_flag_count": len(flags),
        "external_url_count": len(html_findings.get("external_urls") or []),
        "link_count": len(combined_links),
        "external_domains": domains,
        "remote_image_count": int(html_findings.get("remote_image_count") or 0),
        "tracking_pixel_count": int(html_findings.get("tracking_pixel_count") or 0),
        "remote_css_count": len(html_findings.get("remote_css") or []),
        "remote_background_image_count": len(html_findings.get("remote_background_images") or []),
        "embedded_resource_count": len(html_findings.get("embedded_resources") or []),
        "headers": _header_summary(msg),
        "mime": _mime_summary(msg),
        "sanitized_html": sanitize_html(body_html or ""),
    }
    if selected == "full":
        result.update({"links": combined_links, "html": html_findings})
    return result
