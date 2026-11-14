from __future__ import annotations

import time
import unittest

from postmaster.webgui_mail_v986 import EDITOR,MAIL_STYLE,MAIL_SCRIPT
from postmaster.webgui_settings_v985 import SETTINGS_STYLE,SETTINGS_SCRIPT
from test_webgui_v970 import _browser_session,_serve_browser_document


def run(browser,expression):
    value=None
    for _ in range(90):
        payload=browser._call("Runtime.evaluate",{
            "expression":expression,"returnByValue":True,
        })
        value=payload.get("result",{}).get("result",{}).get("value")
        if value is not None:return value
        time.sleep(.04)
    raise AssertionError("Chrome evaluation never resolved")


class BrowserInteractionV986(unittest.TestCase):
    def test_editor_format_toggle_code_and_explicit_attachments(self):
        html=(
            '<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<style>'+MAIL_STYLE+'</style></head><body>'
            '<div class="v970-workspace"><main>'
            '<section id="panel-inbox" class="tab-panel active" data-panel="inbox">'
            '<div class="v986-inbox-head"><h2>Inbox</h2></div>'
            '<details class="v971-floating-compose" open>'
            '<summary>Compose</summary>'
            '<form method="post" action="/dashboard/compose/send" data-v960-send="1">'
            +EDITOR+
            '<label>Attachments<select class="v971-file-picker" name="attachment_file_ids" multiple>'
            '<option value="stored-1">Budget.pdf</option>'
            '<option value="stored-2">Minutes.txt</option>'
            '</select></label>'
            '<button type="submit">Send</button></form></details>'
            '</section></main></div>'+MAIL_SCRIPT+'</body></html>'
        )
        browser=_browser_session()
        with _serve_browser_document(html) as url:
            browser._call("Page.navigate",{"url":url})
            expression=r"""(() => {
              const editor=document.querySelector('[data-v986-editor]');
              if(!editor || !editor.querySelector('[data-v986-format]'))return null;
              const form=editor.closest('form');
              const attachments=form.querySelector('select.v971-file-picker');
              const start={
                selectedFiles:attachments.selectedOptions.length,
                count:form.querySelector('.v986-attachments summary')?.textContent||'',
                format:editor.querySelector('[data-v986-format]').value
              };
              const fmt=editor.querySelector('[data-v986-format]');
              fmt.value='html';
              fmt.dispatchEvent(new Event('change',{bubbles:true}));
              const rich=editor.querySelector('[data-v986-rich]');
              rich.innerHTML='<p><b>Hello</b><img src="https://invalid.test/pixel"></p>';
              rich.dispatchEvent(new Event('input',{bubbles:true}));
              const safe=form.querySelector('textarea[name="body_html"]').value;
              const alternate=form.querySelector('textarea[name="body"]').value;
              const codeButton=editor.querySelector('[data-v986-editor-mode="code"]');
              codeButton.click();
              const raw=editor.querySelector('[data-v986-code]');
              const coded=raw.value;
              raw.value='<p><em>Edited in code</em></p>';
              raw.dispatchEvent(new Event('input',{bubbles:true}));
              const afterCode=form.querySelector('textarea[name="body_html"]').value;
              const checked=form.querySelector('.v986-file-choices input[type="checkbox"]');
              checked.checked=true;
              checked.dispatchEvent(new Event('change',{bubbles:true}));
              return {
                start,afterSelected:attachments.selectedOptions.length,
                chosen:attachments.selectedOptions[0]?.value,
                count:form.querySelector('.v986-attachments summary')?.textContent||'',
                safe,alternate,coded,afterCode,
                codeVisible:!raw.hidden,
                visualHidden:rich.hidden,
                editorFormat:fmt.value
              };
            })()"""
            data=run(browser,expression)
            self.assertEqual(data["start"]["selectedFiles"],0)
            self.assertIn("0 selected",data["start"]["count"])
            self.assertEqual(data["afterSelected"],1)
            self.assertEqual(data["chosen"],"stored-1")
            self.assertIn("1 selected",data["count"])
            self.assertIn("Hello",data["safe"])
            self.assertNotIn("<img",data["safe"].lower())
            self.assertIn("Hello",data["alternate"])
            self.assertIn("Hello",data["coded"])
            self.assertIn("Edited in code",data["afterCode"])
            self.assertEqual(data["editorFormat"],"html")
            self.assertTrue(data["codeVisible"])
            self.assertTrue(data["visualHidden"])

    def test_settings_tabs_toggle_content_without_navigation_to_inbox(self):
        html=(
            '<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<style>'+SETTINGS_STYLE+'</style></head><body>'
            '<div class="v985-settings" data-v986-settings>'
            '<div class="v985-settings-tabs">'
            '<button type="button" data-v986-settings-tab="versions">Versions</button>'
            '<button type="button" data-v986-settings-tab="accounts">Email accounts</button>'
            '<button type="button" data-v986-settings-tab="services">Manage services</button>'
            '<button type="button" data-v986-settings-tab="diagnostics">Diagnostics</button>'
            '</div>'
            '<div data-v986-settings-pane="versions">Release selector</div>'
            '<div data-v986-settings-pane="accounts" hidden>Account editor</div>'
            '<div data-v986-settings-pane="services" hidden>Service management</div>'
            '<div data-v986-settings-pane="diagnostics" hidden>Diagnostics detail</div>'
            '</div>'+SETTINGS_SCRIPT+'</body></html>'
        )
        browser=_browser_session()
        with _serve_browser_document(html) as url:
            browser._call("Page.navigate",{"url":url})
            result=run(browser,r"""(() => {
              if(!document.querySelector('button[data-v986-settings-tab="services"]'))return null;
              const click=key=>document.querySelector('button[data-v986-settings-tab="'+key+'"]').click();
              click('services');
              const services = {
                visible:!document.querySelector('[data-v986-settings-pane="services"]').hidden,
                accountHidden:document.querySelector('[data-v986-settings-pane="accounts"]').hidden,
                selected:document.querySelector('[data-v986-settings-tab="services"]').getAttribute('aria-selected')
              };
              click('accounts');
              const accounts={
                visible:!document.querySelector('[data-v986-settings-pane="accounts"]').hidden,
                url:location.search
              };
              click('diagnostics');
              return {
                services,accounts,
                diagnostics:!document.querySelector('[data-v986-settings-pane="diagnostics"]').hidden,
                hash:location.hash
              };
            })()""")
            self.assertTrue(result["services"]["visible"])
            self.assertTrue(result["services"]["accountHidden"])
            self.assertEqual(result["services"]["selected"],"true")
            self.assertTrue(result["accounts"]["visible"])
            self.assertIn("settings_tab=accounts",result["accounts"]["url"])
            self.assertTrue(result["diagnostics"])
            self.assertEqual(result["hash"],"#system")


if __name__=="__main__":
    unittest.main()
