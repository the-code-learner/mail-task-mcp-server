from __future__ import annotations

import atexit
import base64
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import inspect
import json
import os
from pathlib import Path
import shutil
import socket
import struct
import subprocess
import tempfile
import threading
import time
from types import SimpleNamespace
from urllib import request as urllib_request
import unittest

from starlette.responses import HTMLResponse

from postmaster.webgui_release_identity import install_webgui_release_identity
from postmaster.webgui_v962_views import VIEWS
from postmaster.webgui_v970 import (
    ENTERPRISE_SCRIPT,
    ENTERPRISE_STYLE,
    NAV_GROUPS,
    VIEW_LABELS,
    enterprise_nav,
    install_webgui_v970,
)


def _fake_shell(_request):
    return HTMLResponse(
        '<!doctype html><html><head></head><body><div class="shell">'
        '<nav>legacy</nav><main>'
        '<form method="post" action="/dashboard/compose/send">'
        '<input type="hidden" name="csrf" value="fixed-token">'
        '<input name="subject" value="Existing payload">'
        '<button type="submit" name="compose_action" value="send">Send</button>'
        '</form></main></div><script id="existing-lifecycle">window.existing=true;</script>'
        '</body></html>',
        headers={"Cache-Control": "private, no-store", "X-Postmaster-WebGUI": "9.6.2-lazy"},
    )


def _fake_v962():
    base_style = "/* baseline */"

    def styles():
        return (
            base_style
            + "\n/* legacy-last */"
            + "\n.v951-pagehead h2{font-size:24px}"
            + "\n.v951-metrics{gap:10px;border-radius:11px}"
            + "\n.v951-metric{padding:13px}"
            + "\n@media(max-width:820px){.v951-toolbar{flex-wrap:wrap}}"
        )

    return SimpleNamespace(
        BASE_STYLE=base_style,
        SCRIPT='<script id="baseline-script">window.baseline=true;</script>',
        _styles=styles,
        _nav=lambda: '<nav>legacy</nav>',
        _shell=_fake_shell,
    )


def _browser_path() -> str | None:
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        path = shutil.which(name)
        if path:
            return path
    return None


def _recv_exact(sock: socket.socket, size: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        chunk = sock.recv(size - len(chunks))
        if not chunk:
            raise ConnectionError("Chrome DevTools websocket closed unexpectedly")
        chunks.extend(chunk)
    return bytes(chunks)


def _ws_send_frame(sock: socket.socket, payload: bytes, opcode: int = 1) -> None:
    mask = os.urandom(4)
    header = bytearray([0x80 | opcode])
    size = len(payload)
    if size < 126:
        header.append(0x80 | size)
    elif size < 65536:
        header.append(0x80 | 126)
        header.extend(struct.pack("!H", size))
    else:
        header.append(0x80 | 127)
        header.extend(struct.pack("!Q", size))
    header.extend(mask)
    masked = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
    sock.sendall(header + masked)


def _ws_send(sock: socket.socket, text: str) -> None:
    _ws_send_frame(sock, text.encode("utf-8"), 1)


def _ws_recv(sock: socket.socket) -> str:
    while True:
        first, second = _recv_exact(sock, 2)
        opcode = first & 0x0F
        masked = bool(second & 0x80)
        size = second & 0x7F
        if size == 126:
            size = struct.unpack("!H", _recv_exact(sock, 2))[0]
        elif size == 127:
            size = struct.unpack("!Q", _recv_exact(sock, 8))[0]
        mask = _recv_exact(sock, 4) if masked else b""
        payload = _recv_exact(sock, size)
        if masked:
            payload = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
        if opcode == 8:
            raise ConnectionError("Chrome DevTools websocket sent a close frame")
        if opcode == 9:
            _ws_send_frame(sock, payload, 10)
            continue
        if opcode == 1:
            return payload.decode("utf-8")


def _ws_connect(url: str) -> socket.socket:
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if parsed.scheme != "ws" or not parsed.hostname or not parsed.port:
        raise AssertionError(f"Unexpected DevTools websocket URL: {url}")
    sock = socket.create_connection((parsed.hostname, parsed.port), timeout=5)
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    path = parsed.path + (("?" + parsed.query) if parsed.query else "")
    handshake = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {parsed.hostname}:{parsed.port}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n\r\n"
    )
    sock.sendall(handshake.encode("ascii"))
    response = bytearray()
    while b"\r\n\r\n" not in response:
        response.extend(sock.recv(4096))
        if len(response) > 65536:
            raise AssertionError("Oversized DevTools websocket handshake")
    status = bytes(response).split(b"\r\n", 1)[0]
    if b" 101 " not in status:
        sock.close()
        raise AssertionError(f"DevTools websocket upgrade failed: {status!r}")
    return sock


def _cdp_call(sock: socket.socket, message_id: int, method: str, params: dict | None = None) -> dict:
    message = {"id": message_id, "method": method}
    if params is not None:
        message["params"] = params
    _ws_send(sock, json.dumps(message, separators=(",", ":")))
    while True:
        payload = json.loads(_ws_recv(sock))
        if payload.get("id") != message_id:
            continue
        if "error" in payload:
            raise AssertionError(f"CDP {method} failed: {payload['error']}")
        return payload


def _browser_document(view: str) -> str:
    legacy = r'''
.tab-panel{display:none}.tab-panel.active{display:block}
.v951-pagehead h2{font-size:24px}.v951-metrics{display:grid;gap:10px;border-radius:11px}.v951-metric{padding:13px}
.v951-toolbar{display:flex;flex-wrap:wrap}.v963-detail-actions{display:flex;gap:8px}.v963-detail-actions a{display:inline-flex}
'''
    mapping = {key: [title, subtitle] for key, (title, subtitle) in VIEW_LABELS.items()}
    script = ENTERPRISE_SCRIPT.replace(
        "%VIEW_LABELS%", json.dumps(mapping, ensure_ascii=False, separators=(",", ":"))
    )
    script = script.replace(
        "function syncPresentation() { syncContext(); initTaskCalendar(); initKnowledgeDrilldown(); }",
        "function syncPresentation() { window.__v970SyncCalls=(window.__v970SyncCalls||0)+1; syncContext(); initTaskCalendar(); initKnowledgeDrilldown(); }",
    )
    script = script.replace(
        "const presentationObserver = new MutationObserver(schedulePresentationSync);",
        "const presentationObserver = new MutationObserver(() => { window.__v970ObserverCallbacks=(window.__v970ObserverCallbacks||0)+1; schedulePresentationSync(); });",
    )
    nav = enterprise_nav()
    knowledge_open = " open" if view == "knowledge" else ""
    special = {"overview", "inbox", "scheduler", "knowledge"}
    other_panels = "".join(
        f'''<section class="tab-panel {'active' if view == name else ''}" id="panel-{name}" data-panel="{name}">
          <div class="v951-pagehead"><div><h2>{VIEW_LABELS[name][0]}</h2><p class="muted">Acceptance surface</p></div><button type="button" class="primary">Primary action</button></div>
          <form class="v951-toolbar"><label>Filter <input value="all"></label><button type="button">Apply</button></form>
          <div class="scroll"><table><thead><tr><th>Name</th><th>Status</th></tr></thead><tbody><tr><td>Representative row</td><td>Ready</td></tr></tbody></table></div>
        </section>'''
        for name in VIEWS
        if name not in special
    )
    panels = f'''
<section class="tab-panel {'active' if view == 'overview' else ''}" id="panel-overview" data-panel="overview">
  <div class="v951-pagehead"><div><h2 id="computed-heading">Operations Dashboard</h2></div><button type="button" class="primary">Primary action</button></div>
  <div class="v951-metrics" id="computed-metrics"><div class="v951-metric" id="computed-metric"><span>Runtime</span><strong>9.7</strong></div><div class="v951-metric"><span>Tasks</span><strong>2</strong></div></div>
  <form class="v951-toolbar" id="computed-toolbar"><label>Filter <input value="all"></label><button type="button">Apply</button></form>
</section>
<section class="tab-panel {'active' if view == 'inbox' else ''}" id="panel-inbox" data-panel="inbox">
  <div class="v963-inbox-head"><div><h2>Inbox</h2></div></div>
  <nav class="v960-mailbox-tabs"><a>Inbox</a><a>Sent</a></nav>
  <form class="v951-toolbar"><label>Subject <input></label></form>
  <div class="scroll" id="reader-scroll"><table class="v960-mail-table"><thead><tr><th></th><th>From</th><th>Subject</th><th>Date</th></tr></thead><tbody>
    <tr class="v963-mail-row"><td></td><td>Sender</td><td>List row</td><td>today</td></tr>
    <tr class="v960-inline-detail"><td colspan="4"><div class="v963-detail" id="reader-detail">
      <div class="v963-inbox-head"><div><h3 id="reader-subject">Reader subject</h3><p class="small muted">From: sender@example.invalid</p></div><a>Close</a></div>
      <div class="v963-detail-actions"><a id="reader-reply"><button type="button">Reply</button></a><a id="reader-reply-all"><button type="button">Reply All</button></a><a id="reader-forward"><button type="button">Forward</button></a></div>
      <div class="v963-safe-email" id="reader-body">Safe reader body is visible and scrollable.</div>
    </div></td></tr>
  </tbody></table></div>
  <div class="v960-pagination">Page 1</div><details class="card v963-proxy-card"><summary>Privacy</summary></details>
</section>
<section class="tab-panel {'active' if view == 'scheduler' else ''}" id="panel-scheduler" data-panel="scheduler">
  <section class="card wide"><div class="task-calendar-toolbar"><div><div class="task-calendar-month">August 2026</div></div><div class="task-calendar-controls"><button>Today</button></div></div>
  <div class="task-calendar-shell" id="calendar-shell"><div class="task-calendar-head">{''.join(f'<div>{name}</div>' for name in ('Mon','Tue','Wed','Thu','Fri','Sat','Sun'))}</div>
  <div class="task-calendar-grid" id="calendar-grid">
    <div class="task-calendar-day"><div class="task-calendar-date">1</div><a class="task-calendar-event" href="#one">Task A<small>09:00 · Project</small></a></div>
    <div class="task-calendar-day"><div class="task-calendar-date">2</div><a class="task-calendar-event" href="#two">Task B<small>10:00 · Project</small></a></div>
    <div class="task-calendar-day"><div class="task-calendar-date">3</div></div><div class="task-calendar-day"><div class="task-calendar-date">4</div></div><div class="task-calendar-day"><div class="task-calendar-date">5</div></div><div class="task-calendar-day"><div class="task-calendar-date">6</div></div><div class="task-calendar-day"><div class="task-calendar-date">7</div></div>
  </div></div></section>
  <section class="card wide"><form action="/dashboard/job/update"><label>Task name<input value="Task A"></label><button type="button" class="primary">Save</button></form></section>
</section>
<section class="tab-panel {'active' if view == 'knowledge' else ''}" id="panel-knowledge" data-panel="knowledge"><div class="grid">
  <section class="card wide"><div class="panel-title"><div><h2>Memory One</h2></div></div><div class="markdown-viewer">Knowledge detail</div></section>
  <section class="card wide"><h2>Knowledge / Skills</h2></section>
  <details class="v962-collapsible" data-v962-state-key="knowledge-editor"{knowledge_open}><summary>Add memory / skill</summary><div class="v962-collapsible-body"><section class="card wide"><form><div class="v951-formgrid"><label>Priority<input value="0.70"></label><label>Title<input value="Memory One"></label><label class="wide">Content<textarea>Content</textarea></label><label><input type="checkbox" checked> Always include</label><label><input type="checkbox" checked> Enabled</label></div></form></section></div></details>
  <section class="card wide"><h2>Search</h2></section>
  <section class="card wide"><div class="scroll"><table><thead><tr><th>Item</th><th>Kind</th><th>Scopes</th><th>Priority</th><th></th></tr></thead><tbody><tr><td><strong>Memory One</strong><div class="small muted mono">k1</div></td><td><span class="badge">memory</span></td><td>Project Alpha</td><td>0.70</td><td class="actions"><button>View</button><button>Edit</button></td></tr></tbody></table></div></section>
</div></section>
{other_panels}
'''
    measurement = r'''
<script>
window.addEventListener('load', async () => {
  const later = ms => new Promise(resolve => setTimeout(resolve,ms));
  await later(0);
  let themeStorageReadable=true;
  try { localStorage.getItem('postmaster:v970:theme'); } catch (_error) { themeStorageReadable=false; }

  const observerBefore=window.__v970ObserverCallbacks||0;
  const syncBefore=window.__v970SyncCalls||0;
  const external=document.createElement('span');
  external.hidden=true; external.dataset.v970ExternalMutation='1';
  (document.querySelector('.tab-panel.active') || document.body).append(external);
  await later(0);
  const observerAfter=window.__v970ObserverCallbacks||0;
  const syncAfter=window.__v970SyncCalls||0;
  await later(40);
  const observerQuiet=window.__v970ObserverCallbacks||0;
  const syncQuiet=window.__v970SyncCalls||0;
  const presentationConverged=observerAfter===observerQuiet && syncAfter===syncQuiet;
  const observerDelta=observerAfter-observerBefore;
  const syncDelta=syncAfter-syncBefore;

  if (document.body.dataset.v970View === 'scheduler') {
    const days=document.querySelectorAll('#calendar-grid .task-calendar-day'); if(days[1]) days[1].click();
  }
  const rect = selector => { const el=document.querySelector(selector); if(!el)return {w:0,h:0}; const r=el.getBoundingClientRect(); return {w:r.width,h:r.height}; };
  const metrics=document.querySelector('#computed-metrics'), metric=document.querySelector('#computed-metric'), toolbar=document.querySelector('#computed-toolbar');
  const shell=document.querySelector('#calendar-shell'), grid=document.querySelector('#calendar-grid');
  const active=document.querySelector('.tab-panel.active');
  const activePrimary=active?.querySelector('.primary,button,a.btn');
  const activePrimaryRect=activePrimary?.getBoundingClientRect();
  const trigger=document.querySelector('[data-v970-more]'); trigger?.focus(); trigger?.click();
  const sheet=document.querySelector('#v970-more-sheet'), first=sheet?.querySelector('[data-v970-more-close]');
  const focusables=[...(sheet?.querySelectorAll('a[href],button:not([disabled])') || [])]; const last=focusables[focusables.length-1];
  if(last){last.focus();last.dispatchEvent(new KeyboardEvent('keydown',{key:'Tab',bubbles:true,cancelable:true}));}
  const trapped=document.activeElement===first;
  const openState={role:sheet?.getAttribute('role'),ariaModal:sheet?.getAttribute('aria-modal'),ariaHidden:sheet?.getAttribute('aria-hidden'),workspaceInert:document.querySelector('.v970-workspace')?.hasAttribute('inert'),initialFocus:trapped};
  document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true,cancelable:true}));
  const result={
    origin:location.origin, documentLoadComplete:document.readyState==='complete', eventLoopResponsive:true,
    presentationConverged, observerDelta, syncDelta, mutationStorm:!presentationConverged || observerDelta>2 || syncDelta>2, themeStorageReadable,
    viewport:{w:innerWidth,h:innerHeight}, activeView:document.body.dataset.v970View || '', activeVisible:active ? getComputedStyle(active).display !== 'none' : false, activePrimaryVisible:activePrimaryRect ? activePrimaryRect.width > 0 && activePrimaryRect.height > 0 : true,
    headingFont:getComputedStyle(document.querySelector('#computed-heading')).fontSize,
    metricsGap:metrics ? getComputedStyle(metrics).gap : '', metricsRadius:metrics ? getComputedStyle(metrics).borderRadius : '', metricPadding:metric ? getComputedStyle(metric).paddingTop : '', toolbarWrap:toolbar ? getComputedStyle(toolbar).flexWrap : '',
    readerScrollDisplay:getComputedStyle(document.querySelector('#reader-scroll')).display, readerDetail:rect('#reader-detail'), readerSubject:rect('#reader-subject'), readerBody:rect('#reader-body'), reply:rect('#reader-reply'), replyAll:rect('#reader-reply-all'), forward:rect('#reader-forward'), horizontalOverflow:document.documentElement.scrollWidth-document.documentElement.clientWidth,
    calendarOverflow:shell ? getComputedStyle(shell).overflowX : '', calendarScroll:shell ? shell.scrollWidth-shell.clientWidth : -1, calendarColumns:grid ? getComputedStyle(grid).gridTemplateColumns.trim().split(/\s+/).length : 0, selectedDate:document.querySelector('.task-calendar-day.v970-selected .task-calendar-date')?.textContent?.trim() || '', agendaText:document.querySelector('.v970-task-day-agenda')?.textContent?.replace(/\s+/g,' ').trim() || '',
    knowledgeDetailClass:document.querySelector('#panel-knowledge .v970-knowledge-detail') !== null, knowledgeBack:document.querySelector('#panel-knowledge .v970-knowledge-detail > .v970-overlay-back') !== null, knowledgeEditorClass:document.querySelector('#panel-knowledge .v970-knowledge-editor') !== null, knowledgeEditorFixed:document.querySelector('#panel-knowledge .v970-knowledge-editor') ? getComputedStyle(document.querySelector('#panel-knowledge .v970-knowledge-editor')).position : '', knowledgeEditorBack:document.querySelector('#panel-knowledge .v970-knowledge-editor .v970-overlay-back') !== null,
    more:openState, moreClosed:sheet ? !sheet.classList.contains('v970-open') : false, returnFocus:document.activeElement===trigger, workspaceRestored:!document.querySelector('.v970-workspace')?.hasAttribute('inert')
  };
  document.querySelector('#v970-browser-result').textContent=JSON.stringify(result);
});
</script>
'''
    return f'''<!doctype html><html><head><meta charset="utf-8"><style>{legacy}\n{ENTERPRISE_STYLE}</style></head><body><div class="shell">{nav}<div class="v970-workspace"><header class="v970-contextbar"><div class="v970-context-title"><strong data-v970-context-title>Dashboard</strong><span data-v970-context-subtitle>Test</span></div><div class="v970-context-actions"><button class="v970-theme-toggle" type="button" data-v970-theme-toggle aria-label="Theme: system. Activate to change.">◐</button></div></header><main>{panels}</main></div></div>{script}<pre id="v970-browser-result"></pre>{measurement}</body></html>'''


@contextmanager
def _serve_browser_document(document: str):
    payload = document.encode("utf-8")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.split("?", 1)[0] != "/fixture":
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, _format, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/fixture"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)


class _BrowserSession:
    def __init__(self):
        browser = _browser_path()
        if not browser:
            raise AssertionError("Canonical browser acceptance requires Chrome/Chromium")
        self._tmp = tempfile.TemporaryDirectory(prefix="postmaster-v970-browser-", ignore_cleanup_errors=True)
        profile = Path(self._tmp.name) / "chrome-profile"
        profile.mkdir()
        self._proc = subprocess.Popen(
            [
                browser,
                "--headless=new",
                "--no-sandbox",
                "--disable-gpu",
                "--disable-dev-shm-usage",
                "--disable-background-networking",
                "--disable-default-apps",
                "--disable-extensions",
                "--disable-sync",
                "--metrics-recording-only",
                "--no-first-run",
                "--remote-debugging-port=0",
                "--remote-allow-origins=*",
                f"--user-data-dir={profile}",
                "about:blank",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self._ws = None
        self._message_id = 0
        try:
            active_port = profile / "DevToolsActivePort"
            deadline = time.monotonic() + 8
            while not active_port.exists() and time.monotonic() < deadline:
                if self._proc.poll() is not None:
                    raise AssertionError(f"Chrome exited before exposing DevTools: {self._proc.returncode}")
                time.sleep(0.05)
            if not active_port.exists():
                raise AssertionError("Chrome did not expose DevToolsActivePort")
            port = int(active_port.read_text(encoding="utf-8").splitlines()[0])
            target_deadline = time.monotonic() + 5
            targets = []
            while time.monotonic() < target_deadline:
                try:
                    with urllib_request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=2) as response:
                        targets = json.load(response)
                    if targets:
                        break
                except OSError:
                    time.sleep(0.05)
            target = next((item for item in targets if item.get("type") == "page"), None)
            if not target:
                raise AssertionError(f"No Chrome page target available: {targets!r}")
            self._ws = _ws_connect(target["webSocketDebuggerUrl"])
            self._call("Page.enable")
        except Exception:
            self.close()
            raise

    def _call(self, method: str, params: dict | None = None) -> dict:
        if self._ws is None:
            raise AssertionError("Chrome DevTools session is not available")
        self._message_id += 1
        return _cdp_call(self._ws, self._message_id, method, params)

    def fixture(self, view: str, width: int, height: int) -> dict[str, object]:
        document = _browser_document(view)
        with _serve_browser_document(document) as url:
            self._call(
                "Emulation.setDeviceMetricsOverride",
                {"width": width, "height": height, "deviceScaleFactor": 1, "mobile": False},
            )
            navigation = self._call("Page.navigate", {"url": url})
            error_text = navigation.get("result", {}).get("errorText")
            if error_text:
                raise AssertionError(f"Browser fixture navigation failed: {error_text}")
            expected_url = json.dumps(url)
            expression = (
                "(() => {"
                f"if (location.href !== {expected_url}) return '';"
                "if (document.readyState !== 'complete') return '';"
                "return document.querySelector('#v970-browser-result')?.textContent || '';"
                "})()"
            )
            result_text = ""
            for _attempt in range(40):
                evaluated = self._call(
                    "Runtime.evaluate",
                    {"expression": expression, "returnByValue": True},
                )
                result_text = evaluated.get("result", {}).get("result", {}).get("value", "")
                if result_text:
                    break
                time.sleep(0.025)
            if not result_text:
                raise AssertionError("Browser fixture did not emit acceptance results")
            return json.loads(result_text)

    def close(self) -> None:
        if self._ws is not None:
            try:
                self._ws.close()
            except OSError:
                pass
            self._ws = None
        if hasattr(self, "_proc") and self._proc.poll() is None:
            self._proc.kill()
        if hasattr(self, "_proc"):
            try:
                self._proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                pass
        if hasattr(self, "_tmp"):
            self._tmp.cleanup()


_BROWSER_SESSION: _BrowserSession | None = None


def _browser_session() -> _BrowserSession:
    global _BROWSER_SESSION
    if _BROWSER_SESSION is None:
        _BROWSER_SESSION = _BrowserSession()
        atexit.register(_BROWSER_SESSION.close)
    return _BROWSER_SESSION


def _browser_fixture(view: str, width: int, height: int) -> dict[str, object]:
    return _browser_session().fixture(view, width, height)


class WebGuiV970Tests(unittest.TestCase):
    # Existing seven UI-specific tests remain in place.
    def test_installer_has_no_app_or_backend_registry_parameter(self):
        self.assertEqual(list(inspect.signature(install_webgui_v970).parameters), ["v962"])

    def test_navigation_preserves_every_existing_lazy_view(self):
        covered = {view for _heading, links in NAV_GROUPS for view, _label, _code in links}
        self.assertEqual(covered, set(VIEWS))
        self.assertEqual(set(VIEW_LABELS), set(VIEWS))
        nav = enterprise_nav()
        for view in VIEWS:
            self.assertIn(f'data-v962-nav="{view}"', nav)
            self.assertIn(f'data-v970-nav="{view}"', nav)

    def test_shell_overlay_preserves_existing_form_contract_and_script(self):
        v962 = _fake_v962()
        install_webgui_v970(v962)
        response = v962._shell(object())
        html = response.body.decode("utf-8")
        self.assertIn('method="post" action="/dashboard/compose/send"', html)
        self.assertIn('name="csrf" value="fixed-token"', html)
        self.assertIn('name="subject" value="Existing payload"', html)
        self.assertIn('name="compose_action" value="send"', html)
        self.assertIn('id="existing-lifecycle"', html)
        self.assertIn('class="v970-workspace"', html)
        self.assertIn('class="v970-contextbar"', html)
        self.assertEqual(response.headers["x-postmaster-webgui-design"], "v9.7.0-enterprise-refresh")
        self.assertEqual(response.headers["cache-control"], "private, no-store")

    def test_install_is_idempotent_for_style_and_script(self):
        v962 = _fake_v962()
        install_webgui_v970(v962)
        install_webgui_v970(v962)
        self.assertEqual(v962.BASE_STYLE.count("webgui-v970-enterprise-operational-refresh"), 0)
        self.assertEqual(v962._styles().count("webgui-v970-enterprise-operational-refresh"), 1)
        self.assertEqual(v962.SCRIPT.count('id="v970-enterprise-shell"'), 1)

    def test_release_identity_still_uses_local_release_version(self):
        v962 = _fake_v962()
        install_webgui_v970(v962)
        install_webgui_release_identity(v962, "9.7.0")
        self.assertIn("WebGUI v9.7.0 · lazy fragments", v962._nav())
        response = v962._shell(object())
        self.assertEqual(response.headers["x-postmaster-webgui"], "9.7.0-lazy")

    def test_responsive_accessibility_and_theme_contracts_are_present(self):
        for token in (
            "@media(max-width:1279px)",
            "@media(max-width:767px)",
            "@media(max-width:430px)",
            "prefers-reduced-motion:reduce",
            "prefers-color-scheme:light",
            'data-v970-theme="light"',
            ":focus-visible",
            "v970-mobile-nav",
            "task-calendar-grid",
            "#panel-inbox:has(.v963-detail)",
        ):
            self.assertIn(token, ENTERPRISE_STYLE)
        self.assertIn("MutationObserver", ENTERPRISE_SCRIPT)
        self.assertIn("aria-expanded", ENTERPRISE_SCRIPT)

    def test_mobile_navigation_keeps_existing_lazy_contract(self):
        nav = enterprise_nav()
        self.assertIn('aria-label="Mobile primary navigation"', nav)
        self.assertIn("data-v970-more", nav)
        self.assertIn('aria-controls="v970-more-sheet"', nav)
        for view in ("overview", "inbox", "compose", "scheduler"):
            self.assertIn(f'data-v962-nav="{view}"', nav)

    # New blocker/regression coverage.
    def test_presentation_sync_source_is_idempotent_and_storage_safe(self):
        for token in (
            "node.textContent !== value",
            "schedulePresentationSync",
            "readThemePreference",
            "writeThemePreference",
            "const presentationObserver = new MutationObserver(schedulePresentationSync)",
            "Lazy fragments replace panel DOM asynchronously",
        ):
            self.assertIn(token, ENTERPRISE_SCRIPT)
        self.assertNotIn("if (title) title.textContent = item[0]", ENTERPRISE_SCRIPT)
        self.assertNotIn("if (sub) sub.textContent = item[1]", ENTERPRISE_SCRIPT)
        self.assertNotIn("setInterval(", ENTERPRISE_SCRIPT)

    def test_browser_load_and_presentation_sync_converge_without_mutation_storm(self):
        result = _browser_fixture("overview", 390, 844)
        self.assertTrue(result["origin"].startswith("http://127.0.0.1:"))
        self.assertTrue(result["themeStorageReadable"])
        self.assertTrue(result["documentLoadComplete"])
        self.assertTrue(result["eventLoopResponsive"])
        self.assertTrue(result["presentationConverged"])
        self.assertFalse(result["mutationStorm"])
        self.assertGreaterEqual(result["observerDelta"], 1)
        self.assertLessEqual(result["observerDelta"], 2)
        self.assertGreaterEqual(result["syncDelta"], 1)
        self.assertLessEqual(result["syncDelta"], 2)

    def test_v970_style_is_last_in_effective_cascade(self):
        v962 = _fake_v962()
        install_webgui_v970(v962)
        styles = v962._styles()
        self.assertLess(styles.rfind("legacy-last"), styles.rfind("webgui-v970-enterprise-operational-refresh"))
        self.assertTrue(styles.rstrip().endswith(ENTERPRISE_STYLE.rstrip()))
        result = _browser_fixture("overview", 390, 844)
        self.assertEqual(result["headingFont"], "17px")
        self.assertEqual(result["metricsGap"], "0px")
        self.assertEqual(result["metricsRadius"], "8px")
        self.assertEqual(result["metricPadding"], "9px")
        self.assertEqual(result["toolbarWrap"], "nowrap")
        self.assertLessEqual(result["horizontalOverflow"], 0)

    def test_mobile_inbox_reader_keeps_scroll_ancestor_visible(self):
        self.assertIn("#panel-inbox:has(.v963-detail)>.scroll{display:block", ENTERPRISE_STYLE)
        self.assertIn("tbody>tr:not(.v960-inline-detail){display:none}", ENTERPRISE_STYLE)
        for width, height in ((390, 844), (375, 812), (430, 932)):
            with self.subTest(viewport=(width, height)):
                result = _browser_fixture("inbox", width, height)
                self.assertEqual(result["readerScrollDisplay"], "block")
                for key in ("readerDetail", "readerSubject", "readerBody", "reply", "replyAll", "forward"):
                    self.assertGreater(result[key]["w"], 0, key)
                    self.assertGreater(result[key]["h"], 0, key)
                self.assertLessEqual(result["horizontalOverflow"], 0)

    def test_mobile_tasks_calendar_has_seven_columns_without_horizontal_scroll_design(self):
        self.assertNotIn("min-width:620px", ENTERPRISE_STYLE)
        self.assertIn(".task-calendar-shell{overflow:hidden", ENTERPRISE_STYLE)
        self.assertIn(".v970-task-day-agenda", ENTERPRISE_STYLE)
        self.assertIn("selectTaskDay", ENTERPRISE_SCRIPT)
        result = _browser_fixture("scheduler", 390, 844)
        self.assertEqual(result["calendarOverflow"], "hidden")
        self.assertLessEqual(result["calendarScroll"], 0)
        self.assertEqual(result["calendarColumns"], 7)
        self.assertEqual(result["selectedDate"], "2")
        self.assertIn("Task B", result["agendaText"])
        self.assertLessEqual(result["horizontalOverflow"], 0)

    def test_mobile_knowledge_drilldown_uses_existing_view_and_editor_contracts(self):
        self.assertIn("v970-knowledge-detail", ENTERPRISE_STYLE)
        self.assertIn("v970-knowledge-editor[open]", ENTERPRISE_STYLE)
        self.assertIn("view_knowledge", ENTERPRISE_SCRIPT)
        self.assertIn("edit_knowledge", ENTERPRISE_SCRIPT)
        self.assertIn("Back to list", ENTERPRISE_SCRIPT)
        self.assertIn("[['Kind',cells[1]],['Scopes',cells[2]],['Priority',cells[3]]]", ENTERPRISE_SCRIPT)
        result = _browser_fixture("knowledge", 390, 844)
        self.assertTrue(result["knowledgeDetailClass"])
        self.assertTrue(result["knowledgeBack"])
        self.assertTrue(result["knowledgeEditorClass"])
        self.assertEqual(result["knowledgeEditorFixed"], "fixed")
        self.assertTrue(result["knowledgeEditorBack"])
        self.assertLessEqual(result["horizontalOverflow"], 0)

    def test_more_sheet_has_dialog_focus_containment_and_restore_contract(self):
        nav = enterprise_nav()
        self.assertIn('role="dialog"', nav)
        self.assertIn('aria-modal="true"', nav)
        self.assertIn('aria-labelledby="v970-more-title"', nav)
        for token in ("moreReturnFocus", "setBackgroundInert", "trapMoreFocus", "inert", "Escape"):
            self.assertIn(token, ENTERPRISE_SCRIPT)
        result = _browser_fixture("overview", 390, 844)
        self.assertEqual(result["more"]["role"], "dialog")
        self.assertEqual(result["more"]["ariaModal"], "true")
        self.assertEqual(result["more"]["ariaHidden"], "false")
        self.assertTrue(result["more"]["workspaceInert"])
        self.assertTrue(result["more"]["initialFocus"])
        self.assertTrue(result["moreClosed"])
        self.assertTrue(result["returnFocus"])
        self.assertTrue(result["workspaceRestored"])
        self.assertLessEqual(result["horizontalOverflow"], 0)

    def test_browser_acceptance_breakpoints_and_navigation_surfaces(self):
        viewports = ((1440, 900), (1280, 800), (1024, 768), (768, 1024), (430, 932), (390, 844), (375, 812))
        representative_views = ("overview", "compose", "tracking", "accounts", "security", "projects", "files", "system", "coverage", "domains", "recipients")
        for width, height in viewports:
            for view in representative_views:
                with self.subTest(viewport=(width, height), view=view):
                    result = _browser_fixture(view, width, height)
                    self.assertEqual(result["viewport"], {"w": width, "h": height})
                    self.assertEqual(result["activeView"], view)
                    self.assertTrue(result["activeVisible"])
                    self.assertTrue(result["activePrimaryVisible"])
                    self.assertTrue(result["documentLoadComplete"])
                    self.assertTrue(result["eventLoopResponsive"])
                    self.assertTrue(result["presentationConverged"])
                    self.assertFalse(result["mutationStorm"])
                    self.assertLessEqual(result["horizontalOverflow"], 0)


if __name__ == "__main__":
    unittest.main()
