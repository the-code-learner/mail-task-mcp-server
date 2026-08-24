from __future__ import annotations

import hashlib
from contextvars import ContextVar
from email.message import EmailMessage
from typing import Any

from .mail_v950 import PostmasterV950MailClient
from .mail_v960 import PostmasterV960MailClient
from .mail_v960_unsubscribe import PostmasterV960NewsletterMailClient
from .outbound_operations_v969 import outbound_operation_store

_ARCHIVE_CONTEXT: ContextVar[dict[str, Any] | None] = ContextVar(
    "postmaster_v969_archive_context", default=None
)
_ORIGINAL_SAVE_SENT_COPY = PostmasterV950MailClient._save_sent_copy


def _clean_addresses(client: Any, values: Any) -> list[str]:
    raw = values or []
    try:
        return list(client._validate_recipients(raw))
    except Exception:
        return [str(value).strip() for value in raw if str(value).strip()]


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        key = text.casefold()
        if not text or key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result


def _recipient_groups(
    client: Any,
    action: str,
    kwargs: dict[str, Any],
    result: dict[str, Any],
) -> tuple[list[str], list[str], list[str]]:
    bcc_values = _clean_addresses(client, kwargs.get("bcc") or [])
    if action == "send_email":
        return (
            _clean_addresses(client, kwargs.get("to") or []),
            _clean_addresses(client, kwargs.get("cc") or []),
            bcc_values,
        )

    to_values = _clean_addresses(client, result.get("resolved_to") or [])
    cc_values = _clean_addresses(client, result.get("resolved_cc") or [])
    if not to_values:
        mailbox = str(kwargs.get("mailbox") or "").strip()
        uid = str(kwargs.get("uid") or "").strip()
        mode = "reply" if action == "reply_email" else "follow_up"
        if mailbox and uid and callable(getattr(client, "resolve_thread_recipients", None)):
            try:
                resolved = client.resolve_thread_recipients(mailbox, uid, mode=mode)
                to_values = _clean_addresses(client, resolved.get("to") or [])
                cc_values = _clean_addresses(client, resolved.get("cc") or [])
            except Exception:
                pass

    explicit_cc = _clean_addresses(client, kwargs.get("cc") or [])
    cc_values = _dedupe([*cc_values, *explicit_cc])
    if not to_values:
        envelope = _clean_addresses(client, result.get("to") or [])
        hidden = {value.casefold() for value in [*cc_values, *bcc_values]}
        to_values = [value for value in envelope if value.casefold() not in hidden]
    return _dedupe(to_values), cc_values, bcc_values


def _normal_group_deliveries(
    operation_id: str,
    canonical_message_id: str,
    *,
    to_values: list[str],
    cc_values: list[str],
    bcc_values: list[str],
) -> list[dict[str, str]]:
    """Represent one normal group SMTP submission as logical per-recipient deliveries.

    A normal send still uses one MIME/one DATA transaction and one canonical Sent APPEND.
    These records model the RCPT envelope recipients for reply correlation and private
    sender-side metadata without pretending the transport was individualized.
    """

    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for role, values in (("to", to_values), ("cc", cc_values), ("bcc", bcc_values)):
        for recipient in values:
            key = recipient.casefold()
            if key in seen:
                continue
            seen.add(key)
            seed = f"{operation_id}\x00{role}\x00{key}"
            rows.append(
                {
                    "delivery_id": "delivery_" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24],
                    "message_id": canonical_message_id,
                    "recipient": recipient,
                    "recipient_role": role,
                    "role": role,
                }
            )
    return rows


def _record_logical_operation(
    client: Any,
    *,
    action: str,
    kwargs: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any]:
    operation_id = str(result.get("outbound_operation_id") or "").strip()
    if not operation_id:
        return result

    result["logical_outbound_operation_id"] = operation_id
    account_id = str(
        getattr(getattr(client, "settings", None), "account_id", "")
        or getattr(getattr(client, "settings", None), "email_address", "")
        or ""
    )
    to_values, cc_values, bcc_values = _recipient_groups(client, action, kwargs, result)
    canonical_message_id = str(
        result.get("canonical_sent_message_id")
        or result.get("message_id")
        or ""
    )
    archived = bool(
        result.get("canonical_sent_copy_saved")
        if "canonical_sent_copy_saved" in result
        else result.get("sent_copy_saved")
    )
    deliveries = [
        dict(row)
        for row in (result.get("deliveries") or [])
        if isinstance(row, dict)
    ]
    if (
        not deliveries
        and action == "send_email"
        and bool(result.get("sent"))
        and canonical_message_id
    ):
        deliveries = _normal_group_deliveries(
            operation_id,
            canonical_message_id,
            to_values=to_values,
            cc_values=cc_values,
            bcc_values=bcc_values,
        )
        result["deliveries"] = [dict(row) for row in deliveries]
        result["logical_group_delivery_count"] = len(deliveries)

    metadata_saved = False
    try:
        outbound_operation_store().record_operation(
            operation_id=operation_id,
            account_id=account_id,
            canonical_message_id=canonical_message_id,
            canonical_sent_archived=archived,
            to=to_values,
            cc=cc_values,
            bcc=bcc_values,
            deliveries=deliveries,
        )
        metadata_saved = True
    except Exception:
        # The SMTP operation has already completed. A metadata/archive bookkeeping failure
        # must never turn a successful send into an automatic retry opportunity.
        metadata_saved = False

    result["sender_private_recipient_metadata_saved"] = metadata_saved
    return result


def _install_outbound_archive_boundary() -> None:
    current_save = PostmasterV950MailClient._save_sent_copy
    if not getattr(current_save, "_postmaster_v969_archive_gate", False):

        def _save_sent_copy(self: Any, msg: EmailMessage):
            context = _ARCHIVE_CONTEXT.get()
            if context is not None:
                if context.get("canonical_message") is None:
                    context["canonical_message"] = msg
                return False, None
            return _ORIGINAL_SAVE_SENT_COPY(self, msg)

        _save_sent_copy._postmaster_v969_archive_gate = True  # type: ignore[attr-defined]
        PostmasterV950MailClient._save_sent_copy = _save_sent_copy  # type: ignore[assignment]

    current_individualized = PostmasterV960NewsletterMailClient._send_individualized
    if not getattr(current_individualized, "_postmaster_v969_logical_sent", False):

        def _send_individualized(self: Any, *args: Any, **kwargs: Any):
            context: dict[str, Any] = {"canonical_message": None}
            token = _ARCHIVE_CONTEXT.set(context)
            try:
                result = current_individualized(self, *args, **kwargs)
            finally:
                _ARCHIVE_CONTEXT.reset(token)

            if not isinstance(result, dict):
                return result
            deliveries = list(result.get("deliveries") or [])
            canonical = context.get("canonical_message")
            archived = False
            archive_error: str | None = None
            canonical_message_id = ""
            if deliveries and isinstance(canonical, EmailMessage):
                canonical_message_id = str(canonical.get("Message-ID") or "")
                archived, archive_error = _ORIGINAL_SAVE_SENT_COPY(self, canonical)

            for delivery in deliveries:
                if isinstance(delivery, dict):
                    is_canonical = bool(
                        canonical_message_id
                        and str(delivery.get("message_id") or "") == canonical_message_id
                    )
                    delivery["sent_copy_saved"] = bool(archived and is_canonical)
                    delivery["canonical_sent_archive"] = bool(archived and is_canonical)

            result.update(
                {
                    "canonical_sent_copy_saved": bool(archived),
                    "canonical_sent_message_id": canonical_message_id,
                    "canonical_sent_error": archive_error or "",
                    "sent_append_count": 1 if archived else 0,
                }
            )
            return result

        _send_individualized._postmaster_v969_logical_sent = True  # type: ignore[attr-defined]
        PostmasterV960NewsletterMailClient._send_individualized = _send_individualized  # type: ignore[assignment]

    current_safe = PostmasterV960MailClient._safe_outbound
    if not getattr(current_safe, "_postmaster_v969_logical_metadata", False):

        def _safe_outbound(
            self: Any,
            *,
            action: str,
            kwargs: dict[str, Any],
            callback: Any,
            idempotency_key: str | None,
            force_send: bool,
        ) -> dict[str, Any]:
            result = current_safe(
                self,
                action=action,
                kwargs=kwargs,
                callback=callback,
                idempotency_key=idempotency_key,
                force_send=force_send,
            )
            if not isinstance(result, dict):
                return result
            return _record_logical_operation(
                self,
                action=action,
                kwargs=dict(kwargs),
                result=result,
            )

        _safe_outbound._postmaster_v969_logical_metadata = True  # type: ignore[attr-defined]
        PostmasterV960MailClient._safe_outbound = _safe_outbound  # type: ignore[assignment]


__all__ = [
    "_ARCHIVE_CONTEXT",
    "_install_outbound_archive_boundary",
    "_normal_group_deliveries",
    "_record_logical_operation",
]
