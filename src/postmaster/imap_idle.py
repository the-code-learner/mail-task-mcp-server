from __future__ import annotations

import contextlib
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

from .mail_protocols import parse_imap_capabilities


@dataclass(frozen=True)
class IdleSettings:
    reidle_seconds: float = 25 * 60.0
    socket_timeout_seconds: float = 60.0
    poll_seconds: float = 60.0
    reconnect_base_seconds: float = 1.0
    reconnect_max_seconds: float = 60.0


class IMAPIdleWatcher:
    """Reusable IMAP IDLE watcher running independently from MCP request threads.

    `connect` returns an authenticated IMAP connection. `on_change` is invoked for
    EXISTS/RECENT/EXPUNGE-like notifications. If IDLE is unavailable, `poll` is
    called at the configured interval instead. Fake connections may expose
    `idle_events(timeout_seconds)` to make behavior deterministic in tests.
    """

    def __init__(
        self,
        *,
        account_id: str,
        connect: Callable[[], Any],
        on_change: Callable[[str, Any], None],
        poll: Callable[[str], None] | None = None,
        settings: IdleSettings | None = None,
        stop_event: threading.Event | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ):
        self.account_id = account_id
        self.connect = connect
        self.on_change = on_change
        self.poll = poll
        self.settings = settings or IdleSettings()
        self.stop_event = stop_event or threading.Event()
        self.clock = clock
        self.sleeper = sleeper
        self.last_error = ""
        self.last_event_at: float | None = None
        self.mode = "unknown"
        self.reconnect_count = 0

    def stop(self) -> None:
        self.stop_event.set()

    def _sleep_interruptibly(self, seconds: float) -> None:
        end = self.clock() + max(0.0, seconds)
        while not self.stop_event.is_set():
            remaining = end - self.clock()
            if remaining <= 0:
                return
            self.sleeper(min(remaining, 0.25))

    @staticmethod
    def _event_is_change(event: Any) -> bool:
        if isinstance(event, tuple):
            text = " ".join(str(x) for x in event)
        else:
            text = str(event)
        upper = text.upper()
        return any(token in upper for token in ("EXISTS", "RECENT", "EXPUNGE", "FETCH"))

    def _idle_events_adapter(self, conn: Any, timeout_seconds: float):
        if hasattr(conn, "idle_events"):
            yield from conn.idle_events(timeout_seconds)
            return

        # Python 3.14+ imaplib exposes a public IDLE context manager. Prefer it
        # when available and keep the lower-level fallback isolated below.
        idle = getattr(conn, "idle", None)
        if callable(idle):
            try:
                with idle(duration=timeout_seconds) as idler:
                    for item in idler:
                        yield item
                return
            except TypeError:
                with idle(timeout_seconds) as idler:
                    for item in idler:
                        yield item
                return

        # Compatibility path for older imaplib. It deliberately uses a short
        # socket timeout so shutdown/re-IDLE cannot leave a zombie connection.
        tag = conn._new_tag()
        tag_bytes = tag if isinstance(tag, bytes) else str(tag).encode("ascii")
        conn.send(tag_bytes + b" IDLE\r\n")
        continuation = conn._get_line()
        if not (isinstance(continuation, bytes) and continuation.startswith(b"+")):
            raise RuntimeError(f"IMAP IDLE rejected: {continuation!r}")
        sock = getattr(conn, "sock", None)
        previous_timeout = sock.gettimeout() if sock and hasattr(sock, "gettimeout") else None
        if sock and hasattr(sock, "settimeout"):
            sock.settimeout(min(timeout_seconds, self.settings.socket_timeout_seconds))
        started = self.clock()
        try:
            while not self.stop_event.is_set() and self.clock() - started < timeout_seconds:
                try:
                    line = conn._get_line()
                except TimeoutError:
                    break
                except OSError as exc:
                    if "timed out" in str(exc).lower():
                        break
                    raise
                if line:
                    yield line
        finally:
            try:
                conn.send(b"DONE\r\n")
                # Complete the tagged IDLE command if the implementation exposes
                # the normal imaplib completion hook.
                if hasattr(conn, "_command_complete"):
                    conn._command_complete("IDLE", tag)
            finally:
                if sock and hasattr(sock, "settimeout"):
                    sock.settimeout(previous_timeout)

    def _run_idle_connection(self, conn: Any) -> None:
        self.mode = "idle"
        while not self.stop_event.is_set():
            started = self.clock()
            for event in self._idle_events_adapter(conn, self.settings.reidle_seconds):
                if self.stop_event.is_set():
                    break
                if self._event_is_change(event):
                    self.last_event_at = self.clock()
                    self.on_change(self.account_id, event)
                if self.clock() - started >= self.settings.reidle_seconds:
                    break
            if self.clock() - started < 0.001:
                # A broken server/fake that immediately exits IDLE must not spin.
                self._sleep_interruptibly(0.05)

    def _run_poll_connection(self) -> None:
        self.mode = "poll"
        if self.poll is None:
            self._sleep_interruptibly(self.settings.poll_seconds)
            return
        while not self.stop_event.is_set():
            self.poll(self.account_id)
            self._sleep_interruptibly(self.settings.poll_seconds)

    def run(self) -> None:
        backoff = max(0.0, self.settings.reconnect_base_seconds)
        while not self.stop_event.is_set():
            conn = None
            try:
                conn = self.connect()
                capabilities = parse_imap_capabilities(getattr(conn, "capabilities", ()))
                self.last_error = ""
                backoff = max(0.0, self.settings.reconnect_base_seconds)
                if capabilities["idle"]:
                    self._run_idle_connection(conn)
                else:
                    self._run_poll_connection()
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"
                self.reconnect_count += 1
                if self.stop_event.is_set():
                    break
                self._sleep_interruptibly(backoff)
                backoff = min(
                    max(backoff * 2.0, self.settings.reconnect_base_seconds),
                    self.settings.reconnect_max_seconds,
                )
            finally:
                if conn is not None:
                    with contextlib.suppress(Exception):
                        conn.logout()

    def status(self) -> dict[str, Any]:
        return {
            "account_id": self.account_id,
            "mode": self.mode,
            "running": not self.stop_event.is_set(),
            "reconnect_count": self.reconnect_count,
            "last_error": self.last_error,
            "last_event_monotonic": self.last_event_at,
        }


class IMAPIdleManager:
    """Own bounded background watcher threads and stop them cleanly at shutdown."""

    def __init__(self, max_accounts: int = 20):
        self.max_accounts = max(1, int(max_accounts))
        self._lock = threading.RLock()
        self._watchers: dict[str, IMAPIdleWatcher] = {}
        self._threads: dict[str, threading.Thread] = {}

    def start(self, watchers: list[IMAPIdleWatcher]) -> None:
        with self._lock:
            for watcher in watchers[: self.max_accounts]:
                if watcher.account_id in self._threads and self._threads[watcher.account_id].is_alive():
                    continue
                thread = threading.Thread(
                    target=watcher.run,
                    name=f"postmaster-imap-idle-{watcher.account_id}",
                    daemon=True,
                )
                self._watchers[watcher.account_id] = watcher
                self._threads[watcher.account_id] = thread
                thread.start()

    def stop(self, join_timeout: float = 5.0) -> None:
        with self._lock:
            watchers = list(self._watchers.values())
            threads = list(self._threads.values())
        for watcher in watchers:
            watcher.stop()
        deadline = time.monotonic() + max(0.0, join_timeout)
        for thread in threads:
            thread.join(max(0.0, deadline - time.monotonic()))

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "max_accounts": self.max_accounts,
                "watchers": [
                    {
                        **watcher.status(),
                        "thread_alive": bool(self._threads.get(account_id) and self._threads[account_id].is_alive()),
                    }
                    for account_id, watcher in self._watchers.items()
                ],
            }
