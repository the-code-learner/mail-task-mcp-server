from __future__ import annotations

import re
from email import policy
from email.message import Message
from email.parser import BytesParser
from email.utils import getaddresses, parseaddr
from typing import Any, Iterable, Literal

from .mail_bridge import MailBridgeError


ThreadMode = Literal["reply", "follow_up"]
_REPLY_PREFIX_RE = re.compile(r"^(?:\s*re\s*:\s*)+", re.IGNORECASE)


def _valid_address(value: str) -> str:
    _, address = parseaddr(value or "")
    address = address.strip()
    if not address or "@" not in address:
        return ""
    return address


def _header_addresses(message: Message, header: str) -> list[str]:
    values = message.get_all(header, []) or []
    result: list[str] = []
    for _, address in getaddresses([str(value) for value in values]):
        address = address.strip()
        if address and "@" in address:
            result.append(address)
    return result


def _iter_alias_values(value: Any) -> Iterable[str]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (item.strip() for item in re.split(r"[,;\n]", value) if item.strip())
    try:
        return (str(item).strip() for item in value if str(item).strip())
    except TypeError:
        return (str(value).strip(),)


def sender_identity_addresses(settings: Any) -> tuple[str, ...]:
    """Return the primary sender plus any account identities/aliases known by Settings."""
    candidates: list[str] = [str(getattr(settings, "email_address", "") or "")]

    # Alias attributes are intentionally optional so this resolver remains compatible with
    # current account rows while also honoring deployments/extensions that already attach them.
    for attr in ("sender_aliases", "email_aliases", "aliases"):
        candidates.extend(_iter_alias_values(getattr(settings, attr, None)))

    # IMAP/SMTP usernames are account-configured identities too when they are email addresses.
    # Including them is conservative for self-reply prevention and is a no-op for host/user IDs.
    candidates.extend(
        [
            str(getattr(settings, "smtp_username", "") or ""),
            str(getattr(settings, "imap_username", "") or ""),
        ]
    )

    out: list[str] = []
    seen: set[str] = set()
    for value in candidates:
        address = _valid_address(value)
        key = address.casefold()
        if address and key not in seen:
            seen.add(key)
            out.append(address)
    return tuple(out)


def normalize_reply_subject(subject: str) -> str:
    """Collapse repeated leading Re: prefixes to one canonical Re:."""
    original = (subject or "").strip()
    base = _REPLY_PREFIX_RE.sub("", original).strip()
    return f"Re: {base}" if base else "Re:"


def extend_references(references: str, message_id: str) -> str:
    """Preserve the existing chain and append the selected message exactly once."""
    existing = (references or "").strip()
    selected = (message_id or "").strip()
    if not selected:
        return existing
    tokens = existing.split()
    if selected not in tokens:
        tokens.append(selected)
    return " ".join(tokens)


def _dedupe_external(
    addresses: Iterable[str],
    *,
    excluded: set[str],
    seen: set[str] | None = None,
    strict: bool = False,
) -> list[str]:
    out: list[str] = []
    seen_keys = seen if seen is not None else set()
    for value in addresses:
        address = _valid_address(str(value))
        if not address:
            if strict:
                raise MailBridgeError(f"Invalid recipient address: {value}")
            continue
        key = address.casefold()
        if key in excluded or key in seen_keys:
            continue
        seen_keys.add(key)
        out.append(address)
    return out


def merge_thread_cc(
    to: Iterable[str],
    base_cc: Iterable[str],
    extra_cc: Iterable[str] | None,
    *,
    sender_identities: Iterable[str],
) -> list[str]:
    """Merge original/default Cc with caller Cc without self-addresses or duplicates."""
    excluded = {address.casefold() for address in sender_identities}
    seen = {address.casefold() for address in to}
    merged = _dedupe_external(base_cc, excluded=excluded, seen=seen)
    if extra_cc:
        merged.extend(_dedupe_external(extra_cc, excluded=excluded, seen=seen, strict=True))
    return merged


def resolve_thread_recipients(
    message: Message,
    *,
    mode: ThreadMode,
    sender_identities: Iterable[str],
) -> dict[str, Any]:
    """Resolve safe To/Cc and thread headers for inbound reply or outbound follow-up."""
    if mode not in {"reply", "follow_up"}:
        raise MailBridgeError(f"Unsupported thread recipient mode: {mode}")

    identities = tuple(sender_identities)
    excluded = {address.casefold() for address in identities}
    from_addresses = _header_addresses(message, "From")
    outbound = any(address.casefold() in excluded for address in from_addresses)

    if mode == "reply" and outbound:
        raise MailBridgeError(
            "Selected message is outbound from this sender account; use follow_up_email instead."
        )
    if mode == "follow_up" and not outbound:
        raise MailBridgeError(
            "Selected message is inbound to this sender account; use reply_email instead of follow_up_email."
        )

    seen: set[str] = set()
    if mode == "reply":
        reply_to = _header_addresses(message, "Reply-To")
        preferred = reply_to if reply_to else from_addresses
        to = _dedupe_external(preferred, excluded=excluded, seen=seen)
        cc: list[str] = []
        if not to:
            source = "Reply-To/From" if reply_to else "From"
            raise MailBridgeError(
                f"Original inbound email has no external {source} recipient after sender filtering."
            )
    else:
        # Never read or infer Bcc here. Only visible original To/Cc participate.
        to = _dedupe_external(_header_addresses(message, "To"), excluded=excluded, seen=seen)
        cc = _dedupe_external(_header_addresses(message, "Cc"), excluded=excluded, seen=seen)
        if not to and not cc:
            raise MailBridgeError(
                "No external recipients remain after removing the sender account and aliases; "
                "follow-up was not sent."
            )
        if not to:
            raise MailBridgeError(
                "No external To recipient remains after removing the sender account and aliases; "
                "follow-up was not sent."
            )

    message_id = str(message.get("Message-ID", "") or "").strip()
    references = extend_references(str(message.get("References", "") or ""), message_id)
    return {
        "mode": mode,
        "direction": "outbound" if outbound else "inbound",
        "to": to,
        "cc": cc,
        "subject": normalize_reply_subject(str(message.get("Subject", "") or "")),
        "message_id": message_id,
        "references": references,
        "sender_identities": list(identities),
    }


class ThreadRecipientsMixin:
    """Explicit inbound-reply / outbound-follow-up semantics on the existing mail pipeline."""

    def _thread_source_message(self, mailbox: str, uid: str) -> Message:
        # Headers are sufficient for recipient resolution/threading, avoiding a full body fetch.
        with self._imap() as conn:
            self._select(conn, mailbox, readonly=True)
            raw = self._fetch_headers(conn, uid)
        return BytesParser(policy=policy.default).parsebytes(raw)

    def resolve_thread_recipients(self, mailbox: str, uid: str, *, mode: ThreadMode) -> dict[str, Any]:
        return resolve_thread_recipients(
            self._thread_source_message(mailbox, uid),
            mode=mode,
            sender_identities=sender_identity_addresses(self.settings),
        )

    def _send_threaded(
        self,
        *,
        mode: ThreadMode,
        mailbox: str,
        uid: str,
        body: str = "",
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
        body_html: str | None = None,
        attachments: list[dict[str, Any]] | None = None,
        track_opens: bool | None = None,
        campaign_id: str | None = None,
    ) -> dict[str, Any]:
        resolved = self.resolve_thread_recipients(mailbox, uid, mode=mode)
        identities = sender_identity_addresses(self.settings)
        cc_clean = merge_thread_cc(
            resolved["to"], resolved["cc"], cc, sender_identities=identities
        )
        track = self._resolve_track_opens(track_opens)

        if not track:
            msg, recipients, meta = self._build_message(
                to=resolved["to"],
                subject=resolved["subject"],
                body=body,
                cc=cc_clean,
                bcc=bcc,
                body_html=body_html,
                attachments=attachments,
                allow_unlisted=False,
                in_reply_to=resolved["message_id"],
                references=resolved["references"],
            )
            result = self._send_message(msg, recipients)
            result.update(
                {
                    "html": True,
                    "amp": False,
                    "tracked": False,
                    "individualized": False,
                    "visible_recipient_headers_preserved": True,
                    "attachments": meta,
                }
            )
        else:
            # Dynamic dispatch reaches LinkTrackingMailClient._send_individualized in v9.4,
            # keeping tracked recipient MIME and sanitized Sent MIME on the same pipeline.
            result = self._send_individualized(
                to=resolved["to"],
                cc=cc_clean,
                bcc=bcc,
                subject=resolved["subject"],
                body=body,
                body_html=body_html,
                body_amp=None,
                attachments=attachments,
                track_opens=True,
                campaign_id=campaign_id,
                in_reply_to=resolved["message_id"],
                references=resolved["references"],
            )

        result.update(
            {
                "thread_mode": mode,
                "in_reply_to": resolved["message_id"],
                "references": resolved["references"],
                "resolved_to": list(resolved["to"]),
                "resolved_cc": list(cc_clean),
            }
        )
        key = "reply_to" if mode == "reply" else "follow_up_to"
        result[key] = {
            "mailbox": mailbox,
            "uid": uid,
            "message_id": resolved["message_id"],
        }
        return result

    def reply_email(
        self,
        *,
        mailbox: str,
        uid: str,
        body: str = "",
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
        body_html: str | None = None,
        attachments: list[dict[str, Any]] | None = None,
        track_opens: bool | None = None,
        campaign_id: str | None = None,
    ) -> dict[str, Any]:
        return self._send_threaded(
            mode="reply", mailbox=mailbox, uid=uid, body=body, cc=cc, bcc=bcc,
            body_html=body_html, attachments=attachments,
            track_opens=track_opens, campaign_id=campaign_id,
        )

    def follow_up_email(
        self,
        *,
        mailbox: str,
        uid: str,
        body: str = "",
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
        body_html: str | None = None,
        attachments: list[dict[str, Any]] | None = None,
        track_opens: bool | None = None,
        campaign_id: str | None = None,
    ) -> dict[str, Any]:
        return self._send_threaded(
            mode="follow_up", mailbox=mailbox, uid=uid, body=body, cc=cc, bcc=bcc,
            body_html=body_html, attachments=attachments,
            track_opens=track_opens, campaign_id=campaign_id,
        )

    def _create_thread_draft(
        self,
        *,
        mode: ThreadMode,
        mailbox: str,
        uid: str,
        body: str = "",
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
        body_html: str | None = None,
        attachments: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        resolved = self.resolve_thread_recipients(mailbox, uid, mode=mode)
        identities = sender_identity_addresses(self.settings)
        cc_clean = merge_thread_cc(
            resolved["to"], resolved["cc"], cc, sender_identities=identities
        )
        msg, recipients, meta = self._build_message(
            to=resolved["to"], subject=resolved["subject"], body=body, cc=cc_clean,
            bcc=bcc, body_html=body_html, attachments=attachments,
            allow_unlisted=True, include_bcc_header=True,
            in_reply_to=resolved["message_id"], references=resolved["references"],
        )
        result = self._save_draft(msg)
        result.update(
            {
                "html": True,
                "attachments": meta,
                "recipient_authorization": self.recipient_authorization_status(recipients)["results"],
                "thread_mode": mode,
                "in_reply_to": resolved["message_id"],
                "references": resolved["references"],
                "resolved_to": list(resolved["to"]),
                "resolved_cc": list(cc_clean),
            }
        )
        key = "reply_to" if mode == "reply" else "follow_up_to"
        result[key] = {
            "mailbox": mailbox,
            "uid": uid,
            "message_id": resolved["message_id"],
        }
        return result

    def create_reply_draft(
        self,
        *,
        mailbox: str,
        uid: str,
        body: str = "",
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
        body_html: str | None = None,
        attachments: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        return self._create_thread_draft(
            mode="reply", mailbox=mailbox, uid=uid, body=body, cc=cc, bcc=bcc,
            body_html=body_html, attachments=attachments,
        )

    def create_follow_up_draft(
        self,
        *,
        mailbox: str,
        uid: str,
        body: str = "",
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
        body_html: str | None = None,
        attachments: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        return self._create_thread_draft(
            mode="follow_up", mailbox=mailbox, uid=uid, body=body, cc=cc, bcc=bcc,
            body_html=body_html, attachments=attachments,
        )
