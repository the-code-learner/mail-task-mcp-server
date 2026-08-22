from __future__ import annotations

import re
import time
from datetime import datetime, timedelta, timezone
from email import policy
from email.message import Message
from email.parser import BytesParser
from typing import Any, Callable

from .inbound_inspection import inspect_message
from .mail_bridge import MailBridgeError, _message_to_dict
from .mail_v950 import PostmasterV950MailClient
from .outbound_safety import OutboundSafetyError, OutboundSafetyStore


_LOCALIZED_ROLE_NAMES: dict[str, set[str]] = {
    "sent": {
        "sent", "sent items", "sent messages", "inbox.sent", "posta inviata", "inviati",
        "enviati", "enviados", "envoyés", "gesendet", "gesendete elemente",
    },
    "spam": {
        "junk", "spam", "junk e-mail", "posta indesiderata", "indesiderata",
        "courrier indésirable", "correo no deseado",
    },
    "drafts": {
        "drafts", "draft", "bozze", "brouillons", "entwürfe", "borradores",
    },
    "trash": {
        "trash", "deleted items", "deleted messages", "cestino", "corbeille",
        "papierkorb", "papelera", "eliminati",
    },
}

_SPECIAL_USE_FLAGS = {
    r"\inbox": "received",
    r"\sent": "sent",
    r"\junk": "spam",
    r"\drafts": "drafts",
    r"\trash": "trash",
}


def classify_mailbox_role(name: str, flags: list[str] | tuple[str, ...], settings: Any) -> str:
    folded_flags = {str(flag or "").casefold() for flag in flags}
    for flag, role in _SPECIAL_USE_FLAGS.items():
        if flag in folded_flags:
            return role

    value = (name or "").strip()
    folded = value.casefold()
    configured = {
        "received": str(getattr(settings, "inbox_mailbox", "") or "").casefold(),
        "sent": str(getattr(settings, "sent_mailbox", "") or "").casefold(),
        "spam": str(getattr(settings, "junk_mailbox", "") or "").casefold(),
        "drafts": str(getattr(settings, "draft_mailbox", "") or "").casefold(),
    }
    for role, configured_name in configured.items():
        if configured_name and folded == configured_name:
            return role
    if folded == "inbox":
        return "received"
    for role, names in _LOCALIZED_ROLE_NAMES.items():
        if folded in names:
            return role
    return "other"


def _mailbox_name_from_list_line(line: str) -> str:
    match = re.search(r'"([^"\\]*(?:\\.[^"\\]*)*)"\s*$', line)
    if match:
        return match.group(1).replace(r'\"', '"')
    return line.rsplit(" ", 1)[-1].strip('"')


def _mailbox_flags_from_list_line(line: str) -> list[str]:
    match = re.match(r"\s*\(([^)]*)\)", line)
    if not match:
        return []
    return [part.strip() for part in match.group(1).split() if part.strip()]


def _mime_tree(msg: Message, depth: int = 0) -> dict[str, Any]:
    node: dict[str, Any] = {
        "content_type": str(msg.get_content_type() or ""),
        "content_disposition": str(msg.get_content_disposition() or ""),
        "filename": str(msg.get_filename() or ""),
        "content_id": str(msg.get("Content-ID") or ""),
        "multipart": bool(msg.is_multipart()),
    }
    if depth < 12 and msg.is_multipart():
        payload = msg.get_payload()
        if isinstance(payload, list):
            node["parts"] = [
                _mime_tree(part, depth + 1)
                for part in payload
                if isinstance(part, Message)
            ]
    return node


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000.0, 2)


class PostmasterV960MailClient(PostmasterV950MailClient):
    """v9.6 compatibility layer for outbound safety and privacy-aware inbound reads."""

    def __init__(
        self,
        settings: Any,
        *,
        outbound_safety: OutboundSafetyStore | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(settings, **kwargs)
        self.outbound_safety = outbound_safety or OutboundSafetyStore()
        self.last_timings_ms: dict[str, float] = {}

    def _account_scope(self) -> str:
        return str(getattr(self.settings, "account_id", "") or self.settings.email_address or "default")

    @staticmethod
    def _visible_send_payload(action: str, kwargs: dict[str, Any]) -> dict[str, Any]:
        keys = (
            "to", "cc", "bcc", "subject", "body", "body_html", "body_amp", "attachments",
            "mailbox", "uid",
        )
        return {"action": action, **{key: kwargs.get(key) for key in keys if key in kwargs}}

    def _safe_outbound(
        self,
        *,
        action: str,
        kwargs: dict[str, Any],
        callback: Callable[[], dict[str, Any]],
        idempotency_key: str | None,
        force_send: bool,
    ) -> dict[str, Any]:
        try:
            return self.outbound_safety.execute(
                account_id=self._account_scope(),
                action=action,
                payload={"action": action, **kwargs},
                duplicate_payload=self._visible_send_payload(action, kwargs),
                callback=callback,
                idempotency_key=idempotency_key,
                force_send=force_send,
            )
        except OutboundSafetyError as exc:
            raise MailBridgeError(str(exc)) from exc

    def send_email(
        self,
        *,
        idempotency_key: str | None = None,
        force_send: bool = False,
        **kwargs: Any,
    ) -> dict[str, Any]:
        parent = super(PostmasterV960MailClient, self)
        return self._safe_outbound(
            action="send_email",
            kwargs=dict(kwargs),
            callback=lambda: parent.send_email(**kwargs),
            idempotency_key=idempotency_key,
            force_send=force_send,
        )

    def reply_email(
        self,
        *,
        idempotency_key: str | None = None,
        force_send: bool = False,
        **kwargs: Any,
    ) -> dict[str, Any]:
        parent = super(PostmasterV960MailClient, self)
        return self._safe_outbound(
            action="reply_email",
            kwargs=dict(kwargs),
            callback=lambda: parent.reply_email(**kwargs),
            idempotency_key=idempotency_key,
            force_send=force_send,
        )

    def follow_up_email(
        self,
        *,
        idempotency_key: str | None = None,
        force_send: bool = False,
        **kwargs: Any,
    ) -> dict[str, Any]:
        parent = super(PostmasterV960MailClient, self)
        return self._safe_outbound(
            action="follow_up_email",
            kwargs=dict(kwargs),
            callback=lambda: parent.follow_up_email(**kwargs),
            idempotency_key=idempotency_key,
            force_send=force_send,
        )

    def mailbox_catalog(self) -> list[dict[str, Any]]:
        timings: dict[str, float] = {}
        with self._imap() as conn:
            started = time.perf_counter()
            typ, data = conn.list()
            timings["imap_list"] = _elapsed_ms(started)
            if typ != "OK":
                raise MailBridgeError("Could not list IMAP mailboxes")
            records: list[dict[str, Any]] = []
            for raw in data or []:
                if not raw:
                    continue
                line = raw.decode(errors="replace") if isinstance(raw, bytes) else str(raw)
                name = _mailbox_name_from_list_line(line)
                flags = _mailbox_flags_from_list_line(line)
                records.append(
                    {
                        "name": name,
                        "flags": flags,
                        "role": classify_mailbox_role(name, flags, self.settings),
                    }
                )
        role_order = {"received": 0, "sent": 1, "spam": 2, "drafts": 3, "trash": 4, "other": 5}
        records.sort(key=lambda row: (role_order.get(str(row["role"]), 9), str(row["name"]).casefold()))
        self.last_timings_ms = timings
        return records

    def list_mailboxes(self) -> list[str]:
        # Keep the v9.5.x return contract: a simple list of mailbox names.
        return [str(row["name"]) for row in self.mailbox_catalog()]

    @staticmethod
    def _seen_from_fetch(data: Any) -> bool:
        for item in data or []:
            raw = item[0] if isinstance(item, tuple) else item
            if isinstance(raw, bytes) and re.search(rb"(?i)(?:^|\s)\\Seen(?:\s|\))", raw):
                return True
        return False

    def _seen_for_uid(self, conn: Any, uid: str) -> bool:
        typ, data = conn.uid("FETCH", uid, "(FLAGS)")
        return bool(typ == "OK" and self._seen_from_fetch(data))

    def search_emails(
        self,
        *,
        mailbox: str = "INBOX",
        from_address: str | None = None,
        to_address: str | None = None,
        subject: str | None = None,
        text: str | None = None,
        since_days: int = 90,
        unread_only: bool = False,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        if not 1 <= limit <= 100:
            raise MailBridgeError("limit must be between 1 and 100")
        since_days = max(0, min(since_days, 3650))
        cutoff = datetime.now(timezone.utc) - timedelta(days=since_days)
        role = classify_mailbox_role(mailbox, [], self.settings)
        timings = {"imap_search": 0.0, "imap_fetch": 0.0, "imap_flags": 0.0}

        with self._imap() as conn:
            self._select(conn, mailbox, readonly=True)
            criteria = ["ALL"]
            if unread_only:
                criteria.append("UNSEEN")
            started = time.perf_counter()
            typ, data = conn.uid("SEARCH", None, *criteria)
            timings["imap_search"] += _elapsed_ms(started)
            if typ != "OK" or not data:
                raise MailBridgeError("IMAP search failed")
            uids = data[0].decode().split()
            uids = list(reversed(uids[-self.settings.search_candidate_limit :]))

            filters = {
                "from": (from_address or "").casefold().strip(),
                "to": (to_address or "").casefold().strip(),
                "subject": (subject or "").casefold().strip(),
                "text": (text or "").casefold().strip(),
            }
            results: list[dict[str, Any]] = []
            for uid in uids:
                started = time.perf_counter()
                header_raw = self._fetch_headers(conn, uid)
                timings["imap_fetch"] += _elapsed_ms(started)
                header_msg = BytesParser(policy=policy.default).parsebytes(header_raw)
                header_row = _message_to_dict(
                    header_msg,
                    uid=uid,
                    mailbox=mailbox,
                    include_body=False,
                    truncated=False,
                )
                date_value = header_row.get("date")
                if date_value:
                    try:
                        dt = datetime.fromisoformat(str(date_value))
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=timezone.utc)
                        if dt < cutoff:
                            continue
                    except Exception:
                        pass
                if filters["from"] and filters["from"] not in str(header_row["from"]).casefold():
                    continue
                if filters["to"] and filters["to"] not in str(header_row["to"]).casefold():
                    continue
                if filters["subject"] and filters["subject"] not in str(header_row["subject"]).casefold():
                    continue

                started = time.perf_counter()
                raw, truncated = self._fetch_raw(conn, uid)
                timings["imap_fetch"] += _elapsed_ms(started)
                msg = BytesParser(policy=policy.default).parsebytes(raw)
                row = _message_to_dict(
                    msg,
                    uid=uid,
                    mailbox=mailbox,
                    include_body=not truncated,
                    truncated=truncated,
                )
                if filters["text"]:
                    haystack = "\n".join(
                        [str(row["subject"]), str(row["from"]), str(row["to"]), str(row["body"])]
                    ).casefold()
                    if filters["text"] not in haystack:
                        continue
                body = str(row.pop("body", ""))
                row["snippet"] = re.sub(r"\s+", " ", body).strip()[:600]
                started = time.perf_counter()
                row["seen"] = self._seen_for_uid(conn, uid)
                timings["imap_flags"] += _elapsed_ms(started)
                row["mailbox_role"] = role
                results.append(row)
                if len(results) >= limit:
                    break
        self.last_timings_ms = {key: round(value, 2) for key, value in timings.items()}
        return results

    def get_email(
        self,
        mailbox: str,
        uid: str,
        *,
        inspection: str | None = None,
        content_mode: str = "safe",
        acknowledge_unsanitized_content_risk: bool = False,
    ) -> dict[str, Any]:
        timings = {"imap_fetch": 0.0, "imap_flags": 0.0, "inspection": 0.0}
        with self._imap() as conn:
            self._select(conn, mailbox, readonly=True)
            started = time.perf_counter()
            raw, truncated = self._fetch_raw(conn, uid)
            timings["imap_fetch"] += _elapsed_ms(started)
            msg = BytesParser(policy=policy.default).parsebytes(raw)
            row = _message_to_dict(
                msg,
                uid=uid,
                mailbox=mailbox,
                include_body=not truncated,
                truncated=truncated,
            )
            started = time.perf_counter()
            row["seen"] = self._seen_for_uid(conn, uid)
            timings["imap_flags"] += _elapsed_ms(started)
        row["mailbox_role"] = classify_mailbox_role(mailbox, [], self.settings)
        original_html = str(row.get("body_html") or "")
        selected = (inspection or "").strip().casefold()
        if not selected:
            row["content_safety"] = {
                "body_html": "original_unsanitized",
                "legacy_compatibility": True,
                "warning": "body_html is the original message HTML and may reference remote resources",
            }
            row["performance_ms"] = {key: round(value, 2) for key, value in timings.items()}
            self.last_timings_ms = dict(row["performance_ms"])
            return row
        if selected not in {"summary", "full"}:
            raise MailBridgeError("inspection must be 'summary' or 'full'")

        started = time.perf_counter()
        privacy = inspect_message(
            msg,
            body_html=original_html,
            body_text=str(row.get("body") or ""),
            mode=selected,
        )
        timings["inspection"] = _elapsed_ms(started)
        safe_html = str(privacy.pop("sanitized_html", ""))
        mode = (content_mode or "safe").strip().casefold()
        if mode not in {"safe", "raw"}:
            raise MailBridgeError("content_mode must be 'safe' or 'raw'")
        if mode == "raw":
            if not acknowledge_unsanitized_content_risk:
                raise MailBridgeError(
                    "content_mode='raw' requires acknowledge_unsanitized_content_risk=true"
                )
            row["body_html"] = original_html
            row["body_html_safe"] = safe_html
            row["content_safety"] = {
                "body_html": "original_unsanitized_explicit",
                "acknowledged": True,
            }
        else:
            row["body_html"] = safe_html
            row["content_safety"] = {
                "body_html": "sanitized_static",
                "original_html_returned": False,
            }
        row["privacy_inspection"] = privacy
        if selected == "full":
            row["headers"] = [
                {"name": str(name), "value": str(value)}
                for name, value in msg.raw_items()
            ]
            row["mime"] = _mime_tree(msg)
        row["performance_ms"] = {key: round(value, 2) for key, value in timings.items()}
        self.last_timings_ms = dict(row["performance_ms"])
        return row
