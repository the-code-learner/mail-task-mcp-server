from __future__ import annotations

import base64
import hashlib
import json
from datetime import UTC, datetime
from typing import Any, Callable, Mapping, Sequence

from mcp.types import ToolAnnotations

from .calendar_v990 import build_calendar_request, detect_calendar_parts, job_calendar_window
from .email_search_v990 import EmailSearchError, HybridEmailIndex, combine_attachment_text
from .tracking_policy_v990 import (
    TrackingApprovalError,
    TrackingApprovalStore,
    attachment_intent_digest,
    body_digest,
    classify_open_event,
    effective_tracking,
)

MCP_COMMAND_COUNT_V980 = 118
MCP_EMAIL_CALENDAR_COMMANDS_V990 = 4
# WhatsApp tools are installed by whatsapp_runtime_v990 and counted here for one release identity.
MCP_WHATSAPP_COMMANDS_V990 = 8
MCP_COMMAND_COUNT_V990 = MCP_COMMAND_COUNT_V980 + MCP_EMAIL_CALENDAR_COMMANDS_V990 + MCP_WHATSAPP_COMMANDS_V990


def _annotations(*, read_only: bool, destructive: bool = False, idempotent: bool = False):
    return ToolAnnotations(
        read_only_hint=read_only,
        destructive_hint=destructive,
        idempotent_hint=idempotent,
        open_world_hint=False,
    )


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _safe(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    try:
        return fn(*args, **kwargs)
    except (TrackingApprovalError, EmailSearchError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:  # preserve the project's MCP no-crash convention
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def _account(base: Any, account_id: str | None) -> dict[str, Any]:
    store = base.account_store()
    row = store.get_account(account_id)
    if not isinstance(row, Mapping):
        raise ValueError("Unable to resolve selected email account")
    return dict(row)


def _account_id(base: Any, account_id: str | None) -> str:
    row = _account(base, account_id)
    resolved = str(row.get("id") or row.get("account_id") or "").strip()
    if not resolved:
        raise ValueError("Selected email account has no account id")
    return resolved


def _tracking_default(base: Any, account_id: str | None) -> bool:
    row = _account(base, account_id)
    return bool(row.get("tracking_default", False))


def _normalize_recipients(value: Sequence[str] | None) -> list[str]:
    return [str(x).strip() for x in (value or []) if str(x).strip()]


def _send_intent(
    operation: str,
    *,
    account_id: str,
    to: Sequence[str] | None = None,
    mailbox: str | None = None,
    uid: str | None = None,
    subject: str | None = None,
    body: str = "",
    body_html: str | None = None,
    body_amp: str | None = None,
    cc: Sequence[str] | None = None,
    bcc: Sequence[str] | None = None,
    attachments: Any = None,
    campaign_id: str | None = None,
    idempotency_key: str | None = None,
    extras: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "operation": operation,
        "account_id": account_id,
        "to": _normalize_recipients(to),
        "mailbox": str(mailbox or ""),
        "uid": str(uid or ""),
        "subject": str(subject or ""),
        "body_sha256": body_digest(body),
        "body_html_sha256": body_digest(body_html),
        "body_amp_sha256": body_digest(body_amp),
        "cc": _normalize_recipients(cc),
        "bcc": _normalize_recipients(bcc),
        "attachments_sha256": attachment_intent_digest(attachments),
        "campaign_id": str(campaign_id or ""),
        "idempotency_key": str(idempotency_key or ""),
        "extras": dict(extras or {}),
    }


def _approval_summary(intent: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "operation": intent.get("operation"),
        "account_id": intent.get("account_id"),
        "to": intent.get("to"),
        "mailbox": intent.get("mailbox"),
        "uid": intent.get("uid"),
        "subject": intent.get("subject"),
        "tracking": "off",
    }


def _guard_tracking_off(
    store: TrackingApprovalStore,
    *,
    operation: str,
    intent: Mapping[str, Any],
    effective: bool,
    approval_id: str | None,
) -> dict[str, Any] | None:
    if effective:
        return None
    if not approval_id:
        return store.create(operation=operation, intent=intent, summary=_approval_summary(intent))
    try:
        store.consume(approval_id, operation=operation, intent=intent)
    except TrackingApprovalError as exc:
        return {
            "ok": False,
            "approval_required": True,
            "approval_type": "tracking_disabled",
            "send_performed": False,
            "error": str(exc),
            "message": "Ask for fresh explicit chat approval before retrying this tracking-OFF action.",
        }
    return None


def _semantic_embedder(base: Any) -> tuple[Callable[[str], Sequence[float]] | None, str]:
    """Adapt the existing local semantic runtime without downloading a second model."""
    try:
        engine = base.context_engine()
    except Exception:
        return None, "lexical-only"
    candidates = [engine, getattr(engine, "semantic", None), getattr(engine, "semantic_engine", None), getattr(getattr(engine, "store", None), "semantic", None)]
    for obj in [x for x in candidates if x is not None]:
        model_id = str(getattr(obj, "model_id", None) or getattr(obj, "model_name", None) or "postmaster-context-model")
        for name in ("embed_one", "encode_one"):
            fn = getattr(obj, name, None)
            if callable(fn):
                return lambda text, fn=fn: fn(text), model_id
        fn = getattr(obj, "encode", None)
        if callable(fn):
            def encode_one(text: str, fn=fn):
                result = fn([text])
                try:
                    return result[0]
                except Exception:
                    return result
            return encode_one, model_id
        model = getattr(obj, "model", None)
        fn = getattr(model, "encode", None)
        if callable(fn):
            return lambda text, fn=fn: fn([text])[0], model_id
    return None, "lexical-only"


def _message_rows(result: Any) -> list[dict[str, Any]]:
    if isinstance(result, list):
        return [dict(x) for x in result if isinstance(x, Mapping)]
    if isinstance(result, Mapping):
        for key in ("emails", "messages", "results", "items"):
            value = result.get(key)
            if isinstance(value, list):
                return [dict(x) for x in value if isinstance(x, Mapping)]
    return []


def _full_message(result: Any) -> dict[str, Any]:
    if isinstance(result, Mapping):
        if isinstance(result.get("email"), Mapping):
            return dict(result["email"])
        return dict(result)
    return {}


def _attachment_text_from_tools(core: Any, *, mailbox: str, uid: str, account_id: str, max_attachments: int = 12) -> tuple[str, list[dict[str, Any]]]:
    try:
        listing = core.list_email_attachments(mailbox=mailbox, uid=uid, account_id=account_id)
    except TypeError:
        listing = core.list_email_attachments(mailbox, uid, account_id)
    rows = []
    if isinstance(listing, Mapping):
        rows = listing.get("attachments") or listing.get("items") or []
    elif isinstance(listing, list):
        rows = listing
    extracted: list[dict[str, Any]] = []
    for idx, item in enumerate(rows[:max_attachments]):
        if not isinstance(item, Mapping):
            continue
        filename = str(item.get("filename") or item.get("name") or "")
        content_type = str(item.get("content_type") or item.get("media_type") or "")
        try:
            read = core.read_email_attachment(
                mailbox=mailbox,
                uid=uid,
                filename=filename or None,
                index=item.get("index", idx),
                max_chars=80000,
                account_id=account_id,
            )
        except Exception as exc:
            extracted.append({"filename": filename or None, "content_type": content_type, "text": "", "error": f"{type(exc).__name__}: {exc}"})
            continue
        if isinstance(read, Mapping):
            text = str(read.get("text") or read.get("content") or "")
            extracted.append({
                "filename": filename or read.get("filename"),
                "content_type": content_type or read.get("content_type"),
                "text": text,
                "chars": len(text),
                "error": read.get("error"),
                "network_access": False,
            })
    return combine_attachment_text(extracted)


def _enrich_email_index(
    *,
    base: Any,
    core: Any,
    index: HybridEmailIndex,
    query: str,
    account_id: str,
    mailbox: str,
    since_days: int,
    enrich_limit: int,
) -> dict[str, Any]:
    # Do not rely only on lexical IMAP TEXT matching: semantic queries may use different words.
    try:
        discovery = core.search_emails(
            mailbox=mailbox,
            since_days=since_days,
            unread_only=False,
            limit=max(1, min(int(enrich_limit), 100)),
            account_id=account_id,
            include_timings=False,
        )
    except TypeError:
        discovery = core.search_emails(mailbox=mailbox, since_days=since_days, limit=max(1, min(int(enrich_limit), 100)), account_id=account_id)
    rows = _message_rows(discovery)
    indexed = 0
    failures: list[dict[str, str]] = []
    for row in rows:
        uid = str(row.get("uid") or "").strip()
        if not uid:
            continue
        try:
            detail = _full_message(core.get_email(mailbox=mailbox, uid=uid, account_id=account_id))
            attachments_text, attachment_meta = _attachment_text_from_tools(core, mailbox=mailbox, uid=uid, account_id=account_id)
            doc = {
                "account_id": account_id,
                "mailbox": mailbox,
                "uid": uid,
                "message_id": detail.get("message_id") or row.get("message_id"),
                "date_utc": detail.get("date_utc") or detail.get("date") or row.get("date_utc") or row.get("date"),
                "from_address": detail.get("from_address") or detail.get("from") or row.get("from_address") or row.get("from"),
                "to_address": detail.get("to_address") or detail.get("to") or row.get("to_address") or row.get("to"),
                "cc": detail.get("cc") or row.get("cc"),
                "subject": detail.get("subject") or row.get("subject"),
                "snippet": detail.get("snippet") or row.get("snippet"),
                "body_text": detail.get("body_text") or detail.get("body") or "",
                "attachments_text": attachments_text,
                "attachments": attachment_meta,
            }
            index.upsert(doc, embed=True)
            indexed += 1
        except Exception as exc:
            failures.append({"uid": uid, "error": f"{type(exc).__name__}: {exc}"})
    return {"indexed": indexed, "discovered": len(rows), "failures": failures[:20], "query": query, "network_scope": "imap_only"}


def _calendar_parts_from_detail(core: Any, *, mailbox: str, uid: str, account_id: str, get_email_fn: Callable[..., Any] | None = None) -> dict[str, Any]:
    """Read calendar material locally from full-message/raw or ICS attachments.

    Existing Postmaster readers vary by historical release. Prefer raw MIME if exposed; otherwise
    inspect calendar attachment bytes/text through the existing attachment tools. No remote URL is fetched.
    """
    reader = get_email_fn or core.get_email
    detail_raw = reader(mailbox=mailbox, uid=uid, account_id=account_id)
    detail = _full_message(detail_raw)
    raw = detail.get("raw_bytes") or detail.get("raw") or detail.get("raw_message")
    if isinstance(raw, str):
        try:
            raw = base64.b64decode(raw, validate=True)
        except Exception:
            raw = raw.encode("utf-8", errors="replace")
    if isinstance(raw, (bytes, bytearray)):
        return detect_calendar_parts(bytes(raw))

    found: list[dict[str, Any]] = []
    try:
        listing = core.list_email_attachments(mailbox=mailbox, uid=uid, account_id=account_id)
    except Exception:
        listing = []
    rows = listing.get("attachments", []) if isinstance(listing, Mapping) else (listing if isinstance(listing, list) else [])
    for idx, item in enumerate(rows):
        if not isinstance(item, Mapping):
            continue
        filename = str(item.get("filename") or item.get("name") or "")
        ctype = str(item.get("content_type") or item.get("media_type") or "").lower()
        if ctype not in {"text/calendar", "application/ics", "application/calendar", "application/icalendar"} and not filename.lower().endswith((".ics", ".vcs", ".ifb")):
            continue
        try:
            downloaded = core.get_email_attachment(
                mailbox=mailbox, uid=uid, filename=filename or None, index=item.get("index", idx), include_base64=True, account_id=account_id
            )
            if not isinstance(downloaded, Mapping):
                continue
            b64 = downloaded.get("content_base64") or downloaded.get("base64") or downloaded.get("data_base64")
            if b64:
                payload = base64.b64decode(str(b64))
                from email.message import EmailMessage
                msg = EmailMessage()
                msg["Subject"] = str(detail.get("subject") or "")
                msg.set_content("calendar attachment")
                msg.add_attachment(payload, maintype="text", subtype="calendar", filename=filename or "invite.ics")
                parsed = detect_calendar_parts(msg.as_bytes())
                found.extend(parsed.get("calendar_parts", []))
        except Exception:
            continue
    return {
        "ok": True,
        "calendar_detected": bool(found),
        "calendar_parts": found,
        "network_access": False,
        "external_actions": False,
        "detection_scope": "raw-mime-or-local-calendar-attachments",
    }


def install_runtime_v990(
    base: Any,
    core: Any,
    previous_runtime_status: Callable[[], Any],
    *,
    tracking_approval_db: str | None = None,
    email_search_db: str | None = None,
) -> dict[str, Any]:
    """Install v9.9 email/calendar policy and search overlays; WhatsApp installs separately.

    Persistent v9.9 stores are initialized lazily so importing the composed runtime remains
    side-effect free in validation/test environments that intentionally do not mount /data.
    Production still uses the persistent /data paths on first explicit feature use.
    """
    approval_store_obj: TrackingApprovalStore | None = None
    email_index_obj: HybridEmailIndex | None = None
    embed_one: Callable[[str], Sequence[float]] | None = None
    model_id = "lexical-only"
    semantic_resolved = False

    def get_approval_store() -> TrackingApprovalStore:
        nonlocal approval_store_obj
        if approval_store_obj is None:
            approval_store_obj = TrackingApprovalStore(tracking_approval_db or "/data/tracking-approvals-v990.db")
        return approval_store_obj

    def get_email_index() -> HybridEmailIndex:
        nonlocal email_index_obj, embed_one, model_id, semantic_resolved
        if email_index_obj is None:
            if not semantic_resolved:
                embed_one, model_id = _semantic_embedder(base)
                semantic_resolved = True
            email_index_obj = HybridEmailIndex(
                email_search_db or "/data/email-search-v990.db",
                embed_one=embed_one,
                model_id=model_id,
            )
        return email_index_obj

    old_send = core.send_email
    old_reply = core.reply_email
    old_follow = core.follow_up_email
    old_list_open_events = core.list_open_events
    old_get_email = core.get_email
    old_list_tracking_deliveries = getattr(core, "list_tracking_deliveries", None)

    def send_email(
        to: list[str],
        subject: str,
        body: str = "",
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
        body_html: str | None = None,
        body_amp: str | None = None,
        attachments: list[dict[str, Any]] | None = None,
        track_opens: bool | None = None,
        campaign_id: str | None = None,
        account_id: str | None = None,
        newsletter_mode: bool = False,
        unsubscribe_url: str | None = None,
        unsubscribe_email: str | None = None,
        one_click_unsubscribe: bool = False,
        automatic_unsubscribe: bool = True,
        dsn_notify_success: bool = False,
        idempotency_key: str | None = None,
        force_send: bool = False,
        confirm_suppressed_recipients: list[str] | None = None,
        tracking_disable_approval_id: str | None = None,
    ):
        """WRITE ACTION. Send email through the existing outbound pipeline.

        Generated subjects should prefer ASCII text/punctuation when practical; never silently rewrite
        Unicode explicitly supplied by the user. Prefer a Postmaster Stored File/signed storage link over
        embedding an attachment unless the user requests an attachment or the recipient/workflow requires it.

        If the effective tracking state is OFF -- including account-default OFF when track_opens=None -- do
        not send until the user gives fresh explicit chat approval for this exact send. First call returns a
        one-use tracking_disable preview id; retry only after that approval with tracking_disable_approval_id.
        Suppressed-recipient approval remains a separate exact per-send boundary.
        """
        aid = _account_id(base, account_id)
        eff = effective_tracking(track_opens, account_tracking_default=_tracking_default(base, account_id))
        extras = {
            "newsletter_mode": newsletter_mode,
            "unsubscribe_url": unsubscribe_url or "",
            "unsubscribe_email": unsubscribe_email or "",
            "one_click_unsubscribe": one_click_unsubscribe,
            "automatic_unsubscribe": automatic_unsubscribe,
            "dsn_notify_success": dsn_notify_success,
            "force_send": force_send,
            "confirm_suppressed_recipients": sorted(confirm_suppressed_recipients or []),
        }
        intent = _send_intent(
            "send_email", account_id=aid, to=to, subject=subject, body=body, body_html=body_html, body_amp=body_amp,
            cc=cc, bcc=bcc, attachments=attachments, campaign_id=campaign_id, idempotency_key=idempotency_key, extras=extras,
        )
        blocked = _guard_tracking_off(get_approval_store(), operation="send_email", intent=intent, effective=eff, approval_id=tracking_disable_approval_id)
        if blocked:
            return blocked
        return old_send(
            to=to, subject=subject, body=body, cc=cc, bcc=bcc, body_html=body_html, body_amp=body_amp,
            attachments=attachments, track_opens=track_opens, campaign_id=campaign_id, account_id=account_id,
            newsletter_mode=newsletter_mode, unsubscribe_url=unsubscribe_url, unsubscribe_email=unsubscribe_email,
            one_click_unsubscribe=one_click_unsubscribe, automatic_unsubscribe=automatic_unsubscribe,
            dsn_notify_success=dsn_notify_success, idempotency_key=idempotency_key, force_send=force_send,
            confirm_suppressed_recipients=confirm_suppressed_recipients,
        )

    def _threaded(operation: str, delegate: Callable[..., Any], *, mailbox: str, uid: str, body: str, cc: list[str] | None,
                  bcc: list[str] | None, body_html: str | None, attachments: list[dict[str, Any]] | None,
                  track_opens: bool | None, campaign_id: str | None, account_id: str | None, newsletter_mode: bool,
                  unsubscribe_url: str | None, unsubscribe_email: str | None, one_click_unsubscribe: bool,
                  dsn_notify_success: bool, idempotency_key: str | None, force_send: bool,
                  confirm_suppressed_recipients: list[str] | None, tracking_disable_approval_id: str | None):
        aid = _account_id(base, account_id)
        eff = effective_tracking(track_opens, account_tracking_default=_tracking_default(base, account_id))
        extras = {
            "newsletter_mode": newsletter_mode,
            "unsubscribe_url": unsubscribe_url or "",
            "unsubscribe_email": unsubscribe_email or "",
            "one_click_unsubscribe": one_click_unsubscribe,
            "dsn_notify_success": dsn_notify_success,
            "force_send": force_send,
            "confirm_suppressed_recipients": sorted(confirm_suppressed_recipients or []),
        }
        intent = _send_intent(
            operation, account_id=aid, mailbox=mailbox, uid=uid, body=body, body_html=body_html, cc=cc, bcc=bcc,
            attachments=attachments, campaign_id=campaign_id, idempotency_key=idempotency_key, extras=extras,
        )
        blocked = _guard_tracking_off(get_approval_store(), operation=operation, intent=intent, effective=eff, approval_id=tracking_disable_approval_id)
        if blocked:
            return blocked
        return delegate(
            mailbox=mailbox, uid=uid, body=body, cc=cc, bcc=bcc, body_html=body_html, attachments=attachments,
            track_opens=track_opens, campaign_id=campaign_id, account_id=account_id, newsletter_mode=newsletter_mode,
            unsubscribe_url=unsubscribe_url, unsubscribe_email=unsubscribe_email, one_click_unsubscribe=one_click_unsubscribe,
            dsn_notify_success=dsn_notify_success, idempotency_key=idempotency_key, force_send=force_send,
            confirm_suppressed_recipients=confirm_suppressed_recipients,
        )

    def reply_email(
        mailbox: str, uid: str, body: str = "", cc: list[str] | None = None, bcc: list[str] | None = None,
        body_html: str | None = None, attachments: list[dict[str, Any]] | None = None, track_opens: bool | None = None,
        campaign_id: str | None = None, account_id: str | None = None, newsletter_mode: bool = False,
        unsubscribe_url: str | None = None, unsubscribe_email: str | None = None, one_click_unsubscribe: bool = False,
        dsn_notify_success: bool = False, idempotency_key: str | None = None, force_send: bool = False,
        confirm_suppressed_recipients: list[str] | None = None, tracking_disable_approval_id: str | None = None,
    ):
        """WRITE ACTION. Reply in-thread through the existing outbound pipeline.

        Prefer Stored File/signed storage links over attachments unless an attachment is requested/required.
        Any effective tracking-OFF reply requires fresh one-use explicit chat approval before transmission.
        """
        return _threaded("reply_email", old_reply, mailbox=mailbox, uid=uid, body=body, cc=cc, bcc=bcc,
                         body_html=body_html, attachments=attachments, track_opens=track_opens, campaign_id=campaign_id,
                         account_id=account_id, newsletter_mode=newsletter_mode, unsubscribe_url=unsubscribe_url,
                         unsubscribe_email=unsubscribe_email, one_click_unsubscribe=one_click_unsubscribe,
                         dsn_notify_success=dsn_notify_success, idempotency_key=idempotency_key, force_send=force_send,
                         confirm_suppressed_recipients=confirm_suppressed_recipients,
                         tracking_disable_approval_id=tracking_disable_approval_id)

    def follow_up_email(
        mailbox: str, uid: str, body: str = "", cc: list[str] | None = None, bcc: list[str] | None = None,
        body_html: str | None = None, attachments: list[dict[str, Any]] | None = None, track_opens: bool | None = None,
        campaign_id: str | None = None, account_id: str | None = None, newsletter_mode: bool = False,
        unsubscribe_url: str | None = None, unsubscribe_email: str | None = None, one_click_unsubscribe: bool = False,
        dsn_notify_success: bool = False, idempotency_key: str | None = None, force_send: bool = False,
        confirm_suppressed_recipients: list[str] | None = None, tracking_disable_approval_id: str | None = None,
    ):
        """WRITE ACTION. Follow up on an outbound message through the existing pipeline.

        Prefer Stored File/signed storage links over attachments unless an attachment is requested/required.
        Any effective tracking-OFF follow-up requires fresh one-use explicit chat approval before transmission.
        """
        return _threaded("follow_up_email", old_follow, mailbox=mailbox, uid=uid, body=body, cc=cc, bcc=bcc,
                         body_html=body_html, attachments=attachments, track_opens=track_opens, campaign_id=campaign_id,
                         account_id=account_id, newsletter_mode=newsletter_mode, unsubscribe_url=unsubscribe_url,
                         unsubscribe_email=unsubscribe_email, one_click_unsubscribe=one_click_unsubscribe,
                         dsn_notify_success=dsn_notify_success, idempotency_key=idempotency_key, force_send=force_send,
                         confirm_suppressed_recipients=confirm_suppressed_recipients,
                         tracking_disable_approval_id=tracking_disable_approval_id)

    def _delivery_sent_times(*, delivery_id: str | None, campaign_id: str | None, recipient: str | None, account_id: str | None) -> dict[str, Any]:
        if not callable(old_list_tracking_deliveries):
            return {}
        try:
            deliveries = old_list_tracking_deliveries(
                campaign_id=campaign_id, recipient=recipient, account_id=account_id, limit=1000
            )
        except TypeError:
            try:
                deliveries = old_list_tracking_deliveries(campaign_id=campaign_id, limit=1000)
            except Exception:
                return {}
        except Exception:
            return {}
        candidates = deliveries if isinstance(deliveries, list) else (deliveries.get("deliveries", []) if isinstance(deliveries, Mapping) else [])
        result: dict[str, Any] = {}
        for item in candidates:
            if not isinstance(item, Mapping):
                continue
            did = str(item.get("delivery_id") or item.get("id") or "").strip()
            if delivery_id and did != str(delivery_id):
                continue
            if did:
                result[did] = item.get("sent_at") or item.get("created_at") or item.get("delivered_at")
        return result

    def list_open_events(delivery_id: str | None = None, campaign_id: str | None = None, recipient: str | None = None,
                         account_id: str | None = None, limit: int = 500):
        """Read-only. List observed open-image events with conservative timing classification.

        Google Image Proxy events more than 60 seconds after sent_at are marked likely_human_via_proxy, not
        guaranteed-human. If the open row lacks sent_at, Postmaster resolves it from the matching delivery.
        Proxy-derived IP/location/browser/OS remain unreliable regardless of timing.
        """
        result = old_list_open_events(delivery_id=delivery_id, campaign_id=campaign_id, recipient=recipient, account_id=account_id, limit=limit)
        if not isinstance(result, (list, Mapping)):
            return result
        rows = result if isinstance(result, list) else result.get("events", result.get("open_events", []))
        if not isinstance(rows, list):
            return result
        sent_times = _delivery_sent_times(delivery_id=delivery_id, campaign_id=campaign_id, recipient=recipient, account_id=account_id)
        for row in rows:
            if not isinstance(row, dict):
                continue
            did = str(row.get("delivery_id") or "").strip()
            sent_at = row.get("sent_at") or row.get("delivery_sent_at") or sent_times.get(did)
            row["open_classification"] = classify_open_event(
                sent_at=sent_at, opened_at=row.get("opened_at") or row.get("created_at"),
                user_agent=str(row.get("user_agent") or ""), client_source=str(row.get("client_source") or ""),
            )
        if isinstance(result, Mapping):
            out = dict(result)
            key = "events" if "events" in result else ("open_events" if "open_events" in result else None)
            if key:
                out[key] = rows
            out["classification_policy"] = {"google_image_proxy_human_delay_seconds": 60, "human_is_probabilistic": True, "proxy_metadata_reliable": False}
            return out
        return rows

    def get_email(mailbox: str, uid: str, account_id: str | None = None, inspection: str | None = None,
                  content_mode: str = "safe", acknowledge_unsanitized_content_risk: bool = False):
        """Read one email and always attach local MIME/header calendar-invitation analysis.

        Calendar detection is read-only and never accepts/declines an invite, creates a task, or fetches
        remote content. Existing Safe Email content-mode semantics are preserved unchanged.
        """
        kwargs = {
            "mailbox": mailbox, "uid": uid, "account_id": account_id, "inspection": inspection,
            "content_mode": content_mode,
            "acknowledge_unsanitized_content_risk": acknowledge_unsanitized_content_risk,
        }
        try:
            original = old_get_email(**kwargs)
        except TypeError:
            # Historical readers may not expose the later safe-inspection arguments.
            original = old_get_email(mailbox=mailbox, uid=uid, account_id=account_id)
        if not isinstance(original, Mapping):
            return original
        aid = _account_id(base, account_id)
        analysis = _calendar_parts_from_detail(
            core, mailbox=mailbox, uid=uid, account_id=aid, get_email_fn=old_get_email
        )
        out = dict(original)
        target = out.get("email") if isinstance(out.get("email"), Mapping) else out
        if target is out:
            out["calendar_analysis"] = analysis
        else:
            email_row = dict(target)
            email_row["calendar_analysis"] = analysis
            out["email"] = email_row
        out["calendar_analysis_policy"] = {
            "mandatory": True, "mime_and_headers": True, "network_access": False,
            "automatic_rsvp": False, "automatic_task": False, "external_actions": False,
        }
        return out

    def email_search_status(account_id: str | None = None):
        """Read-only. Return v9.9 email lexical/semantic index status for one account."""
        aid = _account_id(base, account_id)
        result = get_email_index().status()
        result.update({"account_id": aid, "imap_on_demand_enrichment": True, "external_remote_resource_fetch": False, "attachment_text": True})
        return result

    def search_emails_hybrid(query: str, mailbox: str = "INBOX", since_days: int = 90, limit: int = 20,
                             account_id: str | None = None, enrich_imap: bool = True, enrich_limit: int = 50):
        """Read-only. Hybrid FTS5 + local semantic email search over subject/body and extractable attachments.

        When enrich_imap=true, Postmaster may fetch a bounded set of missing recent messages/attachments from
        the selected IMAP mailbox before ranking. It never loads external URLs/resources contained in email.
        """
        aid = _account_id(base, account_id)
        enrichment = {"indexed": 0, "discovered": 0, "failures": [], "network_scope": "none"}
        if enrich_imap:
            enrichment = _enrich_email_index(base=base, core=core, index=get_email_index(), query=query, account_id=aid,
                                              mailbox=mailbox, since_days=since_days, enrich_limit=enrich_limit)
        result = get_email_index().search(query, account_id=aid, mailbox=mailbox, since_days=since_days, limit=limit)
        result["enrichment"] = enrichment
        result["external_remote_resource_fetch"] = False
        return result

    def get_email_calendar_invites(mailbox: str, uid: str, account_id: str | None = None):
        """Read-only. Analyze MIME/header/calendar material locally for invitations; never RSVP or communicate externally."""
        aid = _account_id(base, account_id)
        result = _calendar_parts_from_detail(core, mailbox=mailbox, uid=uid, account_id=aid, get_email_fn=old_get_email)
        result.update({"account_id": aid, "mailbox": mailbox, "uid": str(uid), "auto_rsvp": False, "auto_task": False})
        return result

    def send_job_calendar_invite(job_id: str, attendees: list[str] | None = None, account_id: str | None = None,
                                 video_url: str | None = None, organizer_name: str | None = None,
                                 track_opens: bool | None = None, tracking_disable_approval_id: str | None = None,
                                 idempotency_key: str | None = None):
        """WRITE ACTION. Explicitly send an RFC5545 calendar invitation for one stored passive task.

        create_job never sends. This tool is the explicit external action. A video URL already stored on the
        task or supplied from another platform may be included. Tracking-OFF approval and recipient policy use
        the normal email send boundary.
        """
        job = core.get_job(job_id=job_id)
        if not isinstance(job, Mapping):
            return {"ok": False, "error": "Task not found"}
        if isinstance(job.get("job"), Mapping):
            job = job["job"]
        payload = _as_dict(job.get("payload"))
        cal = _as_dict(payload.get("calendar"))
        recipients = _normalize_recipients(attendees or cal.get("attendees") or payload.get("attendees") or [])
        if not recipients:
            return {"ok": False, "error": "Calendar invitation requires at least one attendee"}
        start_at, end_at = job_calendar_window(dict(job))
        title = str(cal.get("summary") or job.get("title") or "Scheduled task")
        description = str(cal.get("description") or job.get("description") or "")
        resolved_video = str(video_url or cal.get("video_url") or payload.get("video_url") or "").strip() or None
        aid = _account_id(base, account_id)
        account = _account(base, account_id)
        organizer = str(account.get("email_address") or account.get("email") or "").strip()
        if not organizer:
            return {"ok": False, "error": "Selected account has no email address"}
        uid = str(cal.get("uid") or f"postmaster-job-{job_id}@postmaster.local")
        ics = build_calendar_request(
            uid=uid, summary=title, start_utc=start_at, end_utc=end_at, organizer_email=organizer,
            attendees=recipients, description=description, location=str(cal.get("location") or ""),
            video_url=resolved_video, sequence=int(cal.get("sequence") or 0),
        )
        attachment = {
            "filename": "invite.ics",
            "content_type": "text/calendar; method=REQUEST; charset=UTF-8",
            "content_base64": base64.b64encode(ics.encode("utf-8")).decode("ascii"),
        }
        body = description or f"Calendar invitation: {title}"
        if resolved_video:
            body += f"\n\nVideo call: {resolved_video}"
        return send_email(
            to=recipients, subject=title, body=body, attachments=[attachment], track_opens=track_opens,
            account_id=account_id, idempotency_key=idempotency_key or f"calendar-job:{job_id}:seq:{int(cal.get('sequence') or 0)}",
            tracking_disable_approval_id=tracking_disable_approval_id,
        )

    def runtime_status():
        status = previous_runtime_status()
        if not isinstance(status, dict):
            status = {"ok": True}
        status = dict(status)
        status.update({
            "version_capability": "9.9.0",
            "mcp_command_count_expected": MCP_COMMAND_COUNT_V990,
            "mcp_command_count_delta_from_v980": MCP_EMAIL_CALENDAR_COMMANDS_V990 + MCP_WHATSAPP_COMMANDS_V990,
            "tracking_disable_requires_fresh_chat_approval": True,
            "tracking_disable_approval_one_use": True,
            "google_image_proxy_likely_human_after_seconds": 60,
            "proxy_attribution_metadata_reliable": False,
            "subject_ascii_preferred": True,
            "stored_file_link_preferred_over_attachment": True,
            "calendar_invites": {"task_registry_passive": True, "explicit_send_only": True, "inbound_mime_local_analysis": True, "automatic_rsvp": False},
            "email_hybrid_search": {"fts5": True, "semantic": bool(embed_one) if semantic_resolved else False, "semantic_lazy": True, "attachments": True, "imap_on_demand": True, "external_resource_fetch": False},
        })
        return status

    # Replace existing names without changing their count; add four v9.9 email/calendar names.
    replacements = (
        ("send_email", send_email, _annotations(read_only=False)),
        ("reply_email", reply_email, _annotations(read_only=False)),
        ("follow_up_email", follow_up_email, _annotations(read_only=False)),
        ("list_open_events", list_open_events, _annotations(read_only=True, idempotent=True)),
        ("get_email", get_email, _annotations(read_only=True, idempotent=True)),
        ("runtime_status", runtime_status, _annotations(read_only=True, idempotent=True)),
    )
    for name, fn, annotations in replacements:
        core.mcp.remove_tool(name)
        core.mcp.add_tool(fn, name=name, annotations=annotations)
        setattr(core, name, fn)
        setattr(base, name, fn)
    additions = (
        ("email_search_status", email_search_status, _annotations(read_only=True, idempotent=True)),
        ("search_emails_hybrid", search_emails_hybrid, _annotations(read_only=True, idempotent=True)),
        ("get_email_calendar_invites", get_email_calendar_invites, _annotations(read_only=True, idempotent=True)),
        ("send_job_calendar_invite", send_job_calendar_invite, _annotations(read_only=False)),
    )
    for name, fn, annotations in additions:
        core.mcp.add_tool(fn, name=name, annotations=annotations)
        setattr(core, name, fn)
        setattr(base, name, fn)

    base.tracking_approval_store_v990 = get_approval_store
    base.email_search_index_v990 = get_email_index
    return {
        "approval_store": approval_store_obj,
        "email_index": email_index_obj,
        "approval_store_factory": get_approval_store,
        "email_index_factory": get_email_index,
        "runtime_status": runtime_status,
        "mcp_command_count_expected": MCP_COMMAND_COUNT_V990,
    }


__all__ = [
    "MCP_COMMAND_COUNT_V990",
    "MCP_EMAIL_CALENDAR_COMMANDS_V990",
    "MCP_WHATSAPP_COMMANDS_V990",
    "install_runtime_v990",
]
