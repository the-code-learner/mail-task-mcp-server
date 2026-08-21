from __future__ import annotations

import threading
import time
import unittest

from postmaster.imap_idle import IMAPIdleManager, IMAPIdleWatcher, IdleSettings


def wait_until(predicate, timeout=0.5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return bool(predicate())


class FakeConnection:
    def __init__(self, *, capabilities=(b"IMAP4rev1", b"IDLE"), events=None, fail=False):
        self.capabilities = capabilities
        self.events = list(events or [])
        self.fail = fail
        self.logged_out = False
        self.idle_calls = 0

    def idle_events(self, timeout_seconds):
        self.idle_calls += 1
        if self.fail:
            raise ConnectionError("connection dropped")
        pending = list(self.events)
        self.events.clear()
        for event in pending:
            yield event
        time.sleep(min(float(timeout_seconds), 0.01))

    def logout(self):
        self.logged_out = True


class IMAPIdleTests(unittest.TestCase):
    def test_idle_event_calls_callback(self):
        stop = threading.Event()
        events = []
        conn = FakeConnection(events=[b"* 4 EXISTS"])
        def on_change(account_id, event):
            events.append((account_id, event))
            stop.set()
        watcher = IMAPIdleWatcher(
            account_id="a",
            connect=lambda: conn,
            on_change=on_change,
            settings=IdleSettings(reidle_seconds=0.02, poll_seconds=0.02),
            stop_event=stop,
        )
        watcher.run()
        self.assertEqual(events[0][0], "a")
        self.assertIn(b"EXISTS", events[0][1])
        self.assertEqual(watcher.mode, "idle")
        self.assertTrue(conn.logged_out)

    def test_non_change_idle_event_is_ignored(self):
        stop = threading.Event()
        seen = []
        conn = FakeConnection(events=[b"* OK still here"])
        watcher = IMAPIdleWatcher(
            account_id="a",
            connect=lambda: conn,
            on_change=lambda account_id, event: seen.append(event),
            settings=IdleSettings(reidle_seconds=0.01),
            stop_event=stop,
        )
        thread = threading.Thread(target=watcher.run)
        thread.start()
        self.assertTrue(wait_until(lambda: conn.idle_calls >= 2))
        stop.set()
        thread.join(1)
        self.assertFalse(thread.is_alive())
        self.assertEqual(seen, [])

    def test_reidle_periodically(self):
        stop = threading.Event()
        conn = FakeConnection()
        watcher = IMAPIdleWatcher(
            account_id="a",
            connect=lambda: conn,
            on_change=lambda *_: None,
            settings=IdleSettings(reidle_seconds=0.005),
            stop_event=stop,
        )
        thread = threading.Thread(target=watcher.run)
        thread.start()
        self.assertTrue(wait_until(lambda: conn.idle_calls >= 2))
        stop.set()
        thread.join(1)
        self.assertFalse(thread.is_alive())
        self.assertGreaterEqual(conn.idle_calls, 2)

    def test_reconnect_after_failure(self):
        stop = threading.Event()
        calls = []
        first = FakeConnection(fail=True)
        second = FakeConnection(events=[b"* 1 EXISTS"])
        connections = [first, second]
        def connect():
            calls.append(1)
            return connections.pop(0)
        def on_change(*_):
            stop.set()
        watcher = IMAPIdleWatcher(
            account_id="a",
            connect=connect,
            on_change=on_change,
            settings=IdleSettings(reidle_seconds=0.01, reconnect_base_seconds=0.001, reconnect_max_seconds=0.002),
            stop_event=stop,
        )
        watcher.run()
        self.assertGreaterEqual(len(calls), 2)
        self.assertGreaterEqual(watcher.reconnect_count, 1)
        self.assertTrue(first.logged_out)
        self.assertTrue(second.logged_out)

    def test_poll_fallback_when_idle_not_supported(self):
        stop = threading.Event()
        polls = []
        conn = FakeConnection(capabilities=(b"IMAP4rev1",))
        def poll(account_id):
            polls.append(account_id)
            stop.set()
        watcher = IMAPIdleWatcher(
            account_id="a",
            connect=lambda: conn,
            on_change=lambda *_: None,
            poll=poll,
            settings=IdleSettings(poll_seconds=0.001),
            stop_event=stop,
        )
        watcher.run()
        self.assertEqual(polls, ["a"])
        self.assertEqual(watcher.mode, "poll")

    def test_clean_shutdown_manager(self):
        manager = IMAPIdleManager(max_accounts=2)
        watcher = IMAPIdleWatcher(
            account_id="a",
            connect=lambda: FakeConnection(),
            on_change=lambda *_: None,
            settings=IdleSettings(reidle_seconds=0.01),
        )
        manager.start([watcher])
        self.assertTrue(wait_until(lambda: manager.status()["watchers"][0]["thread_alive"]))
        manager.stop(join_timeout=1)
        status = manager.status()
        self.assertEqual(len(status["watchers"]), 1)
        self.assertFalse(status["watchers"][0]["thread_alive"])

    def test_manager_respects_account_bound(self):
        manager = IMAPIdleManager(max_accounts=1)
        watchers = [
            IMAPIdleWatcher(account_id=name, connect=lambda: FakeConnection(), on_change=lambda *_: None, settings=IdleSettings(reidle_seconds=0.01))
            for name in ("a", "b")
        ]
        manager.start(watchers)
        self.assertTrue(wait_until(lambda: len(manager.status()["watchers"]) == 1))
        manager.stop(join_timeout=1)
        self.assertEqual(len(manager.status()["watchers"]), 1)


if __name__ == "__main__":
    unittest.main()
