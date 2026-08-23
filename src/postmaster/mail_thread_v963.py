from __future__ import annotations

import re
from email import policy
from email.message import Message
from email.parser import BytesParser
from email.utils import getaddresses
from typing import Any, Iterable

from .thread_recipients import extend_references, normalize_reply_subject, sender_identity_addresses


_FORWARD_PREFIX_RE = re.compile(r"^(?:\s*(?:fwd?|fw)\s*:\s*)+", re.IGNORECASE)


def _addresses(message: Message, header: str) -> list[str]:
    values = message.get_all(header, []) or []
    return [address.strip() for _, address in getaddresses([str(value) for value in values]) if address.strip() and "@" in address]


def _dedupe(values: Iterable[str], excluded: set[str], seen: set[str] | None = None) -> list[str]:
    used = seen if seen is not None else set()
    result: list[str] = []
    for value in values:
        address = str(value or "").strip()
        key = address.casefold()
        if not address or "@" not in address or key in excluded or key in used:
            continue
        used.add(key)
        result.append(address)
    return result


def reply_all_plan(message: Message, settings: Any, *, require_reply_target: bool = False) -> dict[str, Any]:
    """Plan a Reply-all draft without reading or exposing Bcc.

    Set ``require_reply_target=True`` when validating an actual reply operation. The default
    permits the same metadata helper to support Forward UI for outbound messages where From is
    the current sender account and no external reply target should exist.
    """
    identities = sender_identity_addresses(settings)
    excluded = {value.casefold() for value in identities}
    from_values = _addresses(message, "From")
    reply_to = _addresses(message, "Reply-To")
    preferred = reply_to if reply_to else from_values
    seen: set[str] = set()
    to = _dedupe(preferred, excluded, seen)
    if require_reply_target and not to:
        raise ValueError("No external Reply-To/From recipient remains after sender filtering")

    # Standard Reply-all UX: keep the sender/Reply-To in To, preserve all visible original
    # To/Cc participants as Cc after removing the current account, aliases and duplicates.
    # Bcc is intentionally never read here.
    visible_original = _addresses(message, "To") + _addresses(message, "Cc")
    cc = _dedupe(visible_original, excluded, seen)
    message_id = str(message.get("Message-ID", "") or "").strip()
    return {
        "to": to,
        "cc": cc,
        "subject": normalize_reply_subject(str(message.get("Subject", "") or "")),
        "in_reply_to": message_id,
        "references": extend_references(str(message.get("References", "") or ""), message_id),
        "sender_identities": list(identities),
        "bcc_accessed": False,
    }


def forward_subject(subject: str) -> str:
    base = _FORWARD_PREFIX_RE.sub("", str(subject or "").strip()).strip()
    return f"Fwd: {base}" if base else "Fwd:"


def forward_body(message: Message, body_text: str) -> str:
    rows = ["", "---------- Forwarded message ----------"]
    for label, header in (("From", "From"), ("Date", "Date"), ("Subject", "Subject"), ("To", "To"), ("Cc", "Cc")):
        value = str(message.get(header, "") or "").strip()
        if value:
            rows.append(f"{label}: {value}")
    rows.append("")
    rows.append(str(body_text or ""))
    return "\n".join(rows).strip()


def parse_message(raw: bytes) -> Message:
    return BytesParser(policy=policy.default).parsebytes(raw)


__all__ = ["reply_all_plan", "forward_subject", "forward_body", "parse_message"]
