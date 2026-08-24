from __future__ import annotations

import hashlib
import re
import time
from concurrent.futures import ThreadPoolExecutor, wait
from typing import Any
from urllib.parse import urlencode, urljoin, urlparse

from .email_privacy_v963 import (
    _ALLOWED_PROXY_TYPES,
    _MAX_PROXY_BYTES,
    _MAX_PROXY_CONCURRENCY,
    _classification,
)
from .privacy_cache_v969 import PassiveContentService, _passive_allowed


MAX_STYLESHEET_BYTES = 512_000
MAX_NESTED_RESOURCES_PER_STYLESHEET = 12
MAX_TOTAL_RESOURCES_PER_MESSAGE_CYCLE = 32
MAX_CUMULATIVE_BYTES_PER_STYLESHEET = 4_000_000
MAX_NESTED_EXECUTION_SECONDS = 8.0
CSS_IMPORT_MAX_DEPTH = 0  # v9.6.9: @import is explicitly unsupported/sterilized.

_CSS_URL_RE = re.compile(r"(?is)url\(\s*(['\"]?)(.*?)\1\s*\)")
_CSS_IMPORT_RE = re.compile(
    r"(?is)@import\s+(?:url\([^)]*\)|['\"][^'\"]*['\"])[^;]*;?"
)
_CSS_EXECUTABLE_RE = re.compile(r"(?is)(?:expression\s*\(|-moz-binding\s*:|behavior\s*:)")
_NESTED_ALLOWED_TYPES = set(_ALLOWED_PROXY_TYPES) - {"text/css"}


def _is_success(row: dict[str, Any] | None) -> bool:
    return bool(
        row
        and int(row.get("http_status") or 0) == 200
        and row.get("body") is not None
        and not str(row.get("error_state") or "")
    )


class BoundedPassiveContentService(PassiveContentService):
    """v9.6.9 bounded second-level passive resolver for remote stylesheets.

    This deliberately is not a generic crawler. Only top-level cached CSS is inspected,
    @import is stripped (depth 0), and passive CSS url(...) resources are resolved once
    through the same Privacy Proxy and stable message cache used by the base pipeline.
    """

    @staticmethod
    def _nested_spec(stylesheet_url: str, raw_target: str) -> dict[str, Any] | None:
        target = str(raw_target or "").strip().strip("\"'")
        if not target or target.startswith(("#", "data:", "cid:")):
            return None
        resolved = urljoin(stylesheet_url, target)
        if urlparse(resolved).scheme.casefold() not in {"http", "https"}:
            return None
        record: dict[str, Any] = {
            "url": resolved,
            "source_type": "style-block url()",
            "source_snippet": "remote stylesheet url()",
        }
        classification, score, _, _ = _classification(record)
        record["classification"] = classification
        record["tracking_score"] = score
        if not _passive_allowed(record):
            return None
        return record

    @staticmethod
    def _delete_keys(cache: Any, keys: list[str]) -> None:
        if not keys:
            return
        lock = getattr(cache, "_lock", None)
        if lock is None:
            with cache._connect() as conn:
                conn.executemany(
                    "DELETE FROM mailbox_cache_remote_resources WHERE cache_key=?",
                    [(key,) for key in keys],
                )
            return
        with lock, cache._connect() as conn:
            conn.executemany(
                "DELETE FROM mailbox_cache_remote_resources WHERE cache_key=?",
                [(key,) for key in keys],
            )

    def _nested_fetch(
        self,
        *,
        spec: dict[str, Any],
        account_id: str,
        mailbox: str,
        uid: str,
        remaining_bytes: list[int],
    ) -> dict[str, Any]:
        cache = self.base.mailbox_cache_store()
        proxy = self.base.privacy_proxy_client()
        url = str(spec["url"])
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
        key = cache.resource_key(account_id, mailbox, uid, digest)
        try:
            fetched = proxy.fetch(
                url,
                classification=str(spec.get("classification") or "remote image"),
                tracking_score=int(spec.get("tracking_score") or 0),
                request_kind="render",
                max_response_bytes=min(_MAX_PROXY_BYTES, max(1, remaining_bytes[0])),
            )
            status = int(fetched.get("status") or 0)
            content_type = (
                str(fetched.get("content_type") or "").split(";", 1)[0].strip().casefold()
            )
            body = bytes(fetched.get("body") or b"") if status == 200 else None
            error_state = "proxy_error" if str(fetched.get("error") or "") else ""
            if body is not None:
                if len(body) > _MAX_PROXY_BYTES:
                    raise RuntimeError("nested_resource_size_limit")
                if content_type not in _NESTED_ALLOWED_TYPES:
                    raise RuntimeError("nested_resource_content_type_rejected")
                if len(body) > remaining_bytes[0]:
                    raise RuntimeError("nested_cumulative_size_limit")
                remaining_bytes[0] -= len(body)
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
                classification=str(spec.get("classification") or "remote image"),
                tracking_score=int(spec.get("tracking_score") or 0),
                error_state=error_state,
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
                classification=str(spec.get("classification") or "remote image"),
                tracking_score=int(spec.get("tracking_score") or 0),
                error_state=type(exc).__name__,
            )

    def _process_stylesheets(
        self,
        inventory: dict[str, Any],
        *,
        account_id: str,
        mailbox: str,
        uid: str,
        refresh: bool,
        network_allowed: bool,
    ) -> dict[str, int | float]:
        cache = self.base.mailbox_cache_store()
        filtered, _ = self._filtered_inventory(inventory)
        top_level = self._candidates(filtered)
        remaining_global = max(0, MAX_TOTAL_RESOURCES_PER_MESSAGE_CYCLE - len(top_level))
        stats: dict[str, int | float] = {
            "stylesheets_processed": 0,
            "stylesheets_rewritten": 0,
            "stylesheet_failures": 0,
            "nested_discovered": 0,
            "nested_attempted": 0,
            "nested_succeeded": 0,
            "nested_failed": 0,
            "nested_cache_hits": 0,
            "nested_negative_cache_hits": 0,
            "imports_stripped": 0,
            "bounded_skipped": 0,
            "nested_ms": 0.0,
        }
        started = time.perf_counter()

        for top in top_level:
            if remaining_global <= 0:
                break
            if str(top.get("source_type") or "").casefold() != "link href":
                continue
            stylesheet_url = str(top.get("url") or "")
            digest, css_key = self._key(cache, account_id, mailbox, uid, stylesheet_url)
            css_row = cache.get_resource(css_key)
            if not _is_success(css_row):
                continue
            content_type = str(css_row.get("content_type") or "").split(";", 1)[0].casefold()
            if content_type != "text/css":
                continue
            raw_body = bytes(css_row.get("body") or b"")
            stats["stylesheets_processed"] += 1
            if len(raw_body) > MAX_STYLESHEET_BYTES:
                cache.put_resource(
                    cache_key=css_key,
                    account_id=account_id,
                    mailbox=mailbox,
                    uid=uid,
                    url=stylesheet_url,
                    url_hash=digest,
                    content_type="",
                    body=None,
                    http_status=None,
                    redirect_location="",
                    classification=str(css_row.get("classification") or "remote stylesheet"),
                    tracking_score=int(css_row.get("tracking_score") or 0),
                    error_state="stylesheet_size_limit",
                )
                stats["stylesheet_failures"] += 1
                continue

            css = raw_body.decode("utf-8", errors="replace")
            imports = len(_CSS_IMPORT_RE.findall(css))
            if imports:
                stats["imports_stripped"] += imports
                css = _CSS_IMPORT_RE.sub("", css)
            if _CSS_EXECUTABLE_RE.search(css):
                css = _CSS_EXECUTABLE_RE.sub("blocked(", css)

            refs: list[tuple[str, dict[str, Any]]] = []
            seen: set[str] = set()
            for match in _CSS_URL_RE.finditer(css):
                raw_target = str(match.group(2) or "").strip()
                spec = self._nested_spec(stylesheet_url, raw_target)
                if not spec:
                    continue
                url = str(spec["url"])
                if url in seen:
                    continue
                seen.add(url)
                if len(refs) >= MAX_NESTED_RESOURCES_PER_STYLESHEET or remaining_global <= 0:
                    stats["bounded_skipped"] += 1
                    continue
                refs.append((raw_target, spec))
                remaining_global -= 1

            stats["nested_discovered"] += len(refs)
            key_by_url: dict[str, str] = {}
            work: list[dict[str, Any]] = []
            for _, spec in refs:
                url = str(spec["url"])
                nested_digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
                key = cache.resource_key(account_id, mailbox, uid, nested_digest)
                key_by_url[url] = key
                if refresh:
                    self._delete_keys(cache, [key])
                row = cache.get_resource(key)
                if _is_success(row):
                    stats["nested_cache_hits"] += 1
                elif row is not None:
                    stats["nested_negative_cache_hits"] += 1
                elif network_allowed:
                    work.append(spec)

            remaining_bytes = [
                max(0, MAX_CUMULATIVE_BYTES_PER_STYLESHEET - len(raw_body))
            ]
            if work and remaining_bytes[0] > 0:
                stats["nested_attempted"] += len(work)
                with ThreadPoolExecutor(
                    max_workers=_MAX_PROXY_CONCURRENCY,
                    thread_name_prefix="postmaster-v969-css",
                ) as pool:
                    futures = {
                        pool.submit(
                            self._nested_fetch,
                            spec=spec,
                            account_id=account_id,
                            mailbox=mailbox,
                            uid=uid,
                            remaining_bytes=remaining_bytes,
                        ): spec
                        for spec in work
                    }
                    done, pending = wait(
                        futures,
                        timeout=MAX_NESTED_EXECUTION_SECONDS,
                    )
                    for future in done:
                        row = future.result()
                        if _is_success(row):
                            stats["nested_succeeded"] += 1
                        else:
                            stats["nested_failed"] += 1
                    for future in pending:
                        spec = futures[future]
                        future.cancel()
                        url = str(spec["url"])
                        nested_digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
                        key = cache.resource_key(account_id, mailbox, uid, nested_digest)
                        cache.put_resource(
                            cache_key=key,
                            account_id=account_id,
                            mailbox=mailbox,
                            uid=uid,
                            url=url,
                            url_hash=nested_digest,
                            content_type="",
                            body=None,
                            http_status=None,
                            redirect_location="",
                            classification=str(spec.get("classification") or "remote image"),
                            tracking_score=int(spec.get("tracking_score") or 0),
                            error_state="nested_timeout",
                        )
                        stats["nested_failed"] += 1
            elif work:
                stats["nested_failed"] += len(work)

            def rewrite_url(match: re.Match[str]) -> str:
                raw_target = str(match.group(2) or "").strip()
                stripped = raw_target.strip().strip("\"'")
                if not stripped or stripped.startswith(("#", "data:", "cid:")):
                    return match.group(0)
                spec = self._nested_spec(stylesheet_url, raw_target)
                if not spec:
                    return 'url("")'
                url = str(spec["url"])
                key = key_by_url.get(url)
                row = cache.get_resource(key) if key else None
                if _is_success(row):
                    local = "/dashboard/inbox/resource?" + urlencode({"key": key})
                    return f'url("{local}")'
                return 'url("")'

            rewritten = _CSS_URL_RE.sub(rewrite_url, css).encode("utf-8")
            cache.put_resource(
                cache_key=css_key,
                account_id=account_id,
                mailbox=mailbox,
                uid=uid,
                url=stylesheet_url,
                url_hash=digest,
                content_type="text/css",
                body=rewritten,
                http_status=200,
                redirect_location=str(css_row.get("redirect_location") or ""),
                classification=str(css_row.get("classification") or "remote stylesheet"),
                tracking_score=int(css_row.get("tracking_score") or 0),
                error_state="",
            )
            stats["stylesheets_rewritten"] += 1

        stats["nested_ms"] = round((time.perf_counter() - started) * 1000.0, 2)
        return stats

    def _merge_nested_result(
        self,
        result: dict[str, Any],
        nested: dict[str, int | float],
        *,
        body_html: str,
        inventory: dict[str, Any],
        account_id: str,
        mailbox: str,
        uid: str,
    ) -> dict[str, Any]:
        diag = dict(result.get("diagnostics") or {})
        nested_attempted = int(nested.get("nested_attempted") or 0)
        nested_succeeded = int(nested.get("nested_succeeded") or 0)
        nested_failed = int(nested.get("nested_failed") or 0)
        stylesheet_failures = int(nested.get("stylesheet_failures") or 0)
        diag.update(nested)
        diag["genuine_attempted"] = int(diag.get("genuine_attempted") or 0) + nested_attempted
        diag["genuine_succeeded"] = int(diag.get("genuine_succeeded") or 0) + nested_succeeded
        diag["genuine_failed"] = int(diag.get("genuine_failed") or 0) + nested_failed + stylesheet_failures
        diag["cache_hits"] = int(diag.get("cache_hits") or 0) + int(nested.get("nested_cache_hits") or 0)
        diag["negative_cache_hits"] = int(diag.get("negative_cache_hits") or 0) + int(
            nested.get("nested_negative_cache_hits") or 0
        )
        diag["css_bounds"] = {
            "max_stylesheet_bytes": MAX_STYLESHEET_BYTES,
            "max_nested_resources_per_stylesheet": MAX_NESTED_RESOURCES_PER_STYLESHEET,
            "max_total_resources_per_message_cycle": MAX_TOTAL_RESOURCES_PER_MESSAGE_CYCLE,
            "max_cumulative_bytes_per_stylesheet": MAX_CUMULATIVE_BYTES_PER_STYLESHEET,
            "max_nested_execution_seconds": MAX_NESTED_EXECUTION_SECONDS,
            "max_concurrency": _MAX_PROXY_CONCURRENCY,
            "import_max_depth": CSS_IMPORT_MAX_DEPTH,
        }

        prior = str(result.get("render_state") or "success")
        extra_failures = nested_failed + stylesheet_failures
        if prior == "failure":
            state = "failure"
        elif extra_failures:
            state = "partial" if int(diag.get("cached_succeeded") or 0) or int(diag.get("genuine_succeeded") or 0) else "failure"
        else:
            state = prior
        result["render_state"] = state
        result["ok"] = state != "failure"
        result["full_html_available"] = state != "failure"
        result["diagnostics"] = diag
        result["network_requests_performed"] = int(result.get("network_requests_performed") or 0) + nested_attempted
        result["cache_only"] = bool(result.get("cache_only")) and not nested_attempted
        result["rendered_html"] = (
            self._render(
                body_html,
                inventory,
                account_id=account_id,
                mailbox=mailbox,
                uid=uid,
            )
            if state != "failure" and body_html
            else ""
        )
        return result

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
        result = super().fetch_inventory(
            inventory,
            account_id=account_id,
            mailbox=mailbox,
            uid=uid,
            refresh=refresh,
            body_html=body_html,
        )
        nested = self._process_stylesheets(
            inventory,
            account_id=account_id,
            mailbox=mailbox,
            uid=uid,
            refresh=refresh,
            network_allowed=bool(refresh) or not bool(result.get("cache_only")),
        )
        return self._merge_nested_result(
            result,
            nested,
            body_html=body_html,
            inventory=inventory,
            account_id=account_id,
            mailbox=mailbox,
            uid=uid,
        )

    def render_cached_message(
        self,
        *,
        account_id: str,
        mailbox: str,
        uid: str,
    ) -> dict[str, Any]:
        cache = self.base.mailbox_cache_store()
        detail = cache.get_message(account_id, mailbox, str(uid), include_body=True)
        if detail and detail.get("body_cached"):
            from .email_inventory_v963 import inventory_message

            body_html = str(detail.get("body_html") or "")
            inventory = inventory_message(body_html, str(detail.get("body") or ""))
            self._process_stylesheets(
                inventory,
                account_id=account_id,
                mailbox=mailbox,
                uid=uid,
                refresh=False,
                network_allowed=False,
            )
        return super().render_cached_message(
            account_id=account_id,
            mailbox=mailbox,
            uid=uid,
        )


__all__ = [
    "BoundedPassiveContentService",
    "CSS_IMPORT_MAX_DEPTH",
    "MAX_CUMULATIVE_BYTES_PER_STYLESHEET",
    "MAX_NESTED_EXECUTION_SECONDS",
    "MAX_NESTED_RESOURCES_PER_STYLESHEET",
    "MAX_STYLESHEET_BYTES",
    "MAX_TOTAL_RESOURCES_PER_MESSAGE_CYCLE",
]
