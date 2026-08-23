from __future__ import annotations

import copy
import threading
import time
from typing import Any, Callable


_CACHE_LOCK = threading.RLock()
_CACHE: dict[str, tuple[float, Any]] = {}
_INFLIGHT: dict[str, threading.Event] = {}


def invalidate_structural_cache(*keys: str) -> None:
    with _CACHE_LOCK:
        if keys:
            for key in keys:
                _CACHE.pop(key, None)
        else:
            _CACHE.clear()


def cached_structural(key: str, loader: Callable[[], Any], *, ttl: float = 2.5) -> Any:
    """Short-lived cache with request coalescing for stable structural data."""
    now = time.monotonic()
    with _CACHE_LOCK:
        cached = _CACHE.get(key)
        if cached and cached[0] > now:
            return copy.deepcopy(cached[1])
        event = _INFLIGHT.get(key)
        if event is None:
            event = threading.Event()
            _INFLIGHT[key] = event
            leader = True
        else:
            leader = False

    if not leader:
        event.wait(timeout=10.0)
        with _CACHE_LOCK:
            cached = _CACHE.get(key)
            if cached:
                return copy.deepcopy(cached[1])
        return cached_structural(key, loader, ttl=ttl)

    try:
        value = loader()
        with _CACHE_LOCK:
            _CACHE[key] = (time.monotonic() + max(0.1, float(ttl)), copy.deepcopy(value))
        return copy.deepcopy(value)
    finally:
        with _CACHE_LOCK:
            finished = _INFLIGHT.pop(key, None)
            if finished is not None:
                finished.set()


def active_projects(base: Any) -> list[dict[str, Any]]:
    def load() -> list[dict[str, Any]]:
        result = base._safe_call(base.scheduler().list_projects)
        rows = result if isinstance(result, list) else []
        return [dict(row) for row in rows if isinstance(row, dict) and bool(row.get("active", True))]

    return cached_structural("projects", load)


def owners(base: Any) -> list[dict[str, Any]]:
    def load() -> list[dict[str, Any]]:
        result = base._safe_call(base.scheduler().list_owners)
        return [dict(row) for row in result] if isinstance(result, list) else []

    return cached_structural("owners", load)


def accounts(base: Any) -> list[dict[str, Any]]:
    def load() -> list[dict[str, Any]]:
        try:
            rows = base.account_store().list_accounts()
        except Exception:
            rows = []
        return [dict(row) for row in rows if isinstance(row, dict)]

    return cached_structural("accounts", load)


class _SchedulerProxy:
    def __init__(self, base: Any, scheduler: Any) -> None:
        self._base = base
        self._scheduler = scheduler

    def list_projects(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        return active_projects(self._base)

    def list_owners(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        return owners(self._base)

    def list_jobs(self, *args: Any, **kwargs: Any) -> Any:
        kwargs["limit"] = min(int(kwargs.get("limit", 251)), 251)
        return self._scheduler.list_jobs(*args, **kwargs)

    def list_due_jobs(self, *args: Any, **kwargs: Any) -> Any:
        kwargs["limit"] = min(int(kwargs.get("limit", 251)), 251)
        return self._scheduler.list_due_jobs(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._scheduler, name)


class _StoreProxy:
    def __init__(self, store: Any) -> None:
        self._store = store

    def list_items(self, *args: Any, **kwargs: Any) -> Any:
        kwargs["limit"] = min(int(kwargs.get("limit", 101)), 101)
        return self._store.list_items(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._store, name)


class _ContextProxy:
    def __init__(self, context: Any) -> None:
        self._context = context
        self.store = _StoreProxy(context.store)

    def search(self, *args: Any, **kwargs: Any) -> Any:
        kwargs["limit"] = min(int(kwargs.get("limit", 51)), 51)
        return self._context.search(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._context, name)


class _FileProxy:
    def __init__(self, store: Any) -> None:
        self._store = store

    def list_files(self, *args: Any, **kwargs: Any) -> Any:
        kwargs["limit"] = min(int(kwargs.get("limit", 101)), 101)
        return self._store.list_files(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._store, name)


class _AnalyticsProxy:
    def __init__(self, store: Any) -> None:
        self._store = store

    @staticmethod
    def _bounded(kwargs: dict[str, Any], cap: int = 201) -> dict[str, Any]:
        values = dict(kwargs)
        values["limit"] = min(int(values.get("limit", cap)), cap)
        return values

    def list_campaigns(self, *args: Any, **kwargs: Any) -> Any:
        return self._store.list_campaigns(*args, **self._bounded(kwargs))

    def list_deliveries(self, *args: Any, **kwargs: Any) -> Any:
        return self._store.list_deliveries(*args, **self._bounded(kwargs))

    def list_open_events(self, *args: Any, **kwargs: Any) -> Any:
        return self._store.list_open_events(*args, **self._bounded(kwargs))

    def __getattr__(self, name: str) -> Any:
        return getattr(self._store, name)


class BoundedBaseProxy:
    """Delegate to runtime base while bounding list sources and sharing structural reads."""

    def __init__(self, base: Any) -> None:
        self._base = base

    def scheduler(self) -> _SchedulerProxy:
        return _SchedulerProxy(self._base, self._base.scheduler())

    def context_engine(self) -> _ContextProxy:
        return _ContextProxy(self._base.context_engine())

    def file_store(self) -> _FileProxy:
        return _FileProxy(self._base.file_store())

    def analytics_store(self) -> _AnalyticsProxy:
        return _AnalyticsProxy(self._base.analytics_store())

    def list_email_accounts(self) -> dict[str, Any]:
        return {"ok": True, "accounts": accounts(self._base)}

    def __getattr__(self, name: str) -> Any:
        return getattr(self._base, name)
