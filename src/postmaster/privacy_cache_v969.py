from __future__ import annotations

import hashlib
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from html import escape, unescape
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlencode, urlparse

from .email_inventory_v963 import inventory_message
from .email_privacy_v963 import (
    _ALLOWED_PROXY_TYPES,
    _MAX_PROXY_BYTES,
    _MAX_PROXY_CONCURRENCY,
    fetch_high_noise_decoys,
    safe_email_html,
)
from .mailbox_cache_v963 import MailboxCacheStore

_BLOCKED_NETWORK_CLASSIFICATIONS = {
    "normal link",
    "analytics link",
    "unsubscribe",
    "action url",
    "redirector",
}


def _legacy_resource_key_v969(
    account_id: str,
    mailbox: str,
    uid: str,
    url_hash: str,
) -> str:
    material = "\x00".join(
        [str(account_id), str(mailbox), str(uid), str(url_hash)]
    ).encode("utf-8", "surrogatepass")
    return "r_" + hashlib.sha256(material).hexdigest()


def _stable_message_identity(
    cache: MailboxCacheStore,
    account_id: str,
    mailbox: str,
    uid: str,
) -> str:
    """Account-scoped, move-stable identity for a cached RFC822 message.

    Full raw MIME is preferred because an IMAP MOVE/COPY preserves message bytes while
    mailbox/UID can change. Message-ID is included as a semantic signal but is never trusted
    by itself. If the body is not cached yet, header + stable metadata are used. A final
    UIDVALIDITY+UID fallback exists only for callers that have not cached the message record.
    """

    row: dict[str, Any] | None = None
    try:
        with cache._connect() as conn:
            found = conn.execute(
                """
                SELECT uidvalidity,message_id,date_value,from_text,to_text,cc_text,subject,
                       size_bytes,header_bytes,raw_bytes,body_text,body_html
                FROM mailbox_cache_messages
                WHERE account_id=? AND mailbox=? AND uid=?
                """,
                (str(account_id), str(mailbox), str(uid)),
            ).fetchone()
            row = dict(found) if found else None
    except Exception:
        row = None

    if row:
        raw = row.get("raw_bytes")
        if raw is not None:
            fingerprint = hashlib.sha256(bytes(raw)).hexdigest()
        else:
            header = bytes(row.get("header_bytes") or b"")
            stable_fields = "\x1f".join(
                [
                    str(row.get("date_value") or ""),
                    str(row.get("from_text") or ""),
                    str(row.get("to_text") or ""),
                    str(row.get("cc_text") or ""),
                    str(row.get("subject") or ""),
                    str(row.get("size_bytes") or ""),
                    str(row.get("body_text") or ""),
                    str(row.get("body_html") or ""),
                ]
            ).encode("utf-8", "surrogatepass")
            fingerprint = hashlib.sha256(header + b"\x00" + stable_fields).hexdigest()
        message_id = str(row.get("message_id") or "").strip().casefold()
        material = "\x00".join(
            [str(account_id), message_id, fingerprint]
        ).encode("utf-8", "surrogatepass")
        return "m_" + hashlib.sha256(material).hexdigest()

    uidvalidity = ""
    try:
        uidvalidity = str(cache.state(str(account_id), str(mailbox)).get("uidvalidity") or "")
    except Exception:
        pass
    fallback = "\x00".join(
        [str(account_id), str(mailbox), uidvalidity, str(uid)]
    ).encode("utf-8", "surrogatepass")
    return "m_" + hashlib.sha256(fallback).hexdigest()


def _resource_key_v969(
    account_id: str,
    message_identity: str,
    url_hash: str,
) -> str:
    material = "\x00".join(
        [str(account_id), str(message_identity), str(url_hash)]
    ).encode("utf-8", "surrogatepass")
    return "r_" + hashlib.sha256(material).hexdigest()


def install_hashed_resource_keys(cache: MailboxCacheStore) -> int:
    """Make resource keys opaque, account-scoped, move-stable and UID-reuse-safe.

    Existing rows are migrated when their cached message record can provide a stable
    fingerprint. The per-store resource_key closure lets the v9.6.3 cache-state/render
    helpers transparently use the same v9.6.9 identity without changing their public API.
    """

    migrated = 0
    with cache._connect() as conn:
        rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT cache_key,account_id,mailbox,uid,url_hash
                FROM mailbox_cache_remote_resources
                """
            ).fetchall()
        ]
    plans: list[tuple[str, str]] = []
    for row in rows:
        old = str(row.get("cache_key") or "")
        message_identity = _stable_message_identity(
            cache,
            str(row.get("account_id") or ""),
            str(row.get("mailbox") or ""),
            str(row.get("uid") or ""),
        )
        new = _resource_key_v969(
            str(row.get("account_id") or ""),
            message_identity,
            str(row.get("url_hash") or ""),
        )
        if old and old != new:
            plans.append((old, new))

    lock = getattr(cache, "_lock", None)
    context = lock if lock is not None else _NullContext()
    with context, cache._connect() as conn:
        for old, new in plans:
            existing = conn.execute(
                "SELECT 1 FROM mailbox_cache_remote_resources WHERE cache_key=?",
                (new,),
            ).fetchone()
            if existing:
                conn.execute(
                    "DELETE FROM mailbox_cache_remote_resources WHERE cache_key=?",
                    (old,),
                )
            else:
                conn.execute(
                    """
                    UPDATE mailbox_cache_remote_resources
                    SET cache_key=? WHERE cache_key=?
                    """,
                    (new, old),
                )
            migrated += 1

    def stable_key(
        account_id: str,
        mailbox: str,
        uid: str,
        url_hash: str,
    ) -> str:
        identity = _stable_message_identity(
            cache,
            str(account_id),
            str(mailbox),
            str(uid),
        )
        return _resource_key_v969(str(account_id), identity, str(url_hash))

    cache.resource_key = stable_key  # type: ignore[method-assign]
    return migrated


class _NullContext:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def _passive_allowed(row: dict[str, Any]) -> bool:
    url = str(row.get("url") or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        return False
    source = str(row.get("source_type") or "").casefold()
    classification = str(row.get("classification") or "").casefold()
    if classification in _BLOCKED_NETWORK_CLASSIFICATIONS:
        return False
    if source == "a href" or source in {"form action", "button formaction"}:
        return False
    if source in {
        "img src",
        "source src",
        "video poster",
        "style url()",
        "style-block url()",
        "body background",
        "table background",
        "td background",
        "div background",
        "span background",
    }:
        return True
    if source.endswith(" background"):
        return True
    if source == "link href":
        return bool(
            re.search(
                r"""(?i)\brel\s*=\s*['"][^'"]*stylesheet""",
                str(row.get("source_snippet") or ""),
            )
        )
    return False


class _FullHtmlRewriterV969(HTMLParser):
    """Render cached passive resources while preserving explicit navigation hyperlinks."""

    _DROP_CONTENT = {
        "script",
        "iframe",
        "object",
        "embed",
        "form",
        "button",
        "input",
        "textarea",
        "select",
        "option",
    }
    _SKIP_TAG = {"meta", "base"}
    _VOID = {"br", "hr", "img", "link", "source"}

    def __init__(self, resource_urls: dict[str, str]) -> None:
        super().__init__(convert_charrefs=False)
        self.resource_urls = resource_urls
        self.parts: list[str] = []
        self.drop_depth = 0
        self.style_depth = 0

    @staticmethod
    def _navigation_href(value: str) -> str:
        target = unescape(value or "").strip()
        if target.startswith("#"):
            return target
        parsed = urlparse(target)
        if parsed.scheme.casefold() in {"http", "https", "mailto"}:
            return target
        return ""

    def _css(self, value: str) -> str:
        css_re = re.compile(r"(?is)url\(\s*(['\"]?)(.*?)\1\s*\)")

        def repl(match: re.Match[str]) -> str:
            target = unescape(match.group(2) or "").strip()
            local = self.resource_urls.get(target)
            return f'url("{local}")' if local else 'url("")'

        return css_re.sub(repl, value or "")

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.casefold()
        if tag in self._DROP_CONTENT:
            self.drop_depth += 1
            return
        if self.drop_depth or tag in self._SKIP_TAG:
            return
        if tag == "style":
            self.style_depth += 1
            self.parts.append("<style>")
            return

        data = {str(key).casefold(): str(value or "") for key, value in attrs}
        out: list[tuple[str, str]] = []
        for name, value in data.items():
            if name.startswith("on") or name in {"srcset", "action", "formaction"}:
                continue
            if tag == "a" and name == "href":
                target = self._navigation_href(value)
                if target:
                    out.append(("href", target))
                continue
            if name == "style":
                out.append((name, self._css(value)))
                continue
            if name in {"src", "background", "poster"}:
                if urlparse(unescape(value).strip()).scheme.casefold() in {"http", "https"}:
                    local = self.resource_urls.get(unescape(value).strip())
                    if local:
                        out.append((name, local))
                    continue
            if tag == "link" and name == "href":
                if urlparse(unescape(value).strip()).scheme.casefold() in {"http", "https"}:
                    local = self.resource_urls.get(unescape(value).strip())
                    if local:
                        out.append((name, local))
                    continue
            if name in {"href", "src", "background", "poster"} and value.casefold().startswith(
                ("javascript:", "file:")
            ):
                continue
            out.append((name, value))

        rendered = "".join(
            f' {escape(name, quote=True)}="{escape(value, quote=True)}"'
            for name, value in out
        )
        self.parts.append(f"<{tag}{rendered}>")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if tag in self._DROP_CONTENT:
            if self.drop_depth:
                self.drop_depth -= 1
            return
        if self.drop_depth or tag in self._SKIP_TAG:
            return
        if tag == "style":
            if self.style_depth:
                self.style_depth -= 1
            self.parts.append("</style>")
            return
        if tag not in self._VOID:
            self.parts.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if self.drop_depth:
            return
        self.parts.append(self._css(data) if self.style_depth else data)

    def handle_entityref(self, name: str) -> None:
        if not self.drop_depth:
            self.parts.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        if not self.drop_depth:
            self.parts.append(f"&#{name};")

    def handle_comment(self, data: str) -> None:
        return


def rewrite_full_html_v969(html: str, resource_urls: dict[str, str]) -> str:
    parser = _FullHtmlRewriterV969(resource_urls)
    try:
        parser.feed(html or "")
        parser.close()
    except Exception:
        return safe_email_html(html)
    return "".join(parser.parts)


class PassiveContentService:
    """Shared WebGUI/MCP passive-resource classify/fetch/cache/noise/rewrite pipeline."""

    def __init__(self, base: Any):
        self.base = base

    def _filtered_inventory(
        self,
        raw_inventory: dict[str, Any],
    ) -> tuple[dict[str, Any], int]:
        filtered = dict(raw_inventory)
        rows: list[dict[str, Any]] = []
        excluded = 0
        for raw in raw_inventory.get("urls") or []:
            row = dict(raw)
            allowed = _passive_allowed(row)
            if not allowed:
                source = str(row.get("source_type") or "").casefold()
                classification = str(row.get("classification") or "").casefold()
                if (
                    source in {"a href", "form action", "button formaction"}
                    or classification in _BLOCKED_NETWORK_CLASSIFICATIONS
                ):
                    excluded += 1
            row["passive_resource"] = allowed
            rows.append(row)
        filtered["urls"] = rows
        return filtered, excluded

    @staticmethod
    def _candidates(inventory: dict[str, Any]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw in inventory.get("urls") or []:
            row = dict(raw)
            url = str(row.get("url") or "")
            if not row.get("passive_resource") or url in seen:
                continue
            seen.add(url)
            rows.append(row)
            if len(rows) >= 32:
                break
        return rows

    @staticmethod
    def _state(rows: list[dict[str, Any]]) -> tuple[str, int, int]:
        successes = 0
        failures = 0
        for row in rows:
            ok = (
                int(row.get("http_status") or 0) == 200
                and row.get("body") is not None
                and not str(row.get("error_state") or "")
            )
            if ok:
                successes += 1
            else:
                failures += 1
        if not rows:
            return "success", 0, 0
        if failures == 0:
            return "success", successes, failures
        if successes:
            return "partial", successes, failures
        return "failure", successes, failures

    @staticmethod
    def _delete_resource_keys(cache: MailboxCacheStore, keys: list[str]) -> int:
        if not keys:
            return 0
        lock = getattr(cache, "_lock", None)
        context = lock if lock is not None else _NullContext()
        with context, cache._connect() as conn:
            cursor = conn.executemany(
                "DELETE FROM mailbox_cache_remote_resources WHERE cache_key=?",
                [(key,) for key in keys],
            )
            return int(cursor.rowcount or 0)

    @staticmethod
    def _rebind_resource(
        cache: MailboxCacheStore,
        key: str,
        *,
        account_id: str,
        mailbox: str,
        uid: str,
    ) -> None:
        lock = getattr(cache, "_lock", None)
        context = lock if lock is not None else _NullContext()
        try:
            with context, cache._connect() as conn:
                conn.execute(
                    """
                    UPDATE mailbox_cache_remote_resources
                    SET account_id=?,mailbox=?,uid=?
                    WHERE cache_key=?
                    """,
                    (str(account_id), str(mailbox), str(uid), str(key)),
                )
        except Exception:
            pass

    @staticmethod
    def _key(
        cache: MailboxCacheStore,
        account_id: str,
        mailbox: str,
        uid: str,
        url: str,
    ) -> tuple[str, str]:
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
        return digest, cache.resource_key(account_id, mailbox, uid, digest)

    def _resource_map(
        self,
        inventory: dict[str, Any],
        *,
        account_id: str,
        mailbox: str,
        uid: str,
    ) -> dict[str, str]:
        cache = self.base.mailbox_cache_store()
        result: dict[str, str] = {}
        filtered, _ = self._filtered_inventory(inventory)
        for row in self._candidates(filtered):
            url = str(row.get("url") or "")
            _, key = self._key(cache, account_id, mailbox, uid, url)
            cached = cache.get_resource(key)
            if (
                cached
                and int(cached.get("http_status") or 0) == 200
                and cached.get("body") is not None
                and not str(cached.get("error_state") or "")
            ):
                result[url] = "/dashboard/inbox/resource?" + urlencode({"key": key})
        return result

    def _render(
        self,
        body_html: str,
        inventory: dict[str, Any],
        *,
        account_id: str,
        mailbox: str,
        uid: str,
    ) -> str:
        return rewrite_full_html_v969(
            body_html,
            self._resource_map(
                inventory,
                account_id=account_id,
                mailbox=mailbox,
                uid=uid,
            ),
        )

    def fetch_inventory(
        self,
        inventory: dict[str, Any],
        *,
        account_id: str,
        mailbox: str,
        uid: str,
        refresh: bool = False,
        body_html: str = "",
    ) -> dict[str, Any]:
        cache = self.base.mailbox_cache_store()
        proxy_store = self.base.privacy_proxy_store()
        proxy = self.base.privacy_proxy_client()
        filtered, excluded = self._filtered_inventory(inventory)
        candidates = self._candidates(filtered)

        keyed: list[tuple[dict[str, Any], str, str]] = []
        for row in candidates:
            url = str(row.get("url") or "")
            digest, key = self._key(cache, account_id, mailbox, uid, url)
            keyed.append((row, digest, key))

        if refresh:
            self._delete_resource_keys(cache, [key for _, _, key in keyed])

        attempted_rows: list[dict[str, Any]] = []
        work: list[tuple[dict[str, Any], str, str]] = []
        cache_hits = 0
        negative_cache_hits = 0
        for row, digest, key in keyed:
            cached = cache.get_resource(key)
            if cached is None:
                work.append((row, digest, key))
                continue
            self._rebind_resource(
                cache,
                key,
                account_id=account_id,
                mailbox=mailbox,
                uid=uid,
            )
            if (
                int(cached.get("http_status") or 0) == 200
                and cached.get("body") is not None
                and not str(cached.get("error_state") or "")
            ):
                cache_hits += 1
            else:
                negative_cache_hits += 1

        started_genuine = time.perf_counter()

        def fetch_one(spec: tuple[dict[str, Any], str, str]) -> dict[str, Any]:
            row, digest, key = spec
            url = str(row.get("url") or "")
            try:
                fetched = proxy.fetch(
                    url,
                    classification=str(row.get("classification") or "unknown"),
                    tracking_score=int(row.get("tracking_score") or 0),
                    request_kind="render",
                    max_response_bytes=_MAX_PROXY_BYTES,
                )
                status = int(fetched.get("status") or 0)
                content_type = (
                    str(fetched.get("content_type") or "")
                    .split(";", 1)[0]
                    .strip()
                    .casefold()
                )
                body = bytes(fetched.get("body") or b"") if status == 200 else None
                if body is not None:
                    if len(body) > _MAX_PROXY_BYTES:
                        raise RuntimeError("resource_size_limit")
                    if content_type not in _ALLOWED_PROXY_TYPES:
                        raise RuntimeError("resource_content_type_rejected")
                return cache.put_resource(
                    cache_key=key,
                    account_id=account_id,
                    mailbox=mailbox,
                    uid=uid,
                    url=url,
                    url_hash=digest,
                    content_type=content_type,
                    body=body,
                    http_status=status,
                    redirect_location=str(fetched.get("redirect_location") or ""),
                    classification=str(row.get("classification") or "unknown"),
                    tracking_score=int(row.get("tracking_score") or 0),
                    error_state=(
                        "proxy_error" if str(fetched.get("error") or "") else ""
                    ),
                )
            except Exception as exc:
                return cache.put_resource(
                    cache_key=key,
                    account_id=account_id,
                    mailbox=mailbox,
                    uid=uid,
                    url=url,
                    url_hash=digest,
                    content_type="",
                    body=None,
                    http_status=None,
                    redirect_location="",
                    classification=str(row.get("classification") or "unknown"),
                    tracking_score=int(row.get("tracking_score") or 0),
                    error_state=type(exc).__name__,
                )

        if work:
            with ThreadPoolExecutor(
                max_workers=_MAX_PROXY_CONCURRENCY,
                thread_name_prefix="postmaster-v969-passive",
            ) as pool:
                futures = [pool.submit(fetch_one, spec) for spec in work]
                for future in as_completed(futures):
                    attempted_rows.append(future.result())
        genuine_ms = round((time.perf_counter() - started_genuine) * 1000.0, 2)

        all_rows: list[dict[str, Any]] = []
        for _, _, key in keyed:
            cached = cache.get_resource(key)
            if cached is not None:
                all_rows.append(cached)

        _, attempted_succeeded, attempted_failed = self._state(attempted_rows)
        state, successes, failures = self._state(all_rows)
        missing = max(0, len(candidates) - len(all_rows))
        if missing:
            failures += missing
            if successes:
                state = "partial"
            else:
                state = "failure"

        decoy_attempted = 0
        decoy_succeeded = 0
        decoy_ms = 0.0
        high_noise = bool(proxy_store.status().get("high_noise_decoy_enabled"))
        if work and high_noise:
            started_decoy = time.perf_counter()
            try:
                noise = fetch_high_noise_decoys(
                    filtered,
                    store=proxy_store,
                    proxy=proxy,
                    account_id=account_id,
                    mailbox=mailbox,
                    uid=uid,
                )
                events = list(noise.get("events") or [])
                decoy_attempted = int(noise.get("requests") or 0)
                decoy_succeeded = sum(
                    1
                    for event in events
                    if int(event.get("http_status") or 0) > 0
                    and not str(event.get("error_state") or "")
                )
            except Exception:
                decoy_attempted = 0
                decoy_succeeded = 0
            decoy_ms = round((time.perf_counter() - started_decoy) * 1000.0, 2)

        rendered_html = ""
        if state != "failure" and body_html:
            rendered_html = self._render(
                body_html,
                filtered,
                account_id=account_id,
                mailbox=mailbox,
                uid=uid,
            )

        diagnostics = {
            "passive_discovered": len(candidates),
            "discovered": len(candidates),
            "genuine_attempted": len(work),
            "genuine_succeeded": attempted_succeeded,
            "genuine_failed": attempted_failed,
            "cache_hits": cache_hits,
            "negative_cache_hits": negative_cache_hits,
            "cached_succeeded": successes,
            "cached_failed": failures,
            "decoy_attempted": decoy_attempted,
            "decoy_succeeded": decoy_succeeded,
            "decoy_failed": max(0, decoy_attempted - decoy_succeeded),
            "excluded_action_urls": excluded,
            "excluded_navigation_action": excluded,
            "timings_ms": {
                "genuine": genuine_ms,
                "decoy": decoy_ms,
                "total": round(genuine_ms + decoy_ms, 2),
            },
        }
        return {
            "ok": state != "failure",
            "render_state": state,
            "cache_only": not bool(work),
            "refresh": bool(refresh),
            "full_html_available": state != "failure",
            "rendered_html": rendered_html,
            "network_requests_performed": len(work) + decoy_attempted,
            "diagnostics": diagnostics,
        }

    def fetch_message(
        self,
        *,
        account_id: str,
        mailbox: str,
        uid: str,
        refresh: bool = False,
    ) -> dict[str, Any]:
        detail = self.base.mailbox_cache_synchronizer().ensure_body(
            self.base.mail_client(account_id),
            account_id=account_id,
            mailbox=mailbox,
            uid=uid,
        )
        body_html = str(detail.get("body_html") or "")
        inventory = inventory_message(
            body_html,
            str(detail.get("body") or ""),
        )
        return self.fetch_inventory(
            inventory,
            account_id=account_id,
            mailbox=mailbox,
            uid=uid,
            refresh=refresh,
            body_html=body_html,
        )

    def render_cached_message(
        self,
        *,
        account_id: str,
        mailbox: str,
        uid: str,
    ) -> dict[str, Any]:
        """Cache-only Full read. Never performs proxy/origin fetches or High-Noise."""

        cache = self.base.mailbox_cache_store()
        detail = cache.get_message(account_id, mailbox, str(uid), include_body=True)
        if not detail or not detail.get("body_cached"):
            return {
                "ok": False,
                "render_state": "failure",
                "cache_only": True,
                "full_html_available": False,
                "rendered_html": "",
                "network_requests_performed": 0,
                "error": "body_not_cached",
                "diagnostics": {
                    "passive_discovered": 0,
                    "discovered": 0,
                    "genuine_attempted": 0,
                    "genuine_succeeded": 0,
                    "genuine_failed": 0,
                    "cache_hits": 0,
                    "negative_cache_hits": 0,
                    "cached_succeeded": 0,
                    "cached_failed": 0,
                    "decoy_attempted": 0,
                    "decoy_succeeded": 0,
                    "decoy_failed": 0,
                    "excluded_action_urls": 0,
                    "excluded_navigation_action": 0,
                    "timings_ms": {"genuine": 0.0, "decoy": 0.0, "total": 0.0},
                },
            }

        body_html = str(detail.get("body_html") or "")
        raw_inventory = inventory_message(body_html, str(detail.get("body") or ""))
        filtered, excluded = self._filtered_inventory(raw_inventory)
        candidates = self._candidates(filtered)
        rows: list[dict[str, Any]] = []
        positive = 0
        negative = 0
        for row in candidates:
            url = str(row.get("url") or "")
            _, key = self._key(cache, account_id, mailbox, uid, url)
            cached = cache.get_resource(key)
            if cached is None:
                continue
            rows.append(cached)
            self._rebind_resource(
                cache,
                key,
                account_id=account_id,
                mailbox=mailbox,
                uid=uid,
            )
            if (
                int(cached.get("http_status") or 0) == 200
                and cached.get("body") is not None
                and not str(cached.get("error_state") or "")
            ):
                positive += 1
            else:
                negative += 1

        state, successes, failures = self._state(rows)
        missing = max(0, len(candidates) - len(rows))
        failures += missing
        if candidates:
            if successes and failures:
                state = "partial"
            elif failures and not successes:
                state = "failure"
            else:
                state = "success"
        else:
            state = "success"

        rendered_html = ""
        if state != "failure" and body_html:
            rendered_html = self._render(
                body_html,
                filtered,
                account_id=account_id,
                mailbox=mailbox,
                uid=uid,
            )
        diagnostics = {
            "passive_discovered": len(candidates),
            "discovered": len(candidates),
            "genuine_attempted": 0,
            "genuine_succeeded": 0,
            "genuine_failed": 0,
            "cache_hits": positive,
            "negative_cache_hits": negative,
            "cached_succeeded": successes,
            "cached_failed": failures,
            "decoy_attempted": 0,
            "decoy_succeeded": 0,
            "decoy_failed": 0,
            "excluded_action_urls": excluded,
            "excluded_navigation_action": excluded,
            "timings_ms": {"genuine": 0.0, "decoy": 0.0, "total": 0.0},
        }
        return {
            "ok": state != "failure",
            "render_state": state,
            "cache_only": True,
            "refresh": False,
            "full_html_available": state != "failure",
            "rendered_html": rendered_html,
            "network_requests_performed": 0,
            "diagnostics": diagnostics,
        }


__all__ = [
    "PassiveContentService",
    "install_hashed_resource_keys",
    "rewrite_full_html_v969",
    "_legacy_resource_key_v969",
    "_resource_key_v969",
    "_stable_message_identity",
]
