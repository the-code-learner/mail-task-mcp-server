from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any, Callable
from urllib.parse import quote, urlsplit

from starlette.requests import Request
from starlette.responses import PlainTextResponse, RedirectResponse, Response

from .email_analytics import EmailAnalyticsStore, _now, _safe_base_url, _token, analytics_store
from .file_store import FileStore, FileStoreError, _safe_filename
from .link_tracking import LinkTrackingStore
from .link_tracking_html import (
    already_tracked_url,
    collect_anchors,
    eligible_web_url,
    normalized_url,
    replace_href,
    rewrite_anchor_tags,
)
from .mail_bridge import MailBridgeError
from .thread_recipients import merge_thread_cc, sender_identity_addresses
from .tracked_mail import LinkTrackingMailClient, _sent_clean_html


_STORED_FILE_SCHEME = "postmaster-file:"
_MEDIA_TYPE_RE = re.compile(r"^[A-Za-z0-9!#$&^_.+-]+/[A-Za-z0-9!#$&^_.+-]+$")


class StoredFileMailError(MailBridgeError):
    """Safe structured error code surfaced through the existing MCP error wrapper."""

    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


def _stored_file_id_from_href(value: str) -> str | None:
    raw = str(value or "").strip()
    if not raw.lower().startswith(_STORED_FILE_SCHEME):
        return None
    file_id = raw[len(_STORED_FILE_SCHEME):].strip()
    if file_id.startswith("//"):
        file_id = file_id[2:]
    if (
        not file_id
        or len(file_id) > 255
        or any(ch in file_id for ch in "/\\?#\x00\r\n")
        or file_id in {".", ".."}
    ):
        raise StoredFileMailError("invalid_stored_file_reference")
    return file_id


def has_stored_file_links(body_html: str | None) -> bool:
    for anchor in collect_anchors(body_html or ""):
        if _stored_file_id_from_href(str(anchor.get("href") or "")) is not None:
            return True
    return False


def _validated_filename(value: str, *, code: str = "invalid_attachment_filename") -> str:
    try:
        return _safe_filename(str(value or ""))
    except FileStoreError as exc:
        raise StoredFileMailError(code) from exc


def _validated_media_type(value: str, *, code: str = "invalid_attachment_media_type") -> str:
    media_type = str(value or "").split(";", 1)[0].strip().lower()
    if not media_type or len(media_type) > 200 or not _MEDIA_TYPE_RE.fullmatch(media_type):
        raise StoredFileMailError(code)
    return media_type


def _public_not_found() -> PlainTextResponse:
    return PlainTextResponse(
        "Not found",
        status_code=404,
        headers={"Cache-Control": "private, no-store, no-cache, max-age=0", "Pragma": "no-cache"},
    )


def _parse_utc(value: str | None) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _content_disposition(filename: str) -> str:
    safe = _validated_filename(filename, code="invalid_download_filename")
    ascii_name = "".join(ch if 32 <= ord(ch) < 127 and ch not in {'"', '\\'} else "_" for ch in safe)
    ascii_name = ascii_name or "download.bin"
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(safe, safe='')}"


class StoredFileLinkTrackingStore(LinkTrackingStore):
    """Backward-compatible extension of tracking_links for opaque stored-file targets."""

    def _init_schema(self) -> None:
        super()._init_schema()
        additions = {
            "target_type": "TEXT NOT NULL DEFAULT 'url'",
            "stored_file_id": "TEXT",
            "download_filename": "TEXT NOT NULL DEFAULT ''",
            "download_media_type": "TEXT NOT NULL DEFAULT ''",
            "status": "TEXT NOT NULL DEFAULT 'active'",
            "expires_at": "TEXT",
            "revoked_at": "TEXT",
        }
        with self._connect() as conn:
            columns = {
                str(row["name"])
                for row in conn.execute("PRAGMA table_info(tracking_links)").fetchall()
            }
            for name, declaration in additions.items():
                if name not in columns:
                    conn.execute(f"ALTER TABLE tracking_links ADD COLUMN {name} {declaration}")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_tracking_links_target_type "
                "ON tracking_links(target_type, created_at)"
            )

    @staticmethod
    def _stored_link_id(
        campaign_id: str,
        position: int,
        stored_file_id: str,
        anchor_text: str,
    ) -> str:
        digest = hashlib.sha256(
            f"{campaign_id}\0{position}\0stored_file\0{stored_file_id}\0{anchor_text}".encode(
                "utf-8", "ignore"
            )
        ).hexdigest()[:20]
        return f"lnk_{digest}"

    def _insert_stored_file_link(
        self,
        *,
        delivery: dict[str, Any],
        file_info: dict[str, Any],
        position: int,
        anchor_text: str,
    ) -> dict[str, Any]:
        stored_file_id = str(file_info["id"])
        filename = _validated_filename(str(file_info["filename"]), code="invalid_download_filename")
        media_type = _validated_media_type(
            str(file_info["media_type"]), code="invalid_download_media_type"
        )
        logical_id = self._stored_link_id(
            str(delivery["campaign_id"]), position, stored_file_id, anchor_text
        )
        occurrence_id = f"lno_{_token(12)}"
        token = _token(24)
        now = _now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO tracking_links(
                    id,link_id,tracking_token,campaign_id,delivery_id,account_id,recipient,
                    original_url,normalized_url,destination_host,position,anchor_text,message_id,
                    created_at,target_type,stored_file_id,download_filename,download_media_type,
                    status,expires_at,revoked_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    occurrence_id,
                    logical_id,
                    token,
                    delivery["campaign_id"],
                    delivery["id"],
                    delivery["account_id"],
                    delivery["recipient"],
                    "",
                    "",
                    "",
                    int(position),
                    anchor_text,
                    str(delivery.get("message_id") or ""),
                    now,
                    "stored_file",
                    stored_file_id,
                    filename,
                    media_type,
                    "active",
                    None,
                    None,
                ),
            )
        return {
            "occurrence_id": occurrence_id,
            "link_id": logical_id,
            "tracking_token": token,
            "campaign_id": str(delivery["campaign_id"]),
            "delivery_id": str(delivery["id"]),
            "recipient": str(delivery["recipient"]),
            "target_type": "stored_file",
            "stored_file_id": stored_file_id,
            "download_filename": filename,
            "download_media_type": media_type,
            "position": int(position),
            "anchor_text": anchor_text,
            "message_id": str(delivery.get("message_id") or ""),
            "created_at": now,
        }

    @staticmethod
    def _safe_link_metadata(record: dict[str, Any], public_url: str) -> dict[str, Any]:
        safe = {
            key: value
            for key, value in record.items()
            if key not in {"tracking_token", "stored_file_id"}
        }
        safe["tracked_url_path"] = "/t/c/<token>"
        if safe.get("target_type") == "stored_file":
            safe["public_download"] = True
        return safe

    def instrument_html_with_shares(
        self,
        *,
        body_html: str,
        delivery: dict[str, Any],
        track_web_links: bool = True,
        stored_file_resolver: Callable[[str], dict[str, Any]] | None = None,
    ) -> tuple[str, list[dict[str, Any]], list[tuple[str, str]]]:
        html = body_html or ""
        anchors = collect_anchors(html)
        if not anchors:
            return html, [], []
        replacements: list[tuple[str, str]] = []
        tracked: list[dict[str, Any]] = []
        share_urls: list[tuple[str, str]] = []
        public_base = _safe_base_url()
        for anchor_index, anchor in enumerate(anchors):
            href = str(anchor.get("href") or "")
            stored_file_id = _stored_file_id_from_href(href)
            record: dict[str, Any] | None = None
            if stored_file_id is not None:
                if stored_file_resolver is None:
                    raise StoredFileMailError("stored_file_resolver_unavailable")
                file_info = stored_file_resolver(stored_file_id)
                record = self._insert_stored_file_link(
                    delivery=delivery,
                    file_info=file_info,
                    position=anchor_index,
                    anchor_text=str(anchor.get("anchor_text") or "")[:500],
                )
            elif track_web_links and eligible_web_url(href) and not already_tracked_url(href, public_base):
                record = self._insert_link(
                    delivery=delivery,
                    original_url=href,
                    position=anchor_index,
                    anchor_text=str(anchor.get("anchor_text") or "")[:500],
                )
                record["target_type"] = "url"
            if record is None:
                continue
            tracked_url = f"{public_base}/t/c/{record['tracking_token']}"
            replacements.append(
                (str(anchor["raw_tag"]), replace_href(str(anchor["raw_tag"]), tracked_url))
            )
            tracked.append(self._safe_link_metadata(record, tracked_url))
            if stored_file_id is not None:
                share_urls.append((stored_file_id, tracked_url))
        return rewrite_anchor_tags(html, replacements), tracked, share_urls

    def instrument_html(
        self,
        *,
        body_html: str,
        delivery: dict[str, Any],
    ) -> tuple[str, list[dict[str, Any]]]:
        rendered, tracked, _ = self.instrument_html_with_shares(
            body_html=body_html,
            delivery=delivery,
            track_web_links=True,
            stored_file_resolver=None,
        )
        return rendered, tracked

    @staticmethod
    def rewrite_stored_file_links_for_sent_copy(
        body_html: str,
        share_urls: list[tuple[str, str]],
    ) -> str:
        if not share_urls:
            return body_html
        queues: dict[str, list[str]] = {}
        for file_id, public_url in share_urls:
            queues.setdefault(file_id, []).append(public_url)
        replacements: list[tuple[str, str]] = []
        for anchor in collect_anchors(body_html or ""):
            file_id = _stored_file_id_from_href(str(anchor.get("href") or ""))
            if file_id is None or not queues.get(file_id):
                continue
            public_url = queues[file_id].pop(0)
            replacements.append(
                (str(anchor["raw_tag"]), replace_href(str(anchor["raw_tag"]), public_url))
            )
        return rewrite_anchor_tags(body_html or "", replacements)

    def _decorate_occurrences(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        occurrence_ids = [
            str(row.get("occurrence_id") or row.get("link_occurrence_id") or "")
            for row in rows
        ]
        occurrence_ids = [value for value in occurrence_ids if value]
        if not occurrence_ids:
            return rows
        placeholders = ",".join("?" for _ in occurrence_ids)
        with self._connect() as conn:
            meta_rows = conn.execute(
                f"""
                SELECT id,target_type,download_filename,download_media_type,status,
                       expires_at,revoked_at
                FROM tracking_links WHERE id IN ({placeholders})
                """,
                occurrence_ids,
            ).fetchall()
        metadata = {str(row["id"]): dict(row) for row in meta_rows}
        for row in rows:
            occurrence_id = str(row.get("occurrence_id") or row.get("link_occurrence_id") or "")
            meta = metadata.get(occurrence_id) or {}
            row["target_type"] = str(meta.get("target_type") or "url")
            if row["target_type"] == "stored_file":
                row["download_filename"] = str(meta.get("download_filename") or "")
                row["download_media_type"] = str(meta.get("download_media_type") or "")
                row["target_status"] = str(meta.get("status") or "active")
                row["expires_at"] = meta.get("expires_at")
                row["revoked_at"] = meta.get("revoked_at")
        return rows

    def list_links(self, **kwargs: Any) -> list[dict[str, Any]]:
        return self._decorate_occurrences(super().list_links(**kwargs))

    def list_click_events(self, **kwargs: Any) -> list[dict[str, Any]]:
        return self._decorate_occurrences(super().list_click_events(**kwargs))

    def top_links(self, **kwargs: Any) -> list[dict[str, Any]]:
        rows = super().top_links(**kwargs)
        logical_ids = [str(row.get("link_id") or "") for row in rows if row.get("link_id")]
        if not logical_ids:
            return rows
        placeholders = ",".join("?" for _ in logical_ids)
        with self._connect() as conn:
            meta_rows = conn.execute(
                f"SELECT link_id,MIN(target_type) AS target_type,MIN(download_filename) AS download_filename "
                f"FROM tracking_links WHERE link_id IN ({placeholders}) GROUP BY link_id",
                logical_ids,
            ).fetchall()
        metadata = {str(row["link_id"]): dict(row) for row in meta_rows}
        for row in rows:
            meta = metadata.get(str(row.get("link_id") or "")) or {}
            row["target_type"] = str(meta.get("target_type") or "url")
            if row["target_type"] == "stored_file":
                row["download_filename"] = str(meta.get("download_filename") or "")
        return rows

    def status(self) -> dict[str, Any]:
        status = super().status()
        with self._connect() as conn:
            stored = conn.execute(
                "SELECT COUNT(*) FROM tracking_links WHERE target_type='stored_file'"
            ).fetchone()[0]
        status.update(
            {
                "stored_file_targets": int(stored),
                "stored_file_public_downloads": True,
                "public_file_path": "/t/c/*",
                "stored_file_id_exposed_in_public_url": False,
            }
        )
        return status


@lru_cache(maxsize=1)
def stored_file_link_store() -> StoredFileLinkTrackingStore:
    return StoredFileLinkTrackingStore(analytics_store())


class PostmasterV946MailClient(LinkTrackingMailClient):
    """v9.4.6 delivery client: canonical FileStore attachments plus opaque file links."""

    def __init__(
        self,
        settings: Any,
        *,
        file_store: FileStore | None = None,
        file_authorizer: Callable[[dict[str, Any]], bool | None] | None = None,
        analytics: EmailAnalyticsStore | None = None,
        tracking_store: StoredFileLinkTrackingStore | None = None,
    ) -> None:
        super().__init__(settings)
        self._stored_file_store = file_store or FileStore()
        self._stored_file_authorizer = file_authorizer
        self._v946_analytics = analytics
        self._v946_tracking_store = tracking_store

    def _analytics_store(self) -> EmailAnalyticsStore:
        return self._v946_analytics or analytics_store()

    def _link_store(self) -> StoredFileLinkTrackingStore:
        return self._v946_tracking_store or stored_file_link_store()

    @staticmethod
    def _map_file_store_error(exc: FileStoreError) -> StoredFileMailError:
        text = str(exc).lower()
        if "not found" in text:
            return StoredFileMailError("stored_file_not_found")
        if "missing from disk" in text:
            return StoredFileMailError("stored_file_blob_missing")
        if "integrity" in text or "size verification" in text:
            return StoredFileMailError("stored_file_blob_invalid")
        return StoredFileMailError("stored_file_unavailable")

    def _authorize_file(self, info: dict[str, Any]) -> None:
        if self._stored_file_authorizer is None:
            return
        try:
            allowed = self._stored_file_authorizer(info)
        except StoredFileMailError:
            raise
        except Exception as exc:
            raise StoredFileMailError("stored_file_not_authorized") from exc
        if allowed is False:
            raise StoredFileMailError("stored_file_not_authorized")

    def _resolve_stored_file(
        self,
        file_id: str,
        *,
        include_bytes: bool,
    ) -> tuple[dict[str, Any], bytes | None]:
        file_id = str(file_id or "").strip()
        if not file_id:
            raise StoredFileMailError("stored_file_not_found")
        try:
            info = self._stored_file_store.get_info(file_id)
        except FileStoreError as exc:
            raise self._map_file_store_error(exc) from exc
        self._authorize_file(info)
        try:
            if include_bytes:
                verified_info, blob = self._stored_file_store.raw_bytes(file_id)
                return verified_info, blob
            verified_info, _ = self._stored_file_store.resolve_blob(file_id)
            return verified_info, None
        except FileStoreError as exc:
            raise self._map_file_store_error(exc) from exc

    def _resolve_stored_file_share(self, file_id: str) -> dict[str, Any]:
        info, _ = self._resolve_stored_file(file_id, include_bytes=False)
        _validated_filename(str(info.get("filename") or ""), code="invalid_download_filename")
        _validated_media_type(str(info.get("media_type") or ""), code="invalid_download_media_type")
        return info

    def _decode_attachment_specs(
        self,
        attachments: list[dict[str, Any]] | None,
    ) -> list[dict[str, Any]]:
        if not attachments:
            return []
        decoded: list[dict[str, Any]] = []
        total = 0
        stored_seen = False
        for spec in attachments:
            if not isinstance(spec, dict):
                raise MailBridgeError("Each attachment must be an object")
            has_file_id = "file_id" in spec
            if has_file_id:
                competing = bool(spec.get("content_base64")) or bool(
                    spec.get("source_mailbox") or spec.get("source_uid")
                )
                if competing:
                    raise StoredFileMailError("attachment_source_conflict")
                stored_seen = True
                file_id = str(spec.get("file_id") or "").strip()
                info, payload = self._resolve_stored_file(file_id, include_bytes=True)
                blob = payload or b""
                filename = _validated_filename(
                    str(spec.get("filename") or info.get("filename") or "")
                )
                media_type = _validated_media_type(
                    str(
                        spec.get("media_type")
                        or spec.get("content_type")
                        or info.get("media_type")
                        or ""
                    )
                )
                if len(blob) > self.max_attachment_bytes:
                    raise StoredFileMailError("attachment_size_limit_exceeded")
                maintype, subtype = media_type.split("/", 1)
                item = {
                    "filename": filename,
                    "blob": blob,
                    "content_type": media_type,
                    "maintype": maintype,
                    "subtype": subtype,
                    "size": len(blob),
                }
            else:
                legacy = super()._decode_attachment_specs([spec])
                if not legacy:
                    continue
                item = legacy[0]
            total += int(item["size"])
            if total > self.max_attachment_bytes:
                if stored_seen:
                    raise StoredFileMailError("attachment_size_limit_exceeded")
                raise MailBridgeError(
                    f"Total attachment size exceeds limit {self.max_attachment_bytes} bytes"
                )
            decoded.append(item)
        return decoded

    def send_email(
        self,
        *,
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
    ) -> dict[str, Any]:
        share_requested = has_stored_file_links(body_html)
        resolved_track = self._resolve_track_opens(track_opens)
        if share_requested and not resolved_track and not body_amp:
            return self._send_individualized(
                to=to,
                subject=subject,
                body=body,
                cc=cc,
                bcc=bcc,
                body_html=body_html,
                body_amp=None,
                attachments=attachments,
                track_opens=False,
                campaign_id=campaign_id,
            )
        return super().send_email(
            to=to,
            subject=subject,
            body=body,
            cc=cc,
            bcc=bcc,
            body_html=body_html,
            body_amp=body_amp,
            attachments=attachments,
            track_opens=track_opens,
            campaign_id=campaign_id,
        )

    def _send_threaded(self, **kwargs: Any) -> dict[str, Any]:
        body_html = kwargs.get("body_html")
        track = self._resolve_track_opens(kwargs.get("track_opens"))
        if not has_stored_file_links(body_html) or track:
            return super()._send_threaded(**kwargs)

        mode = kwargs["mode"]
        mailbox = kwargs["mailbox"]
        uid = kwargs["uid"]
        resolved = self.resolve_thread_recipients(mailbox, uid, mode=mode)
        identities = sender_identity_addresses(self.settings)
        cc_clean = merge_thread_cc(
            resolved["to"],
            resolved["cc"],
            kwargs.get("cc"),
            sender_identities=identities,
        )
        result = self._send_individualized(
            to=resolved["to"],
            cc=cc_clean,
            bcc=kwargs.get("bcc"),
            subject=resolved["subject"],
            body=kwargs.get("body", ""),
            body_html=body_html,
            body_amp=None,
            attachments=kwargs.get("attachments"),
            track_opens=False,
            campaign_id=kwargs.get("campaign_id"),
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

    def _send_individualized(
        self,
        *,
        to: list[str],
        subject: str,
        body: str = "",
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
        body_html: str | None = None,
        body_amp: str | None = None,
        attachments: list[dict[str, Any]] | None = None,
        track_opens: bool,
        campaign_id: str | None = None,
        in_reply_to: str = "",
        references: str = "",
    ) -> dict[str, Any]:
        amp_used = bool(body_amp)
        to_clean = self._validate_recipients(to)
        cc_clean = self._validate_recipients(cc or []) if cc else []
        bcc_clean = self._validate_recipients(bcc or []) if bcc else []
        recipient_roles: list[tuple[str, str]] = []
        seen: set[str] = set()
        for role, addresses in (("to", to_clean), ("cc", cc_clean), ("bcc", bcc_clean)):
            for address in addresses:
                key = address.lower()
                if key not in seen:
                    seen.add(key)
                    recipient_roles.append((address, role))

        analytics = self._analytics_store()
        links = self._link_store()
        base_html = body_html if body_html is not None else ""
        stored_links = has_stored_file_links(base_html)
        if track_opens or amp_used or stored_links:
            analytics.validate_public_base_url()
        campaign = analytics.create_campaign(
            account_id=getattr(self.settings, "account_id", "") or self.settings.email_address,
            sender=self.settings.email_address,
            subject=subject.strip(),
            track_opens=track_opens,
            amp_used=amp_used,
            campaign_id=campaign_id,
        )
        if body_html is None:
            from .mail_extensions import _plain_to_html

            base_html = _plain_to_html(body)

        delivery_results: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        attachment_meta: list[dict[str, Any]] = []
        for recipient, role in recipient_roles:
            delivery = analytics.create_delivery(
                campaign_id=campaign["id"],
                account_id=getattr(self.settings, "account_id", "") or self.settings.email_address,
                recipient=recipient,
                recipient_role=role,
            )
            recipient_html, recipient_amp = analytics.render_for_recipient(
                body_html=base_html,
                body_amp=body_amp,
                delivery=delivery,
                track_opens=track_opens,
            )
            clean_html = _sent_clean_html(base_html, delivery)
            link_meta: list[dict[str, Any]] = []
            if track_opens or stored_links:
                recipient_html, link_meta, share_urls = links.instrument_html_with_shares(
                    body_html=recipient_html,
                    delivery=delivery,
                    track_web_links=track_opens,
                    stored_file_resolver=self._resolve_stored_file_share,
                )
                clean_html = links.rewrite_stored_file_links_for_sent_copy(
                    clean_html,
                    share_urls,
                )
            try:
                outbound, _, meta = self._build_message(
                    to=to_clean,
                    cc=cc_clean,
                    subject=subject,
                    body=body,
                    body_html=recipient_html,
                    body_amp=recipient_amp,
                    attachments=attachments,
                    allow_unlisted=False,
                    in_reply_to=in_reply_to,
                    references=references,
                )
                sent_copy, _, _ = self._build_message(
                    to=to_clean,
                    cc=cc_clean,
                    subject=subject,
                    body=body,
                    body_html=clean_html,
                    body_amp=None,
                    attachments=attachments,
                    allow_unlisted=False,
                    in_reply_to=in_reply_to,
                    references=references,
                )
                result = self._send_message_with_clean_sent(outbound, sent_copy, [recipient])
                analytics.mark_sent(delivery["id"], str(result.get("message_id", "")))
                links.mark_delivery_message(delivery["id"], str(result.get("message_id", "")))
                attachment_meta = meta
                delivery_results.append(
                    {
                        "delivery_id": delivery["id"],
                        "recipient": recipient,
                        "role": role,
                        "message_id": result.get("message_id", ""),
                        "sent_copy_saved": result.get("sent_copy_saved", False),
                        "sent_copy_tracking_sanitized": True,
                        "link_tracking": bool(track_opens or stored_links),
                        "stored_file_download_links": bool(stored_links),
                        "links": link_meta,
                    }
                )
            except Exception as exc:
                errors.append(
                    {
                        "delivery_id": delivery["id"],
                        "recipient": recipient,
                        "role": role,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
        return {
            "sent": bool(delivery_results) and not errors,
            "partial": bool(delivery_results) and bool(errors),
            "from": self.settings.email_address,
            "subject": subject.strip(),
            "campaign_id": campaign["id"],
            "individualized": True,
            "visible_recipient_headers_preserved": True,
            "tracked": bool(track_opens),
            "link_tracking": bool(track_opens or stored_links),
            "stored_file_download_links": bool(stored_links),
            "sent_copy_tracking_sanitized": True,
            "amp": amp_used,
            "amp_registered": bool(getattr(self.settings, "amp_registered", False)),
            "deliveries": delivery_results,
            "errors": errors,
            "attachments": attachment_meta,
            "tracking_note": (
                "Open/link/download events are fetch telemetry and may be affected by mail proxies, "
                "security scanners or prefetching; provider classification remains query-time."
            )
            if track_opens or stored_links
            else "",
        }


async def public_tracking_target(
    request: Request,
    *,
    tracking_store: StoredFileLinkTrackingStore,
    file_store: FileStore,
    logger: Any,
) -> Response:
    token = str(request.path_params.get("token", "")).strip()
    try:
        link = tracking_store.get_by_token(token)
        target_type = str(link.get("target_type") or "url")
        if target_type == "stored_file":
            if str(link.get("status") or "active") != "active" or link.get("revoked_at"):
                return _public_not_found()
            expires_at = _parse_utc(link.get("expires_at"))
            if expires_at is not None and expires_at <= datetime.now(timezone.utc):
                return _public_not_found()
            stored_file_id = str(link.get("stored_file_id") or "")
            if not stored_file_id:
                return _public_not_found()
        elif target_type == "url":
            destination = str(link.get("original_url") or "")
            if not eligible_web_url(destination):
                return _public_not_found()
        else:
            return _public_not_found()
    except Exception:
        return _public_not_found()

    forwarded = request.headers.get("x-forwarded-for", "")
    client_ip = forwarded.split(",", 1)[0].strip() if forwarded else ""
    if not client_ip and request.client:
        client_ip = request.client.host or ""
    try:
        tracking_store.record_click(
            link,
            user_agent=request.headers.get("user-agent", ""),
            client_ip=client_ip,
            country_code=request.headers.get("cf-ipcountry", ""),
        )
    except Exception:
        logger.info("Tracking event could not be recorded", exc_info=True)

    if target_type == "url":
        response = RedirectResponse(destination, status_code=302)
        response.headers["Cache-Control"] = "private, no-store, no-cache, max-age=0"
        response.headers["Pragma"] = "no-cache"
        return response

    try:
        info, blob = file_store.raw_bytes(stored_file_id)
        filename = _validated_filename(
            str(link.get("download_filename") or info.get("filename") or ""),
            code="invalid_download_filename",
        )
        media_type = _validated_media_type(
            str(link.get("download_media_type") or info.get("media_type") or ""),
            code="invalid_download_media_type",
        )
    except Exception:
        return _public_not_found()

    return Response(
        content=blob,
        media_type=media_type,
        headers={
            "Content-Disposition": _content_disposition(filename),
            "Cache-Control": "private, no-store, no-cache, max-age=0",
            "Pragma": "no-cache",
            "X-Content-Type-Options": "nosniff",
        },
    )
