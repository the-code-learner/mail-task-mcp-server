from __future__ import annotations

import hashlib
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from .email_inventory_v963 import inventory_message
from .email_privacy_v963 import _ALLOWED_PROXY_TYPES, _MAX_PROXY_BYTES, _MAX_PROXY_CONCURRENCY, fetch_high_noise_decoys
from .mailbox_cache_v963 import MailboxCacheStore

_BLOCKED_NETWORK_CLASSIFICATIONS = {"normal link", "analytics link", "unsubscribe", "action url", "redirector"}

def _resource_key_v969(account_id: str, mailbox: str, uid: str, url_hash: str) -> str:
    material = "\x00".join(
        [str(account_id), str(mailbox), str(uid), str(url_hash)]
    ).encode("utf-8", "surrogatepass")
    return "r_" + hashlib.sha256(material).hexdigest()


def install_hashed_resource_keys(cache: MailboxCacheStore) -> int:
    """Migrate existing keys in place, then make all new cache keys opaque hashes."""
    migrated = 0
    lock = getattr(cache, "_lock", None)
    context = lock if lock is not None else _NullContext()
    with context, cache._connect() as conn:
        rows = conn.execute(
            """
            SELECT cache_key,account_id,mailbox,uid,url_hash
            FROM mailbox_cache_remote_resources
            """
        ).fetchall()
        for raw in rows:
            row = dict(raw)
            old = str(row.get("cache_key") or "")
            new = _resource_key_v969(
                str(row.get("account_id") or ""),
                str(row.get("mailbox") or ""),
                str(row.get("uid") or ""),
                str(row.get("url_hash") or ""),
            )
            if not old or old == new:
                continue
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
    MailboxCacheStore.resource_key = staticmethod(_resource_key_v969)  # type: ignore[assignment]
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


class PassiveContentService:
    """Shared WebGUI/MCP passive-resource fetch/cache/high-noise pipeline."""

    def __init__(self, base: Any):
        self.base = base

    def _filtered_inventory(self, raw_inventory: dict[str, Any]) -> tuple[dict[str, Any], int]:
        filtered = dict(raw_inventory)
        rows: list[dict[str, Any]] = []
        excluded = 0
        for raw in raw_inventory.get("urls") or []:
            row = dict(raw)
            allowed = _passive_allowed(row)
            if not allowed:
                source = str(row.get("source_type") or "").casefold()
                classification = str(row.get("classification") or "").casefold()
                if source in {"a href", "form action", "button formaction"} or classification in _BLOCKED_NETWORK_CLASSIFICATIONS:
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
            return "partial success", successes, failures
        return "failure", successes, failures

    @staticmethod
    def _delete_message_resources(
        cache: MailboxCacheStore, account_id: str, mailbox: str, uid: str
    ) -> int:
        lock = getattr(cache, "_lock", None)
        context = lock if lock is not None else _NullContext()
        with context, cache._connect() as conn:
            cursor = conn.execute(
                """
                DELETE FROM mailbox_cache_remote_resources
                WHERE account_id=? AND mailbox=? AND uid=?
                """,
                (account_id, mailbox, str(uid)),
            )
            return int(cursor.rowcount or 0)

    def fetch_inventory(
        self,
        inventory: dict[str, Any],
        *,
        account_id: str,
        mailbox: str,
        uid: str,
        refresh: bool = False,
    ) -> dict[str, Any]:
        cache = self.base.mailbox_cache_store()
        proxy_store = self.base.privacy_proxy_store()
        proxy = self.base.privacy_proxy_client()
        filtered, excluded = self._filtered_inventory(inventory)
        candidates = self._candidates(filtered)
        if refresh:
            self._delete_message_resources(cache, account_id, mailbox, uid)

        attempted_rows: list[dict[str, Any]] = []
        work: list[tuple[dict[str, Any], str, str]] = []
        for row in candidates:
            url = str(row.get("url") or "")
            digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
            key = cache.resource_key(account_id, mailbox, uid, digest)
            if cache.get_resource(key) is None:
                work.append((row, digest, key))

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
                    error_state=("proxy_error" if str(fetched.get("error") or "") else ""),
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
        for row in candidates:
            url = str(row.get("url") or "")
            digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
            key = cache.resource_key(account_id, mailbox, uid, digest)
            cached = cache.get_resource(key)
            if cached is not None:
                all_rows.append(cached)

        _, attempted_succeeded, attempted_failed = self._state(attempted_rows)
        state, successes, failures = self._state(all_rows)

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

        return {
            "ok": state != "failure",
            "render_state": state,
            "cache_only": not bool(work),
            "refresh": bool(refresh),
            "diagnostics": {
                "discovered": len(candidates),
                "genuine_attempted": len(work),
                "genuine_succeeded": attempted_succeeded,
                "genuine_failed": attempted_failed,
                "cached_succeeded": successes,
                "cached_failed": failures,
                "decoy_attempted": decoy_attempted,
                "decoy_succeeded": decoy_succeeded,
                "excluded_navigation_action": excluded,
                "timings_ms": {
                    "genuine": genuine_ms,
                    "decoy": decoy_ms,
                    "total": round(genuine_ms + decoy_ms, 2),
                },
            },
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
        inventory = inventory_message(
            str(detail.get("body_html") or ""),
            str(detail.get("body") or ""),
        )
        return self.fetch_inventory(
            inventory,
            account_id=account_id,
            mailbox=mailbox,
            uid=uid,
            refresh=refresh,
        )
