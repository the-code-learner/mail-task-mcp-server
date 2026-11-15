"""Compose HTML and AMP rich-editor contract tests (v9.8.10)."""
from __future__ import annotations

import unittest

from postmaster.webgui_mail_v986 import (
    EDITOR, MAIL_SCRIPT, MAIL_STYLE, modern_compose_markup,
)


SOURCE = (
    '<form method="post" action="/dashboard/compose/send" data-v960-send="1">'
    '<input name="csrf" value="csrf-secret">'
    '<input name="idempotency_key" value="sample-key">'
    '<label class="wide">Plain text <textarea name="body" rows="8"></textarea></label>'
    '\n'
    '<label class="wide">HTML (optional) <textarea name="body_html" rows="6" '
    'placeholder="&lt;p&gt;HTML alternative&lt;/p&gt;"></textarea></label>'
    '\n'
    '<label class="wide">AMP (optional, account capability required) '
    '<textarea name="body_amp" rows="5" '
    'placeholder="AMP-for-Email document"></textarea></label>'
    '<input name="attachment_file_ids">'
    '<button name="compose_action" value="send">Send</button></form>'
)


class ComposeRichEditorContracts(unittest.TestCase):
    def test_email_safe_font_options_and_sizes(self):
        for face in ("Arial", "Verdana", "Georgia", "Tahoma",
                     "Trebuchet MS", "Times New Roman", "Courier New"):
            self.assertIn(f'<option value="{face}">{face}</option>', EDITOR)
        for size in ("10", "12", "14", "16", "18", "24", "32"):
            self.assertIn(f'<option value="{size}">{size} px</option>', EDITOR)
        self.assertEqual(EDITOR.count('data-v986-rich-font '), 1)
        self.assertEqual(EDITOR.count('data-v986-rich-size '), 1)
        self.assertIn("FONT_FAMILIES", MAIL_SCRIPT)
        self.assertIn("FONT_SIZES", MAIL_SCRIPT)
        self.assertIn("sans-serif", MAIL_SCRIPT)
        self.assertIn("serif", MAIL_SCRIPT)
        self.assertIn("monospace", MAIL_SCRIPT)

    def test_editor_adds_html_formatting_actions_without_changing_submit_contract(self):
        rendered = modern_compose_markup(SOURCE)
        for field in ("body", "body_html", "body_amp", "attachment_file_ids",
                      "idempotency_key", "csrf", "compose_action"):
            self.assertEqual(rendered.count(f'name="{field}"'), 1)
        self.assertIn('action="/dashboard/compose/send"', rendered)
        for command in ("bold", "italic", "underline", "strikeThrough",
                        "justifyLeft", "justifyCenter", "justifyRight",
                        "insertOrderedList", "insertUnorderedList",
                        "removeFormat", "undo", "redo", "createLink"):
            self.assertIn(f'data-v986-rich-command="{command}"', rendered)
        for item in ("table", "image"):
            self.assertIn(f'data-v986-rich-insert="{item}"', rendered)
        for item in ("foreColor", "hiliteColor"):
            self.assertIn(f'data-v986-rich-color="{item}"', rendered)
        self.assertIn('data-v986-rich-block', rendered)
        self.assertIn('data-v986-editor-mode="visual"', rendered)
        self.assertIn('data-v986-editor-mode="code"', rendered)

    def test_inline_styles_are_strictly_allowlisted(self):
        self.assertIn("function safeStyle(raw)", MAIL_SCRIPT)
        self.assertIn("const css=safeStyle(child.getAttribute('style')||'')", MAIL_SCRIPT)
        self.assertIn("if(css)child.setAttribute('style',css)", MAIL_SCRIPT)
        self.assertIn("if(key==='font-family')", MAIL_SCRIPT)
        self.assertIn("key==='font-size'", MAIL_SCRIPT)
        self.assertIn("key==='text-align'", MAIL_SCRIPT)
        self.assertIn("key==='background-color'", MAIL_SCRIPT)
        self.assertNotIn("new Function(", MAIL_SCRIPT)
        self.assertNotIn("eval(", MAIL_SCRIPT)
        self.assertIn("imagePicker?.addEventListener", MAIL_SCRIPT)
        self.assertIn("'FORM','IMG','SVG'", MAIL_SCRIPT)
        self.assertIn("Stored in Postmaster.", MAIL_SCRIPT)
        self.assertIn("if(!GOOD.has(child.tagName))", MAIL_SCRIPT)

    def test_amp_components_are_code_only_and_source_is_preserved(self):
        self.assertIn('data-v986-amp-toolbar', EDITOR)
        for item in ("amp-img", "amp-carousel", "amp-accordion",
                     "amp-selector", "amp-form"):
            self.assertIn(f'data-v986-amp-insert="{item}"', EDITOR)
            self.assertIn(f"'{item}':", MAIL_SCRIPT)
        self.assertIn('data-v986-amp-check', EDITOR)
        self.assertIn("if(format==='amp')mode='code'", MAIL_SCRIPT)
        self.assertIn("lockedAMP=format==='amp'&&ampComplex(value)", MAIL_SCRIPT)
        self.assertIn("visual.contentEditable=(lockedAMP||lockedHTML)?'false':'true'", MAIL_SCRIPT)
        self.assertIn("if(mode==='code'){", MAIL_SCRIPT)
        self.assertIn("drafts[format]=code.value", MAIL_SCRIPT)
        self.assertIn("format==='amp'?", MAIL_SCRIPT)  # HTML fallback generation
        self.assertIn("missing AMP runtime script", MAIL_SCRIPT)
        self.assertIn("NOT a full AMP validator", MAIL_SCRIPT)
        self.assertIn("https://cdn.ampproject.org/v0/", MAIL_SCRIPT)

    def test_visual_toolbar_responsive_and_hidden_in_code_mode(self):
        self.assertIn(".v986-rich-toolbar[hidden],.v986-amp-toolbar[hidden]", MAIL_STYLE)
        self.assertIn(".v986-rich-toolbar select", MAIL_STYLE)
        self.assertIn("flex-wrap:wrap", MAIL_STYLE)
        self.assertIn("if(tools)tools.hidden=mode!=='visual'", MAIL_SCRIPT)
        self.assertIn("if(ampTools)ampTools.hidden=mode!=='code'||format!=='amp'", MAIL_SCRIPT)
        self.assertIn("role=\"status\"", EDITOR)
        self.assertIn("aria-live=\"polite\"", EDITOR)

    def test_browser_preserves_selected_fonts_and_amp_code(self):
        from test_webgui_v970 import _browser_session, _serve_browser_document
        import time

        page = (
            '<!doctype html><html><head><meta charset="utf-8"></head><body>'
            '<div class="v970-workspace"><main><section id="panel-inbox" class="tab-panel active">'
            '<details class="v971-floating-compose" open><summary>Compose</summary>'
            '<form method="post" data-v960-send="1"><input name="csrf" value="ok">'
            + EDITOR +
            '</form></details></section></main></div>' + MAIL_SCRIPT + '</body></html>'
        )
        browser = _browser_session()
        with _serve_browser_document(page) as url:
            browser._call("Page.navigate", {"url": url})
            script = r"""(() => {
              const ed=document.querySelector('[data-v986-editor]');
              if(!ed || !ed.querySelector('[data-v986-format]'))return null;
              const fmt=ed.querySelector('[data-v986-format]');
              fmt.value='html';fmt.dispatchEvent(new Event('change'));
              const rich=ed.querySelector('[data-v986-rich]');
              rich.innerHTML='<p>Hello font</p>';
              rich.dispatchEvent(new Event('input'));
              const textNode=rich.querySelector('p').firstChild;
              const range=document.createRange();
              range.selectNodeContents(textNode);
              const sel=getSelection();sel.removeAllRanges();sel.addRange(range);
              const font=ed.querySelector('[data-v986-rich-font]');
              font.value='Georgia';font.dispatchEvent(new Event('change'));
              const styled=ed.querySelector('textarea[name="body_html"]').value;
              const codeButton=ed.querySelector('[data-v986-editor-mode="code"]');
              codeButton.click();
              const code=ed.querySelector('[data-v986-code]').value;
              ed.querySelector('[data-v986-editor-mode="visual"]').click();
              codeButton.click();
              const codeAfter=ed.querySelector('[data-v986-code]').value;
              fmt.value='amp';fmt.dispatchEvent(new Event('change'));
              ed.querySelector('[data-v986-amp-insert="amp-accordion"]').click();
              const ampBefore=ed.querySelector('[data-v986-code]').value;
              ed.querySelector('[data-v986-editor-mode="visual"]').click();
              const readOnly=ed.querySelector('[data-v986-rich]').contentEditable;
              codeButton.click();
              const ampAfter=ed.querySelector('[data-v986-code]').value;
              return {styled,code,codeAfter,ampBefore,ampAfter,readOnly};
            })()"""
            data = None
            for _ in range(100):
                result = browser._call("Runtime.evaluate", {
                    "expression": script, "returnByValue": True,
                })
                data = result.get("result", {}).get("result", {}).get("value")
                if data is not None:
                    break
                time.sleep(.04)
            self.assertIsNotNone(data)
            self.assertIn("Georgia, serif", data["styled"])
            self.assertIn("Hello font", data["code"])
            self.assertEqual(data["code"], data["codeAfter"])
            self.assertIn("<amp-accordion>", data["ampBefore"])
            self.assertIn('custom-element="amp-accordion"', data["ampBefore"])
            self.assertEqual(data["ampBefore"], data["ampAfter"])
            self.assertEqual(data["readOnly"], "false")

    def test_no_automatic_file_attachment_selection_or_remote_validation(self):
        self.assertIn("for(const option of select.options) option.selected=false", MAIL_SCRIPT)
        self.assertNotIn("window.fetch(", MAIL_SCRIPT)
        self.assertNotIn("XMLHttpRequest", MAIL_SCRIPT)


if __name__ == "__main__":
    unittest.main()
