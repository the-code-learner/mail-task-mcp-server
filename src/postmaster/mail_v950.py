from __future__ import annotations

import contextlib
import contextvars
import hashlib
import imaplib
import os
import smtplib
import ssl
import threading
import time
from datetime import datetime, timedelta, timezone
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import format_datetime, make_msgid
from typing import Any, Callable
from urllib.parse import urlparse

from .delivery_reliability import (
    ReliabilityStore,
    RetryPolicy,
    ThrottleController,
    classify_smtp_failure,
)
from .email_analytics import EmailAnalyticsStore, analytics_store
from .mail_bridge import MailBridgeError, _message_to_dict
from .mail_health import DNSHealthChecker, socket_tls_info
from .mail_protocols import (
    build_dsn_options,
    message_diagnostics,
    parse_imap_capabilities,
    parse_imap_quota,
    parse_smtp_capabilities,
)
from .stored_file_delivery import PostmasterV946MailClient
from .tracked_mail import _synchronize_transport_headers


_NEWSLETTER = contextvars.ContextVar("postmaster_v950_newsletter", default=None)
_SEND_OPTIONS = contextvars.ContextVar("postmaster_v950_send_options", default=None)
_DELIVERY_ID = contextvars.ContextVar("postmaster_v950_delivery_id", default="")


class _SMTPAttemptFailure(RuntimeError):
    def __init__(self, original: BaseException, phase: str, capabilities: dict[str, Any] | None = None):
        super().__init__(str(original))
        self.original = original
        self.phase = phase
        self.capabilities = capabilities or {}


class _DeliveryAwareAnalytics:
    def __init__(self, wrapped: EmailAnalyticsStore):
        self._wrapped = wrapped

    def __getattr__(self, name: str):
        return getattr(self._wrapped, name)

    def create_delivery(self, **kwargs: Any) -> dict[str, Any]:
        delivery = self._wrapped.create_delivery(**kwargs)
        _DELIVERY_ID.set(str(delivery.get("id") or ""))
        return delivery


def _iso_after(seconds: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=max(0.0, seconds))).isoformat()


def _valid_unsubscribe_config(
    *,
    newsletter_mode: bool,
    unsubscribe_url: str | None,
    unsubscribe_email: str | None,
    one_click_unsubscribe: bool,
) -> dict[str, Any] | None:
    url = (unsubscribe_url or "").strip()
    email = (unsubscribe_email or "").strip()
    if not newsletter_mode:
        if url or email or one_click_unsubscribe:
            raise MailBridgeError(
                "unsubscribe settings require newsletter_mode=true; tracking never enables newsletter mode automatically"
            )
        return None
    if not url and not email:
        raise MailBridgeError("newsletter_mode requires unsubscribe_url or unsubscribe_email")
    for value in (url, email):
        if "\r" in value or "\n" in value:
            raise MailBridgeError("Invalid unsubscribe value")
    if url:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise MailBridgeError("unsubscribe_url must be an absolute HTTP(S) URL")
    if email:
        raw = email[7:] if email.lower().startswith("mailto:") else email
        if "@" not in raw or any(char in raw for char in " <>\t"):
            raise MailBridgeError("unsubscribe_email must be an email address or mailto: URI")
        email = "mailto:" + raw
    if one_click_unsubscribe:
        if not url or urlparse(url).scheme != "https":
            raise MailBridgeError("one_click_unsubscribe requires an HTTPS unsubscribe_url")
    return {
        "url": url,
        "email": email,
        "one_click": bool(one_click_unsubscribe),
    }


class PostmasterV950MailClient(PostmasterV946MailClient):
    """Standards-first v9.5 mail client layered on the v9.4.6 delivery pipeline."""

    _health_cache: dict[tuple[str, str, bool], tuple[float, dict[str, Any]]] = {}
    _health_lock = threading.RLock()
    _inbound_highwater: dict[str, int] = {}
    _inbound_lock = threading.RLock()

    def __init__(
        self,
        settings: Any,
        *,
        reliability: ReliabilityStore | None = None,
        throttle: ThrottleController | None = None,
        retry_policy: RetryPolicy | None = None,
        dns_checker: DNSHealthChecker | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        **kwargs: Any,
    ) -> None:
        super().__init__(settings, **kwargs)
        self.reliability = reliability or ReliabilityStore()
        self.throttle = throttle or ThrottleController()
        self.retry_policy = retry_policy or RetryPolicy.from_env()
        self.dns_checker = dns_checker or DNSHealthChecker(
            timeout=float(os.getenv("MAIL_HEALTH_DNS_TIMEOUT_SECONDS", "5"))
        )
        self.sleeper = sleeper

    def _analytics_store(self) -> EmailAnalyticsStore:
        return _DeliveryAwareAnalytics(super()._analytics_store())  # type: ignore[return-value]

    @contextlib.contextmanager
    def _delivery_options(
        self,
        *,
        newsletter_mode: bool = False,
        unsubscribe_url: str | None = None,
        unsubscribe_email: str | None = None,
        one_click_unsubscribe: bool = False,
        dsn_notify_success: bool = False,
    ):
        newsletter = _valid_unsubscribe_config(
            newsletter_mode=newsletter_mode,
            unsubscribe_url=unsubscribe_url,
            unsubscribe_email=unsubscribe_email,
            one_click_unsubscribe=one_click_unsubscribe,
        )
        token_newsletter = _NEWSLETTER.set(newsletter)
        token_send = _SEND_OPTIONS.set({"dsn_notify_success": bool(dsn_notify_success)})
        token_delivery = _DELIVERY_ID.set("")
        try:
            yield
        finally:
            _DELIVERY_ID.reset(token_delivery)
            _SEND_OPTIONS.reset(token_send)
            _NEWSLETTER.reset(token_newsletter)

    def _build_message(self, **kwargs: Any):
        msg, recipients, meta = super()._build_message(**kwargs)
        newsletter = _NEWSLETTER.get()
        if newsletter:
            values: list[str] = []
            if newsletter.get("email"):
                values.append(f"<{newsletter['email']}>")
            if newsletter.get("url"):
                values.append(f"<{newsletter['url']}>")
            msg["List-Unsubscribe"] = ", ".join(values)
            if newsletter.get("one_click"):
                msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
        return msg, recipients, meta

    def send_email(
        self,
        *,
        newsletter_mode: bool = False,
        unsubscribe_url: str | None = None,
        unsubscribe_email: str | None = None,
        one_click_unsubscribe: bool = False,
        dsn_notify_success: bool = False,
        **kwargs: Any,
    ) -> dict[str, Any]:
        with self._delivery_options(
            newsletter_mode=newsletter_mode,
            unsubscribe_url=unsubscribe_url,
            unsubscribe_email=unsubscribe_email,
            one_click_unsubscribe=one_click_unsubscribe,
            dsn_notify_success=dsn_notify_success,
        ):
            result = super().send_email(**kwargs)
        result["newsletter_mode"] = bool(newsletter_mode)
        result["dsn_notify_success_requested"] = bool(dsn_notify_success)
        return result

    def reply_email(self, *, newsletter_mode: bool = False, unsubscribe_url: str | None = None, unsubscribe_email: str | None = None, one_click_unsubscribe: bool = False, dsn_notify_success: bool = False, **kwargs: Any) -> dict[str, Any]:
        with self._delivery_options(newsletter_mode=newsletter_mode, unsubscribe_url=unsubscribe_url, unsubscribe_email=unsubscribe_email, one_click_unsubscribe=one_click_unsubscribe, dsn_notify_success=dsn_notify_success):
            result = super().reply_email(**kwargs)
        result["newsletter_mode"] = bool(newsletter_mode)
        return result

    def follow_up_email(self, *, newsletter_mode: bool = False, unsubscribe_url: str | None = None, unsubscribe_email: str | None = None, one_click_unsubscribe: bool = False, dsn_notify_success: bool = False, **kwargs: Any) -> dict[str, Any]:
        with self._delivery_options(newsletter_mode=newsletter_mode, unsubscribe_url=unsubscribe_url, unsubscribe_email=unsubscribe_email, one_click_unsubscribe=one_click_unsubscribe, dsn_notify_success=dsn_notify_success):
            result = super().follow_up_email(**kwargs)
        result["newsletter_mode"] = bool(newsletter_mode)
        return result

    def create_draft(self, *, newsletter_mode: bool = False, unsubscribe_url: str | None = None, unsubscribe_email: str | None = None, one_click_unsubscribe: bool = False, **kwargs: Any) -> dict[str, Any]:
        with self._delivery_options(newsletter_mode=newsletter_mode, unsubscribe_url=unsubscribe_url, unsubscribe_email=unsubscribe_email, one_click_unsubscribe=one_click_unsubscribe):
            result = super().create_draft(**kwargs)
        result["newsletter_mode"] = bool(newsletter_mode)
        return result

    def create_reply_draft(self, *, newsletter_mode: bool = False, unsubscribe_url: str | None = None, unsubscribe_email: str | None = None, one_click_unsubscribe: bool = False, **kwargs: Any) -> dict[str, Any]:
        with self._delivery_options(newsletter_mode=newsletter_mode, unsubscribe_url=unsubscribe_url, unsubscribe_email=unsubscribe_email, one_click_unsubscribe=one_click_unsubscribe):
            result = super().create_reply_draft(**kwargs)
        result["newsletter_mode"] = bool(newsletter_mode)
        return result

    def create_follow_up_draft(self, *, newsletter_mode: bool = False, unsubscribe_url: str | None = None, unsubscribe_email: str | None = None, one_click_unsubscribe: bool = False, **kwargs: Any) -> dict[str, Any]:
        with self._delivery_options(newsletter_mode=newsletter_mode, unsubscribe_url=unsubscribe_url, unsubscribe_email=unsubscribe_email, one_click_unsubscribe=one_click_unsubscribe):
            result = super().create_follow_up_draft(**kwargs)
        result["newsletter_mode"] = bool(newsletter_mode)
        return result

    @staticmethod
    def _needs_smtputf8(msg: EmailMessage, recipients: list[str], sender: str) -> bool:
        try:
            (sender + "".join(recipients)).encode("ascii")
            for header in ("From", "To", "Cc", "Subject"):
                str(msg.get(header, "")).encode("ascii")
            return False
        except UnicodeEncodeError:
            return True

    def _smtp_connect(self):
        context = ssl.create_default_context()
        security = (
            self.settings.smtp_security
            or ("starttls" if self.settings.smtp_starttls else "ssl")
        ).strip().lower()
        if security == "ssl":
            smtp = smtplib.SMTP_SSL(
                self.settings.smtp_host,
                self.settings.smtp_port,
                timeout=30,
                context=context,
            )
        elif security in {"starttls", "plain"}:
            smtp = smtplib.SMTP(self.settings.smtp_host, self.settings.smtp_port, timeout=30)
        else:
            raise MailBridgeError(f"Unsupported SMTP security mode: {security}")
        smtp.ehlo()
        if security == "starttls":
            smtp.starttls(context=context)
            smtp.ehlo()
        return smtp, security

    def _smtp_send_once(self, msg: EmailMessage, recipients: list[str], delivery_id: str) -> dict[str, Any]:
        smtp = None
        phase = "connect"
        capabilities: dict[str, Any] = {}
        try:
            smtp, security = self._smtp_connect()
            capabilities = parse_smtp_capabilities(getattr(smtp, "esmtp_features", {}))
            username = self.settings.smtp_username or self.settings.email_address
            password = self.settings.smtp_password or self.settings.email_password
            phase = "auth"
            smtp.login(username, password)
            mail_options: list[str] = []
            if self._needs_smtputf8(msg, recipients, self.settings.email_address):
                if not capabilities["smtputf8"]:
                    raise smtplib.SMTPNotSupportedError("SMTPUTF8 is required but not advertised by the server")
                mail_options.append("SMTPUTF8")
            notify_success = bool((_SEND_OPTIONS.get() or {}).get("dsn_notify_success"))
            dsn_enabled = bool(capabilities["dsn"])
            if dsn_enabled:
                dsn_mail_options, _ = build_dsn_options(
                    envelope_id=delivery_id,
                    recipient=recipients[0],
                    notify_success=notify_success,
                )
                mail_options.extend(dsn_mail_options)
            phase = "mail_from"
            code, response = smtp.mail(self.settings.email_address, options=mail_options)
            if code >= 400:
                raise smtplib.SMTPSenderRefused(code, response, self.settings.email_address)
            refused: dict[str, tuple[int, bytes]] = {}
            for recipient in recipients:
                rcpt_options: list[str] = []
                if dsn_enabled:
                    _, rcpt_options = build_dsn_options(
                        envelope_id=delivery_id,
                        recipient=recipient,
                        notify_success=notify_success,
                    )
                phase = "rcpt_to"
                rcpt_code, rcpt_response = smtp.rcpt(recipient, options=rcpt_options)
                if rcpt_code >= 400:
                    refused[recipient] = (rcpt_code, rcpt_response)
            if refused:
                with contextlib.suppress(Exception):
                    smtp.rset()
                raise smtplib.SMTPRecipientsRefused(refused)
            phase = "data_waiting"
            data_code, data_response = smtp.data(msg.as_bytes(policy=policy.SMTP))
            phase = "data_response"
            if data_code >= 400:
                raise smtplib.SMTPDataError(data_code, data_response)
            return {
                "dsn_supported": dsn_enabled,
                "dsn_envid": delivery_id if dsn_enabled and delivery_id else "",
                "dsn_notify": "FAILURE,DELAY,SUCCESS" if dsn_enabled and notify_success else ("FAILURE,DELAY" if dsn_enabled else ""),
                "smtp_capabilities": capabilities,
                "smtp_security": security,
                "smtp_tls": socket_tls_info(
                    getattr(smtp, "sock", None),
                    hostname=self.settings.smtp_host,
                    implicit_tls=security == "ssl",
                    starttls=security == "starttls",
                ),
            }
        except Exception as exc:
            raise _SMTPAttemptFailure(exc, phase, capabilities) from exc
        finally:
            if smtp is not None:
                with contextlib.suppress(Exception):
                    smtp.quit()
                with contextlib.suppress(Exception):
                    smtp.close()

    def _save_sent_copy(self, msg: EmailMessage) -> tuple[bool, str | None]:
        if not self.settings.save_sent_copy:
            return False, None
        try:
            with self._imap() as conn:
                typ, _ = conn.append(
                    self.settings.sent_mailbox,
                    r"\Seen",
                    imaplib.Time2Internaldate(datetime.now().timestamp()),
                    msg.as_bytes(policy=policy.SMTP),
                )
                if typ == "OK":
                    return True, None
                return False, "IMAP APPEND returned non-OK"
        except Exception as exc:
            return False, type(exc).__name__

    def _reliable_transport(self, outbound: EmailMessage, recipients: list[str], *, sent_copy: EmailMessage | None = None) -> dict[str, Any]:
        if not self.settings.enable_send:
            raise MailBridgeError(
                "Sending is disabled. Set ENABLE_SEND=true only when you are ready to allow SMTP writes."
            )
        if sent_copy is not None:
            _synchronize_transport_headers(outbound, sent_copy, self.settings.email_address)
        else:
            if "Date" not in outbound:
                outbound["Date"] = format_datetime(datetime.now().astimezone())
            if "Message-ID" not in outbound:
                domain = self.settings.email_address.rsplit("@", 1)[-1]
                outbound["Message-ID"] = make_msgid(domain=domain)
        delivery_id = str(_DELIVERY_ID.get() or "")
        blocked = self.reliability.blocked_recipients(recipients)
        if blocked:
            summary = ", ".join(
                f"{row['recipient']} ({row['reason']})" for row in blocked
            )
            raise MailBridgeError(f"Send blocked by local suppression list: {summary}")
        message_id = str(outbound.get("Message-ID", ""))
        operation_seed = delivery_id or (message_id + "\n" + "\n".join(sorted(x.lower() for x in recipients)))
        operation_id = delivery_id or "smtp_" + hashlib.sha256(operation_seed.encode("utf-8")).hexdigest()[:24]
        idempotency_key = hashlib.sha256((operation_seed + self.settings.email_address).encode("utf-8")).hexdigest()
        if self.reliability.operation_sent(operation_id, message_id):
            return {
                "sent": True,
                "from": self.settings.email_address,
                "to": recipients,
                "subject": str(outbound.get("Subject", "")),
                "message_id": message_id,
                "idempotent_replay": True,
                "delivery_state": "sent",
                "attempt_count": 0,
                "sent_copy_saved": False,
                "sent_copy_error": None,
            }
        total_throttle = 0.0
        last_failure: dict[str, Any] | None = None
        smtp_meta: dict[str, Any] = {}
        for attempt in range(1, self.retry_policy.max_attempts + 1):
            total_throttle += self.throttle.acquire(
                getattr(self.settings, "account_id", "") or self.settings.email_address,
                recipients,
            )
            try:
                smtp_meta = self._smtp_send_once(outbound, recipients, delivery_id)
                self.reliability.record_attempt(
                    operation_id=operation_id,
                    delivery_id=delivery_id,
                    account_id=getattr(self.settings, "account_id", "") or self.settings.email_address,
                    recipient=recipients[0] if len(recipients) == 1 else ",".join(recipients),
                    attempt_number=attempt,
                    state="sent",
                    message_id=message_id,
                    idempotency_key=idempotency_key,
                )
                break
            except _SMTPAttemptFailure as wrapped:
                failure = classify_smtp_failure(wrapped.original, phase=wrapped.phase)
                last_failure = failure
                can_retry = bool(failure["temporary"]) and attempt < self.retry_policy.max_attempts
                delay = self.retry_policy.delay_for(attempt) if can_retry else 0.0
                next_retry = _iso_after(delay) if can_retry else ""
                state = "temporarily_failed" if failure["temporary"] else (
                    "permanently_failed" if failure["permanent"] else "delivery_uncertain"
                )
                self.reliability.record_attempt(
                    operation_id=operation_id,
                    delivery_id=delivery_id,
                    account_id=getattr(self.settings, "account_id", "") or self.settings.email_address,
                    recipient=recipients[0] if len(recipients) == 1 else ",".join(recipients),
                    attempt_number=attempt,
                    state=state,
                    message_id=message_id,
                    idempotency_key=idempotency_key,
                    classification=str(failure["classification"]),
                    smtp_code=failure.get("smtp_code"),
                    detail=str(failure["detail"]),
                    phase=str(failure["phase"]),
                    next_retry_at=next_retry,
                )
                if not can_retry:
                    raise MailBridgeError(
                        f"SMTP {failure['classification']} after {attempt} attempt(s): {failure['detail']}"
                    ) from wrapped.original
                self.sleeper(delay)
        else:  # pragma: no cover - loop always exits by success/raise
            raise MailBridgeError("SMTP send did not complete")
        if hasattr(self, "_sent_addresses_cache"):
            delattr(self, "_sent_addresses_cache")
        saved, save_error = self._save_sent_copy(sent_copy or outbound)
        result = {
            "sent": True,
            "from": self.settings.email_address,
            "to": recipients,
            "subject": str(outbound.get("Subject", "")),
            "message_id": message_id,
            "sent_copy_saved": saved,
            "sent_copy_error": save_error,
            "delivery_state": "sent",
            "attempt_count": len(self.reliability.list_attempts(delivery_id=delivery_id, limit=50)) if delivery_id else None,
            "throttled_seconds": round(total_throttle, 6),
            "last_error_classification": last_failure["classification"] if last_failure else "",
            "idempotency_key_recorded": True,
        }
        result.update(smtp_meta)
        return result

    def _send_message(self, msg: EmailMessage, recipients: list[str]) -> dict[str, Any]:
        return self._reliable_transport(msg, recipients)

    def _send_message_with_clean_sent(self, outbound: EmailMessage, sent_copy: EmailMessage, recipients: list[str]) -> dict[str, Any]:
        result = self._reliable_transport(outbound, recipients, sent_copy=sent_copy)
        result["sent_copy_tracking_sanitized"] = True
        return result

    def _imap_health(self) -> dict[str, Any]:
        started = time.perf_counter()
        with self._imap() as conn:
            latency_ms = round((time.perf_counter() - started) * 1000.0, 2)
            caps = parse_imap_capabilities(getattr(conn, "capabilities", ()))
            security = (self.settings.imap_security or "ssl").strip().lower()
            tls = socket_tls_info(
                getattr(conn, "sock", None),
                hostname=self.settings.imap_host,
                implicit_tls=security == "ssl",
                starttls=security == "starttls",
            )
            quota = {"supported": False, "resources": []}
            quota_error = None
            if caps["quota"]:
                try:
                    typ, data = conn.getquotaroot(self.settings.inbox_mailbox)
                    if typ == "OK":
                        quota = parse_imap_quota(data)
                        quota["supported"] = True
                    else:
                        quota_error = "GETQUOTAROOT returned non-OK"
                except Exception as exc:
                    quota_error = f"{type(exc).__name__}: {exc}"
            if quota_error:
                quota["error"] = quota_error
            return {
                "ok": True,
                "host": self.settings.imap_host,
                "port": self.settings.imap_port,
                "security": security,
                "connection_and_auth_latency_ms": latency_ms,
                "capabilities": caps,
                "quota": quota,
                "tls": tls,
            }

    def _smtp_health(self) -> dict[str, Any]:
        started = time.perf_counter()
        smtp = None
        try:
            smtp, security = self._smtp_connect()
            connected_ms = round((time.perf_counter() - started) * 1000.0, 2)
            caps = parse_smtp_capabilities(getattr(smtp, "esmtp_features", {}))
            auth_started = time.perf_counter()
            smtp.login(
                self.settings.smtp_username or self.settings.email_address,
                self.settings.smtp_password or self.settings.email_password,
            )
            auth_ms = round((time.perf_counter() - auth_started) * 1000.0, 2)
            tls = socket_tls_info(
                getattr(smtp, "sock", None),
                hostname=self.settings.smtp_host,
                implicit_tls=security == "ssl",
                starttls=security == "starttls",
            )
            warnings = []
            if caps["auth"]["supported"] and security == "plain":
                warnings.append("SMTP AUTH is advertised/used without TLS protection")
            return {
                "ok": True,
                "host": self.settings.smtp_host,
                "port": self.settings.smtp_port,
                "security": security,
                "connection_and_tls_latency_ms": connected_ms,
                "auth_latency_ms": auth_ms,
                "capabilities": caps,
                "tls": tls,
                "warnings": warnings,
            }
        finally:
            if smtp is not None:
                with contextlib.suppress(Exception):
                    smtp.quit()
                with contextlib.suppress(Exception):
                    smtp.close()

    def mailbox_health(self, *, refresh: bool = False) -> dict[str, Any]:
        key = (getattr(self.settings, "account_id", "") or self.settings.email_address, "imap", False)
        ttl = max(1.0, float(os.getenv("MAIL_HEALTH_CACHE_TTL_SECONDS", "300")))
        now = time.monotonic()
        with self._health_lock:
            cached = self._health_cache.get(key)
            if cached and not refresh and now - cached[0] < ttl:
                result = dict(cached[1])
                result["cached"] = True
                return result
        result = self._imap_health()
        result.update({
            "account_id": getattr(self.settings, "account_id", ""),
            "account": self.settings.email_address,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "cached": False,
            "cache_ttl_seconds": ttl,
        })
        with self._health_lock:
            self._health_cache[key] = (now, dict(result))
        return result

    def test_connections(self, *, refresh: bool = False, dkim_selector: str | None = None, include_dns: bool = True) -> dict[str, Any]:
        key = (getattr(self.settings, "account_id", "") or self.settings.email_address, dkim_selector or "", bool(include_dns))
        ttl = max(1.0, float(os.getenv("MAIL_HEALTH_CACHE_TTL_SECONDS", "300")))
        now = time.monotonic()
        with self._health_lock:
            cached = self._health_cache.get(key)
            if cached and not refresh and now - cached[0] < ttl:
                result = dict(cached[1])
                result["cached"] = True
                return result
        result: dict[str, Any] = {
            "ok": True,
            "account_id": getattr(self.settings, "account_id", ""),
            "account": self.settings.email_address,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "cached": False,
            "cache_ttl_seconds": ttl,
        }
        errors: list[dict[str, str]] = []
        try:
            result["imap"] = self._imap_health()
        except Exception as exc:
            errors.append({"component": "imap", "classification": type(exc).__name__, "error": str(exc)})
            result["imap"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        try:
            result["smtp"] = self._smtp_health()
        except Exception as exc:
            errors.append({"component": "smtp", "classification": type(exc).__name__, "error": str(exc)})
            result["smtp"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        if include_dns:
            domain = self.settings.email_address.rsplit("@", 1)[-1].lower()
            try:
                result["dns"] = self.dns_checker.check(domain, dkim_selector=dkim_selector)
            except Exception as exc:
                errors.append({"component": "dns", "classification": type(exc).__name__, "error": str(exc)})
                result["dns"] = {"domain": domain, "ok": False, "error": f"{type(exc).__name__}: {exc}"}
        result["errors"] = errors
        result["ok"] = not any(item["component"] in {"imap", "smtp"} for item in errors)
        result["graceful_degradation"] = True
        with self._health_lock:
            self._health_cache[key] = (now, dict(result))
        return result

    def get_email(self, mailbox: str, uid: str) -> dict[str, Any]:
        with self._imap() as conn:
            self._select(conn, mailbox, readonly=True)
            raw, truncated = self._fetch_raw(conn, uid)
        msg = BytesParser(policy=policy.default).parsebytes(raw)
        row = _message_to_dict(msg, uid=uid, mailbox=mailbox, include_body=not truncated, truncated=truncated)
        row["diagnostics"] = message_diagnostics(raw)
        row["diagnostics"]["raw_message_complete"] = not truncated
        return row

    def initialize_inbound_highwater(self) -> int:
        account_id = getattr(self.settings, "account_id", "") or self.settings.email_address
        with self._imap() as conn:
            self._select(conn, self.settings.inbox_mailbox, readonly=True)
            typ, data = conn.uid("SEARCH", None, "ALL")
            if typ != "OK":
                raise MailBridgeError("IMAP search failed while initializing inbound watcher")
            values = [int(value) for value in (data[0].decode().split() if data and data[0] else []) if value.isdigit()]
        high = max(values, default=0)
        with self._inbound_lock:
            self._inbound_highwater[account_id] = high
        return high

    def process_inbound_changes(self, limit: int = 50) -> dict[str, Any]:
        account_id = getattr(self.settings, "account_id", "") or self.settings.email_address
        with self._inbound_lock:
            previous = self._inbound_highwater.get(account_id)
        if previous is None:
            return {"initialized": True, "highwater_uid": self.initialize_inbound_highwater(), "processed": []}
        with self._imap() as conn:
            self._select(conn, self.settings.inbox_mailbox, readonly=True)
            typ, data = conn.uid("SEARCH", None, "UID", f"{previous + 1}:*")
            if typ != "OK":
                raise MailBridgeError("IMAP search failed for inbound changes")
            uids = [value for value in (data[0].decode().split() if data and data[0] else []) if value.isdigit()]
            uids = uids[-max(1, min(int(limit), 500)):]
            processed = []
            high = previous
            for uid in uids:
                raw, truncated = self._fetch_raw(conn, uid)
                high = max(high, int(uid))
                if truncated:
                    continue
                result = self.reliability.process_inbound(raw, account_id=account_id)
                processed.append({"uid": uid, **result})
        with self._inbound_lock:
            self._inbound_highwater[account_id] = high
        return {"initialized": False, "highwater_uid": high, "processed": processed}
