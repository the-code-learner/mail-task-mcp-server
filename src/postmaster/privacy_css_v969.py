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
CSS_IMPORT_MAX_DEPTH = 0  # v9.6.9 deliberately sterilizes @import instead of recursing.

_CSS_URL_RE = re.compile(r"(?is)url\(\s*(['\"]?)(.*?)\1\s*\)")
_CSS_IMPORT_RE = re.compile(
    r"(?is)@import\s+(?:url\([^)]*\)|['\"][^'\"]*['\"])[^;]*;?"
)
_CSS_DANGEROUS_RE = re.compile(r"(?is)(?:expression\s*\(|-moz-binding\s*:|behavior\s*:)")
_NESTED_ALLOWED_TYPES = set(_ALLOWED_PROXY_TYPES) - {"text/css"}
_LOCAL_RESOURCE_PREFIX = "/dashboard/inbox/resource?"


def _is_success(row: dict[str, Any] | None) -> bool:
    return bool(
        row
        and int(row.get("http_status") or 0) == 200
        and row.get("body") is not None
        and not str(row.get("error_state") or "")
    )


def _clean_target(value: str) -> str:
    return str(value or "").strip().strip("\"'")


class BoundedPassiveContentService(PassiveContentService):
    """One-level, bounded passive CSS resolver shared by WebGUI and MCP.

    It is intentionally not a crawler: only successful top-level stylesheets already selected
    by the base passive pipeline are inspected; @import depth is zero; each CSS url(...) is
    resolved once and fetched only through the existing authenticated Privacy Proxy.
    """

    @staticmethod
    def _nested_spec(stylesheet_url: str, raw_target: str) -> dict[str, Any] | None:
        target = _clean_target(raw_target)
        if not target or target.startswith(("#", "data:", "cid:", _LOCAL_RESOURCE_PREFIX)):
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
        return record if _passive_allowed(record) else None

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
        else:
            with lock, cache._connect() as conn:
                conn.executemany(
                    "DELETE FROM mailbox_cache_remote_resources WHERE cache_key=?",
                    [(key,) for key in keys],
                )

    def _fetch_nested_remote(self, spec: dict[str, Any]) -> dict[str, Any]:
        proxy = self.base.privacy_proxy_client()
        try:
            fetched = proxy.fetch(
                str(spec["url"]),
                classification=str(spec.get("classification") or "remote image"),
                tracking_score=int(spec.get("tracking_score") or 0),
                request_kind="render",
                max_response_bytes=_MAX_PROXY_BYTES,
            )
            status = int(fetched.get("status") or 0)
            content_type = (
                str(fetched.get("content_type") or "").split(";", 1)[0].strip().casefold()
            )
            body = bytes(fetched.get("body") or b"") if status == 200 else None
            if body is not None:
                if len(body) > _MAX_PROXY_BYTES:
                    raise RuntimeError("nested_resource_size_limit")
                if content_type not in _NESTED_ALLOWED_TYPES:
                    raise RuntimeError("nested_resource_content_type_rejected")
            return {
                "status": status,
                "content_type": content_type,
                "body": body,
                "redirect_location": str(fetched.get("redirect_location") or ""),
                "error_state": "proxy_error" if str(fetched.get("error") or "") else "",
            }
        except Exception as exc:
            return {
                "status": None,
                "content_type": "",
                "body": None,
                "redirect_location": "",
                "error_state": type(exc).__name__,
            }

    def _store_nested(
        self,
        *,
        spec: dict[str, Any],
        fetched: dict[str, Any],
        account_id: str,
        mailbox: str,
        uid: str,
        cumulative_remaining: int,
    ) -> tuple[dict[str, Any], int]:
        cache = self.base.mailbox_cache_store()
        url = str(spec["url"])
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
        key = cache.resource_key(account_id, mailbox, uid, digest)
        body = fetched.get("body")
        error_state = str(fetched.get("error_state") or "")
        status = fetched.get("status")
        content_type = str(fetched.get("content_type") or "")
        if body is not None and not error_state:
            body = bytes(body)
            if len(body) > cumulative_remaining:
                body = None
                status = None
                content_type = ""
                error_state = "nested_cumulative_size_limit"
            else:
                cumulative_remaining -= len(body)
        row = cache.put_resource(
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
        return row, cumulative_remaining

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
            "top_positive_after": 0,
            "nested_ms": 0.0,
        }
        started = time.perf_counter()

        for top in top_level:
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
            stats["stylesheets_processed"] += 1
            raw_body = bytes(css_row.get("body") or b"")
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
            if _CSS_DANGEROUS_RE.search(css):
                css = _CSS_DANGEROUS_RE.sub("blocked(", css)

            refs: list[dict[str, Any]] = []
            key_by_url: dict[str, str] = {}
            seen: set[str] = set()
            for match in _CSS_URL_RE.finditer(css):
                target = _clean_target(match.group(2) or "")
                if target.startswith(_LOCAL_RESOURCE_PREFIX):
                    continue
                spec = self._nested_spec(stylesheet_url, target)
                if not spec:
                    continue
                url = str(spec["url"])
                if url in seen:
                    continue
                seen.add(url)
                if (
                    len(refs) >= MAX_NESTED_RESOURCES_PER_STYLESHEET
                    or remaining_global <= 0
                ):
                    stats["bounded_skipped"] += 1
                    continue
                refs.append(spec)
                remaining_global -= 1
                nested_digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
                key_by_url[url] = cache.resource_key(
                    account_id, mailbox, uid, nested_digest
                )

            stats["nested_discovered"] += len(refs)
            work: list[dict[str, Any]] = []
            for spec in refs:
                key = key_by_url[str(spec["url"])]
                if refresh:
                    self._delete_keys(cache, [key])
                row = cache.get_resource(key)
                if _is_success(row):
                    stats["nested_cache_hits"] += 1
                elif row is not None:
                    stats["nested_negative_cache_hits"] += 1
                elif network_allowed:
                    work.append(spec)

            cumulative_remaining = max(
                0, MAX_CUMULATIVE_BYTES_PER_STYLESHEET - len(raw_body)
            )
            if work and cumulative_remaining > 0:
                stats["nested_attempted"] += len(work)
                pool = ThreadPoolExecutor(
                    max_workers=_MAX_PROXY_CONCURRENCY,
                    thread_name_prefix="postmaster-v969-css",
                )
                futures = {pool.submit(self._fetch_nested_remote, spec): spec for spec in work}
                done, pending = wait(futures, timeout=MAX_NESTED_EXECUTION_SECONDS)
                for future in done:
                    spec = futures[future]
                    fetched = future.result()
                    row, cumulative_remaining = self._store_nested(
                        spec=spec,
                        fetched=fetched,
                        account_id=account_id,
                        mailbox=mailbox,
                        uid=uid,
                        cumulative_remaining=cumulative_remaining,
                    )
                    if _is_success(row):
                        stats["nested_succeeded"] += 1
                    else:
                        stats["nested_failed"] += 1
                for future in pending:
                    spec = futures[future]
                    future.cancel()
                    row, cumulative_remaining = self._store_nested(
                        spec=spec,
                        fetched={
                            "status": None,
                            "content_type": "",
                            "body": None,
                            "redirect_location": "",
                            "error_state": "nested_timeout",
                        },
                        account_id=account_id,
                        mailbox=mailbox,
                        uid=uid,
                        cumulative_remaining=cumulative_remaining,
                    )
                    stats["nested_failed"] += 1
                pool.shutdown(wait=False, cancel_futures=True)
            elif work:
                for spec in work:
                    self._store_nested(
                        spec=spec,
                        fetched={
                            "status": None,
                            "content_type": "",
                            "body": None,
                            "redirect_location": "",
                            "error_state": "nested_cumulative_size_limit",
                        },
                        account_id=account_id,
                        mailbox=mailbox,
                        uid=uid,
                        cumulative_remaining=0,
                    )
                stats["nested_failed"] += len(work)

            def rewrite_url(match: re.Match[str]) -> str:
                target = _clean_target(match.group(2) or "")
                if not target or target.startswith(("#", "data:", "cid:", _LOCAL_RESOURCE_PREFIX)):
                    return match.group(0)
                spec = self._nested_spec(stylesheet_url, target)
                if not spec:
                    return 'url("")'
                key = key_by_url.get(str(spec["url"]))
                row = cache.get_resource(key) if key else None
                if _is_success(row):
                    local = _LOCAL_RESOURCE_PREFIX + urlencode({"key": key})
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

        positive_after = 0
        for row in top_level:
            url = str(row.get("url") or "")
            _, key = self._key(cache, account_id, mailbox, uid, url)
            if _is_success(cache.get_resource(key)):
                positive_after += 1
        stats["top_positive_after"] = positive_after
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
            "mime_allowlist": sorted(_NESTED_ALLOWED_TYPES),
            "ssrf_redirect_auth": "existing Privacy Proxy enforcement",
        }

        prior = str(result.get("render_state") or "success")
        extra_failures = nested_failed + stylesheet_failures
        if prior == "failure":
            state = "failure"
        elif extra_failures:
            state = "partial" if int(nested.get("top_positive_after") or 0) > 0 else "failure"
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
