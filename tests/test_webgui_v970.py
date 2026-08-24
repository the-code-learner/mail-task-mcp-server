from __future__ import annotations

import html as html_module
import inspect
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
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


def _browser_fixture(view: str, width: int, height: int) -> dict[str, object]:
    browser = _browser_path()
    if not browser:
        raise AssertionError("Canonical browser acceptance requires Chrome/Chromium")

    legacy = r'''
.tab-panel{display:none}.tab-panel.active{display:block}
.v951-pagehead h2{font-size:24px}.v951-metrics{display:grid;gap:10px;border-radius:11px}.v951-metric{padding:13px}
.v951-toolbar{display:flex;flex-wrap:wrap}.v963-detail-actions{display:flex;gap:8px}.v963-detail-actions a{display:inline-flex}
'''
    mapping = {key: [title, subtitle] for key, (title, subtitle) in VIEW_LABELS.items()}
    script = ENTERPRISE_SCRIPT.replace(
        "%VIEW_LABELS%", json.dumps(mapping, ensure_ascii=False, separators=(",", ":"))
    )
    nav = enterprise_nav()
    knowledge_open = " open" if view == "knowledge" else ""
    panels = f'''
<section class="tab-panel {'active' if view == 'overview' else ''}" id="panel-overview" data-panel="overview">
  <div class="v951-pagehead"><div><h2 id="computed-heading">Operations Dashboard</h2></div></div>
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
</section>
<section class="tab-panel {'active' if view == 'knowledge' else ''}" id="panel-knowledge" data-panel="knowledge"><div class="grid">
  <section class="card wide"><div class="panel-title"><div><h2>Memory One</h2></div></div><div class="markdown-viewer">Knowledge detail</div></section>
  <section class="card wide"><h2>Knowledge / Skills</h2></section>
  <details class="v962-collapsible" data-v962-state-key="knowledge-editor"{knowledge_open}><summary>Add memory / skill</summary><div class="v962-collapsible-body"><section class="card wide"><form><div class="v951-formgrid"><label>Priority<input value="0.70"></label><label>Title<input value="Memory One"></label><label class="wide">Content<textarea>Content</textarea></label><label><input type="checkbox" checked> Always include</label><label><input type="checkbox" checked> Enabled</label></div></form></section></div></details>
  <section class="card wide"><h2>Search</h2></section>
  <section class="card wide"><div class="scroll"><table><thead><tr><th>Item</th><th>Kind</th><th>Scopes</th><th>Priority</th><th></th></tr></thead><tbody><tr><td><strong>Memory One</strong><div class="small muted mono">k1</div></td><td><span class="badge">memory</span></td><td>Project Alpha</td><td>0.70</td><td class="actions"><button>View</button><button>Edit</button></td></tr></tbody></table></div></section>
</div></section>
'''
    measurement = r'''
<script>
(() => {
  if (document.body.dataset.v970View === 'scheduler') {
    const days=document.querySelectorAll('#calendar-grid .task-calendar-day'); if(days[1]) days[1].click();
  }
  const rect = selector => { const el=document.querySelector(selector); if(!el)return {w:0,h:0}; const r=el.getBoundingClientRect(); return {w:r.width,h:r.height}; };
  const metrics=document.querySelector('#computed-metrics'), metric=document.querySelector('#computed-metric'), toolbar=document.querySelector('#computed-toolbar');
  const shell=document.querySelector('#calendar-shell'), grid=document.querySelector('#calendar-grid');
  const trigger=document.querySelector('[data-v970-more]'); trigger?.focus(); trigger?.click();
  const sheet=document.querySelector('#v970-more-sheet'), first=sheet?.querySelector('[data-v970-more-close]');
  const focusables=[...(sheet?.querySelectorAll('a[href],button:not([disabled])') || [])]; const last=focusables[focusables.length-1];
  if(last){last.focus();last.dispatchEvent(new KeyboardEvent('keydown',{key:'Tab',bubbles:true,cancelable:true}));}
  const trapped=document.activeElement===first;
  const openState={role:sheet?.getAttribute('role'),ariaModal:sheet?.getAttribute('aria-modal'),ariaHidden:sheet?.getAttribute('aria-hidden'),workspaceInert:document.querySelector('.v970-workspace')?.hasAttribute('inert'),initialFocus:trapped};
  document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true,cancelable:true}));
  const result={
    headingFont:getComputedStyle(document.querySelector('#computed-heading')).fontSize,
    metricsGap:metrics ? getComputedStyle(metrics).gap : '', metricsRadius:metrics ? getComputedStyle(metrics).borderRadius : '', metricPadding:metric ? getComputedStyle(metric).paddingTop : '', toolbarWrap:toolbar ? getComputedStyle(toolbar).flexWrap : '',
    readerScrollDisplay:getComputedStyle(document.querySelector('#reader-scroll')).display, readerDetail:rect('#reader-detail'), readerSubject:rect('#reader-subject'), readerBody:rect('#reader-body'), reply:rect('#reader-reply'), replyAll:rect('#reader-reply-all'), forward:rect('#reader-forward'), horizontalOverflow:document.documentElement.scrollWidth-document.documentElement.clientWidth,
    calendarOverflow:shell ? getComputedStyle(shell).overflowX : '', calendarScroll:shell ? shell.scrollWidth-shell.clientWidth : -1, calendarColumns:grid ? getComputedStyle(grid).gridTemplateColumns.trim().split(/\s+/).length : 0, selectedDate:document.querySelector('.task-calendar-day.v970-selected .task-calendar-date')?.textContent?.trim() || '', agendaText:document.querySelector('.v970-task-day-agenda')?.textContent?.replace(/\s+/g,' ').trim() || '',
    knowledgeDetailClass:document.querySelector('#panel-knowledge .v970-knowledge-detail') !== null, knowledgeBack:document.querySelector('#panel-knowledge .v970-knowledge-detail > .v970-overlay-back') !== null, knowledgeMeta:document.querySelector('.v970-knowledge-meta')?.textContent?.replace(/\s+/g,' ').trim() || '', knowledgeEditorClass:document.querySelector('#panel-knowledge .v970-knowledge-editor') !== null, knowledgeEditorFixed:document.querySelector('#panel-knowledge .v970-knowledge-editor') ? getComputedStyle(document.querySelector('#panel-knowledge .v970-knowledge-editor')).position : '', knowledgeEditorBack:document.querySelector('#panel-knowledge .v970-knowledge-editor .v970-overlay-back') !== null,
    more:openState, moreClosed:sheet ? !sheet.classList.contains('v970-open') : false, returnFocus:document.activeElement===trigger, workspaceRestored:!document.querySelector('.v970-workspace')?.hasAttribute('inert')
  };
  document.querySelector('#v970-browser-result').textContent=JSON.stringify(result);
})();
</script>
'''
    document = f'''<!doctype html><html><head><meta charset="utf-8"><style>{legacy}\n{ENTERPRISE_STYLE}</style></head><body><div class="shell">{nav}<div class="v970-workspace"><header class="v970-contextbar"><div class="v970-context-title"><strong data-v970-context-title>Dashboard</strong><span data-v970-context-subtitle>Test</span></div></header><main>{panels}</main></div></div>{script}<pre id="v970-browser-result"></pre>{measurement}</body></html>'''

    with tempfile.TemporaryDirectory(prefix="postmaster-v970-browser-", ignore_cleanup_errors=True) as tmp:
        root = Path(tmp)
        page = root / "fixture.html"
        page.write_text(document, encoding="utf-8")
        url = page.as_uri()
        if view == "knowledge":
            url += "?ui_view=knowledge&view_knowledge=k1#knowledge"
        else:
            url += f"?ui_view={view}#{view}"
        completed = subprocess.run(
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
                f"--window-size={width},{height}",
                "--dump-dom",
                url,
            ],
            text=True,
            capture_output=True,
            timeout=12,
        )
        if completed.returncode != 0:
            raise AssertionError(f"Chrome acceptance failed: {completed.stderr[-1200:]}")
        match = re.search(r'<pre id="v970-browser-result">(.*?)</pre>', completed.stdout, re.S)
        if not match or not match.group(1).strip():
            raise AssertionError(f"Browser fixture did not emit results: {completed.stdout[-1800:]}")
        return json.loads(html_module.unescape(match.group(1)))


class WebGuiV970Tests(unittest.TestCase):
    # Existing seven UI-specific tests remain in place.
    def test_installer_has_no_app_or_backend_registry_parameter(self):
        self.assertEqual(list(inspect.signature(install_webgui_v970).parameters), ["v962"])

    def test_navigation_preserves_every_existing_lazy_view(self):
        covered = {
            view
            for _heading, links in NAV_GROUPS
            for view, _label, _code in links
        }
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

    def test_mobile_knowledge_drilldown_uses_existing_view_and_editor_contracts(self):
        self.assertIn("v970-knowledge-detail", ENTERPRISE_STYLE)
        self.assertIn("v970-knowledge-editor[open]", ENTERPRISE_STYLE)
        self.assertIn("view_knowledge", ENTERPRISE_SCRIPT)
        self.assertIn("edit_knowledge", ENTERPRISE_SCRIPT)
        self.assertIn("Back to list", ENTERPRISE_SCRIPT)
        result = _browser_fixture("knowledge", 390, 844)
        self.assertTrue(result["knowledgeDetailClass"])
        self.assertTrue(result["knowledgeBack"])
        self.assertIn("Kind", result["knowledgeMeta"])
        self.assertIn("memory", result["knowledgeMeta"])
        self.assertIn("Project Alpha", result["knowledgeMeta"])
        self.assertTrue(result["knowledgeEditorClass"])
        self.assertEqual(result["knowledgeEditorFixed"], "fixed")
        self.assertTrue(result["knowledgeEditorBack"])

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


if __name__ == "__main__":
    unittest.main()
