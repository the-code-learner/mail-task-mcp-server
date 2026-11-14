"""v9.8.6 real Inbox/Compose UX over the existing cache and SMTP pipelines.

No new mail send handler, IMAP fetch, security bypass or default file attachments.
A single editor maps its output back into the original body/body_html/body_amp
fields; stored file choices remain opt-in through attachment_file_ids.
"""
from __future__ import annotations

from html import escape
import re
from typing import Any
from starlette.requests import Request


_SOURCE_BEGIN = '<label class="wide">Plain text <textarea name="body" rows="8"></textarea></label>'
_SOURCE_HTML = '<label class="wide">HTML (optional) <textarea name="body_html" rows="6" placeholder="&lt;p&gt;HTML alternative&lt;/p&gt;"></textarea></label>'
_SOURCE_AMP = '<label class="wide">AMP (optional, account capability required) <textarea name="body_amp" rows="5" placeholder="AMP-for-Email document"></textarea></label>'

EDITOR = """<div class="wide v986-composer-editor" data-v986-editor>
<div class="v986-composer-editor-head">
<label>Message format <select data-v986-format aria-label="Message format">
<option value="plain">Plain text</option><option value="html">HTML</option>
<option value="amp">AMP for Email</option></select></label>
<div class="v986-composer-modes" role="group" aria-label="Editor mode">
<button type="button" data-v986-editor-mode="visual" aria-pressed="true" class="active">Visual</button>
<button type="button" data-v986-editor-mode="code" aria-pressed="false">Code</button></div></div>
<div class="v986-rich-toolbar" role="toolbar" aria-label="Text formatting">
<button type="button" data-v986-rich-command="bold" title="Bold"><b>B</b></button>
<button type="button" data-v986-rich-command="italic" title="Italic"><i>I</i></button>
<button type="button" data-v986-rich-command="underline" title="Underline"><u>U</u></button>
<button type="button" data-v986-rich-command="insertUnorderedList" title="Bullet list">• List</button>
<button type="button" data-v986-rich-command="createLink" title="Insert URL">Link</button>
</div>
<div class="v986-rich-surface" data-v986-rich contenteditable="true" role="textbox"
 aria-label="Visual email editor" aria-multiline="true" spellcheck="true"
 data-placeholder="Write your message…"></div>
<textarea data-v986-code class="v986-code-surface" rows="12" hidden
 aria-label="Email source code"></textarea>
<div class="v986-compose-original" hidden aria-hidden="true">
<textarea name="body"></textarea><textarea name="body_html"></textarea>
<textarea name="body_amp"></textarea></div>
<p class="v986-editor-hint">HTML and AMP are optional formats. Visual editing supports basic safe formatting.
Use Code for advanced HTML or AMP markup; AMP requires an enabled, capable sender account.</p>
</div>"""


def modern_compose_markup(html: str) -> str:
    """Replace only the redundant three body inputs; preserve every send field."""
    source = _SOURCE_BEGIN + "\n" + _SOURCE_HTML + "\n" + _SOURCE_AMP
    if source not in html:
        # The original composer might have different line breaks; do not risk
        # a partially transformed form if its schema has changed.
        pieces = (_SOURCE_BEGIN, _SOURCE_HTML, _SOURCE_AMP)
        if not all(part in html for part in pieces):
            return html
        start = html.index(_SOURCE_BEGIN)
        finish = html.index(_SOURCE_AMP, start) + len(_SOURCE_AMP)
        html = html[:start] + EDITOR + html[finish:]
    else:
        html = html.replace(source, EDITOR, 1)
    return html.replace(
        '<summary>Compose inside Inbox</summary>',
        '<summary>Compose email</summary>', 1
    )


_SELECT_PATTERN = re.compile(
    r'<label>(?P<label>Account|Folder|Mailbox)\s*'
    r'<select name="(?P<name>account_id|mailbox)">'
    r'(?P<options>.*?)</select></label>',
    re.S,
)
_SELECTED_PATTERN = re.compile(r'<option value="([^"]*)" selected(?:\s|>)')
_FILTER_PATTERN = re.compile(
    r'(<form method="get" action="/" class="v951-toolbar" '
    r'data-v960-fragment="inbox">.*?</form>)',
    re.S,
)
_SELECTED_MAILBOX = re.compile(
    r'<nav class="v960-mailbox-tabs".*?'
    r'<a[^>]*\bclass="active"[^>]*>([^<]+)</a>',
    re.S,
)


def modern_inbox_markup(html: str) -> str:
    if 'id="panel-inbox"' not in html:
        return html
    role = _SELECTED_MAILBOX.search(html)
    if role:
        heading = role.group(1)
        if heading in {"Inbox", "Sent", "Drafts", "Spam", "Trash"}:
            html = html.replace("<h2>Inbox</h2>", "<h2>" + heading + "</h2>", 1)

    def hidden_select(m: re.Match[str]) -> str:
        match = _SELECTED_PATTERN.search(m.group("options"))
        if not match:
            return m.group(0)  # Never lose a select if no valid default exists.
        return (
            '<input type="hidden" name="' + escape(m.group("name"),quote=True)
            + '" value="' + match.group(1) + '">'
        )
    # The header already has an account selector and the sidebar has mailbox
    # navigation. Keep their selected values in GET filters, without duplicates.
    def collapsible(m: re.Match[str]) -> str:
        form = _SELECT_PATTERN.sub(hidden_select, m.group(1))
        return (
            '<details class="v986-mail-filters">'
            '<summary>Advanced mailbox filters</summary>' + form + '</details>'
        )
    html = _FILTER_PATTERN.sub(collapsible, html, count=1)
    html = html.replace('>Aggiorna</button>', '>Refresh</button>')
    html = html.replace("Use Aggiorna for an explicit incremental sync.", "Use Refresh for an explicit incremental sync.")
    html = re.sub(
        r'(<div class="v963-refresh"><span class="v963-chip">)[^<]*(</span>)',
        r'\1Cached mailbox\2',html,count=1,
    )
    html = html.replace(
        '<div class="v963-inbox-head">',
        '<div class="v963-inbox-head v986-inbox-head">',1,
    )
    html = html.replace(
        '<table class="v960-mail-table">',
        '<table class="v960-mail-table v986-mail-list">',1,
    )
    return html


MAIL_STYLE = r"""
/* Inbox v5: one account selector globally, provider folders in the sidebar. */
#panel-inbox .v986-inbox-head{display:flex;justify-content:space-between;align-items:center;gap:12px;
 flex-wrap:wrap;border:0!important;padding:8px 0 15px!important}
#panel-inbox .v986-inbox-head h2{font-size:22px!important;letter-spacing:-.5px;margin:0}
#panel-inbox .v986-inbox-head p{font-size:11px!important;color:var(--v970-muted)}
#panel-inbox .v963-refresh{display:flex;align-items:center;gap:10px}
#panel-inbox .v963-refresh button{padding:8px 13px!important;border-radius:8px!important}
#panel-inbox .v960-mailbox-tabs{display:none!important}
#panel-inbox .v986-mail-filters{margin:10px 0 17px;padding:10px 13px;border:1px solid var(--v970-line);
 border-radius:9px;background:var(--v970-surface-2)}
#panel-inbox .v986-mail-filters summary{font-size:12px;font-weight:750;cursor:pointer}
#panel-inbox .v986-mail-filters form{padding:12px 0 2px!important;background:transparent!important;
 box-shadow:none!important;border:0!important;display:flex;flex-wrap:wrap;gap:10px}
#panel-inbox .v986-mail-filters input[type=hidden]{display:none}
#panel-inbox .v986-mail-filters form label{display:flex;flex-direction:column;gap:4px;min-width:140px}
#panel-inbox .v986-mail-filters label:has(input[type=checkbox]){flex-direction:row;align-items:center;min-width:auto}
#panel-inbox .scroll:has(.v986-mail-list){border:1px solid var(--v970-line);border-radius:10px;overflow-x:auto}
#panel-inbox table.v986-mail-list{display:block!important;width:100%;border:0!important;border-collapse:separate!important}
#panel-inbox .v986-mail-list thead{display:none!important}
#panel-inbox .v986-mail-list tbody{display:block!important}
#panel-inbox .v986-mail-list tr.v963-mail-row{display:grid!important;grid-template-columns:18px minmax(160px,1.15fr) minmax(180px,2fr) 155px;
 align-items:center;gap:10px;padding:0 14px!important;background:var(--v970-surface);min-height:57px;
 border-bottom:1px solid var(--v970-line)!important;cursor:pointer}
#panel-inbox .v986-mail-list tr.v963-mail-row:hover{background:var(--v970-accent-soft)!important}
#panel-inbox .v986-mail-list tr.v963-mail-row.unread{background:var(--v970-surface-2);font-weight:750}
#panel-inbox .v986-mail-list tr.v963-mail-row td{display:block!important;min-width:0;padding:11px 0!important;
 overflow:hidden;text-overflow:ellipsis;white-space:nowrap;border:0!important;font-size:12px}
#panel-inbox .v986-mail-list tr.v963-mail-row td:nth-child(2){font-weight:700}
#panel-inbox .v986-mail-list tr.v963-mail-row td:nth-child(3){color:var(--v970-text)}
#panel-inbox .v986-mail-list tr.v963-mail-row td:nth-child(4){font-size:11px;color:var(--v970-muted);text-align:right}
#panel-inbox .v986-mail-list tr.v960-inline-detail{display:block!important;padding:15px;border-bottom:1px solid var(--v970-line)}
#panel-inbox .v986-mail-list tr.v960-inline-detail td{display:block!important;width:100%;padding:0!important}
#panel-inbox .v986-mail-list tr:not(.v963-mail-row):not(.v960-inline-detail){display:block!important;padding:14px}
#panel-inbox .v986-mail-list tr:not(.v963-mail-row):not(.v960-inline-detail) td{display:block}
#panel-inbox .v960-pagination{display:flex;align-items:center;justify-content:space-between;gap:10px;
 padding:13px 4px;font-size:11px}
#panel-inbox .v963-proxy-card{margin-top:20px!important}
#panel-inbox details.v971-floating-compose{border:1px solid var(--v970-line)!important;border-radius:11px;
 margin:15px 0!important;box-shadow:none!important;background:var(--v970-surface)}
/* The sidebar Compose action is canonical; never show a second floating action. */
#panel-inbox details.v971-floating-compose:not([open]){display:none!important}
#panel-inbox details.v971-floating-compose[open]{position:relative!important;inset:auto!important;
 right:auto!important;bottom:auto!important;width:100%!important;max-height:none!important;overflow:visible!important;
 z-index:auto!important;box-shadow:none!important}
#panel-inbox details.v971-floating-compose>summary{font-size:14px;font-weight:800;padding:11px!important;
 border-radius:9px;background:var(--v970-surface-2)}
#panel-inbox .v971-floating-compose form{padding:10px 12px 15px}
#panel-inbox .v971-floating-compose .v951-formgrid{display:grid;
 grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}
#panel-inbox .v971-floating-compose .v951-formgrid .wide{grid-column:1/-1}
#panel-inbox .v971-floating-compose .v951-formgrid>label{font-size:11px;display:flex;
 flex-direction:column;gap:5px}
#panel-inbox .v971-floating-compose .v951-formgrid>label input,
#panel-inbox .v971-floating-compose .v951-formgrid>label select{
 min-height:36px!important;border:1px solid var(--v970-line)}
.v986-composer-editor{display:flex;flex-direction:column;gap:9px;grid-column:1/-1;
 border:1px solid var(--v970-line);border-radius:10px;background:var(--v970-surface);padding:12px}
.v986-composer-editor-head{display:flex;align-items:end;justify-content:space-between;gap:10px;flex-wrap:wrap}
.v986-composer-editor-head>label{display:flex;flex-direction:column;gap:4px;font-size:11px;font-weight:700}
.v986-composer-editor-head select{min-width:180px;padding:7px;border-radius:7px}
.v986-composer-modes{display:flex;gap:3px;border:1px solid var(--v970-line);border-radius:8px;padding:3px}
.v986-composer-modes button{padding:6px 12px;background:transparent;border:0!important;border-radius:6px!important;
 color:var(--v970-muted)}
.v986-composer-modes button.active{background:var(--v970-accent-soft)!important;color:var(--v970-accent)!important;font-weight:800}
.v986-rich-toolbar{display:flex;gap:5px;border-top:1px solid var(--v970-line);padding-top:9px}
.v986-rich-toolbar button{border:1px solid var(--v970-line);padding:5px 8px!important;min-width:34px;background:var(--v970-surface-2)}
.v986-rich-toolbar[hidden]{display:none!important}
.v986-rich-surface,.v986-code-surface{min-height:175px;width:100%;padding:12px;
 border:1px solid var(--v970-line);border-radius:9px;background:var(--v970-surface);
 color:var(--v970-text);font-size:13px;line-height:1.6;outline:0;overflow:auto}
.v986-rich-surface:focus,.v986-code-surface:focus{border-color:var(--v970-accent)}
.v986-rich-surface:empty:before{content:attr(data-placeholder);color:var(--v970-muted);pointer-events:none}
.v986-code-surface{font-family:ui-monospace,SFMono-Regular,monospace;font-size:12px}
.v986-code-surface[hidden],.v986-rich-surface[hidden]{display:none!important}
.v986-editor-hint{font-size:10px;color:var(--v970-muted);margin:1px 0}
.v986-composer-editor .v986-compose-original{display:none!important}
.v986-attachments{grid-column:1/-1;padding:9px 13px;border:1px solid var(--v970-line);
 border-radius:9px;background:var(--v970-surface-2)}
.v986-attachments details>summary{cursor:pointer;font-size:12px;font-weight:750}
.v986-file-choices{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:5px;margin:10px 0}
.v986-file-choices label{display:flex;align-items:center;gap:8px;font-size:11px}
.v986-file-choices label input{flex:0 0 auto}
.v986-attachment-info{color:var(--v970-muted);font-size:10px}
#panel-inbox .v971-floating-compose .v971-file-picker{display:none!important}
#panel-inbox .v971-floating-compose .v971-file-picker>option:checked{display:block}
@media(max-width:890px){
 #panel-inbox .v986-mail-list tr.v963-mail-row{grid-template-columns:14px minmax(110px,1fr) minmax(130px,1.5fr) 105px;gap:7px}
 #panel-inbox .v971-floating-compose .v951-formgrid{grid-template-columns:1fr}
}
@media(max-width:560px){
 #panel-inbox .v986-mail-list tr.v963-mail-row{grid-template-columns:12px minmax(85px,.9fr) minmax(120px,1.5fr);padding:0 8px!important}
 #panel-inbox .v986-mail-list tr.v963-mail-row td:nth-child(4){grid-column:3;text-align:left;padding-top:0!important;font-size:10px}
 #panel-inbox .v986-mail-list tr.v963-mail-row td:nth-child(3){grid-column:3;grid-row:1}
 #panel-inbox .v986-mail-list tr.v963-mail-row td:nth-child(2){grid-column:2;grid-row:1}
}
"""

MAIL_SCRIPT = r"""<script id="v986-mail-composer">
(() => {
 'use strict';
 const BAD = new Set(['SCRIPT','STYLE','LINK','META','BASE','IFRAME','OBJECT','EMBED','FORM','IMG','SVG','MATH','VIDEO','AUDIO','INPUT','BUTTON']);
 const GOOD = new Set(['P','DIV','BR','B','STRONG','I','EM','U','S','UL','OL','LI','A','BLOCKQUOTE','H1','H2','H3','SPAN']);
 const states = new WeakMap();
 const escapeText = txt => String(txt).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;');
 function safeHTML(source) {
   const doc = new DOMParser().parseFromString('<div>'+String(source || '')+'</div>','text/html');
   const wrapper = doc.body.firstElementChild;
   if(!wrapper)return '';
   function visit(node){
     for(const child of Array.from(node.children)) {
       if(BAD.has(child.tagName)){child.remove();continue;}
       if(!GOOD.has(child.tagName)){
         const text=doc.createTextNode(child.textContent||'');
         child.replaceWith(text);continue;
       }
       for(const attr of Array.from(child.attributes)){
         if(child.tagName==='A' && attr.name.toLowerCase()==='href') {
           const href=attr.value.trim();
           if(/^(https?:|mailto:)/i.test(href))continue;
         }
         child.removeAttribute(attr.name);
       }
       visit(child);
     }
   }
   visit(wrapper);
   return wrapper.innerHTML;
 }
 function getBody(text) {
   const doc=new DOMParser().parseFromString(String(text||''),'text/html');
   return doc.body?.innerHTML || '';
 }
 function plain(text) {
   const doc=new DOMParser().parseFromString('<div>'+String(text||'')+'</div>','text/html');
   return (doc.body?.textContent||'').trim();
 }
 function ampTemplate(content){
   return '<!doctype html>\n<html amp4email>\n<head>\n<meta charset="utf-8">\n'
     +'<script async src="https://cdn.ampproject.org/v0.js"><\/script>\n'
     +'<style amp4email-boilerplate>body{visibility:hidden}</style>\n'
     +'<\/head>\n<body>\n'+content+'\n<\/body>\n<\/html>';
 }
 function setupEditor(editor){
   if(states.has(editor))return;
   const form=editor.closest('form[data-v960-send]');
   if(!form)return;
   const visual=editor.querySelector('[data-v986-rich]');
   const code=editor.querySelector('[data-v986-code]');
   const picker=editor.querySelector('[data-v986-format]');
   const body=form.querySelector('textarea[name="body"]');
   const html=form.querySelector('textarea[name="body_html"]');
   const amp=form.querySelector('textarea[name="body_amp"]');
   if(!visual||!code||!picker||!body||!html||!amp)return;
   const drafts={plain:body.value||'',html:html.value||'',amp:amp.value||''};
   let format='plain',mode='visual',visualDirty=false;
   const read=()=>mode==='code'?code.value:
     format==='plain'?visual.innerText:visual.innerHTML;
   function save(){
     if(mode==='code'){
       // Code is the authoritative source until the user actually edits Visual.
       drafts[format]=code.value;
     }else if(visualDirty){
       const value=read();
       drafts[format]=format==='plain'?value:
         format==='html'?safeHTML(value):ampTemplate(safeHTML(value));
     }
     const value=drafts[format];
     body.value=format==='plain'?value:
       plain(format==='amp'?getBody(value):value);
     html.value=format==='html'?value:'';
     amp.value=format==='amp'?value:'';
   }
   function show(){
     const value=drafts[format] || (format==='plain'?'':format==='html'?safeHTML(escapeText(drafts.plain).replaceAll('\n','<br>')):
       ampTemplate(safeHTML(escapeText(drafts.plain).replaceAll('\n','<br>'))));
     drafts[format]=value;
     if(mode==='visual'){
       visual.innerHTML=format==='plain'?safeHTML(escapeText(value).replaceAll('\n','<br>')):
         safeHTML(format==='amp'?getBody(value):value);
     }else{
       code.value=value;
     }
     visualDirty=false;
     visual.hidden=mode!=='visual';
     code.hidden=mode!=='code';
     const tools=editor.querySelector('.v986-rich-toolbar');
     if(tools)tools.hidden=mode!=='visual'||format==='plain';
     editor.querySelectorAll('[data-v986-editor-mode]').forEach(btn=>{
       const active=btn.dataset.v986EditorMode===mode;
       btn.classList.toggle('active',active);
       btn.setAttribute('aria-pressed',String(active));
     });
   }
   picker.addEventListener('change',()=>{
     save();format=picker.value;show();save();
   });
   editor.querySelectorAll('[data-v986-editor-mode]').forEach(btn=>btn.addEventListener('click',()=>{
     if(btn.dataset.v986EditorMode===mode)return;
     save();mode=btn.dataset.v986EditorMode;show();save();
   }));
   visual.addEventListener('paste',ev=>{
     ev.preventDefault();
     const txt=ev.clipboardData?.getData('text/plain')||'';
     document.execCommand('insertText',false,txt);
   });
   visual.addEventListener('input',()=>{visualDirty=true;save();});
   code.addEventListener('input',save);
   editor.querySelectorAll('[data-v986-rich-command]').forEach(btn=>{
     btn.addEventListener('click',()=>{
       visual.focus();
       const cmd=btn.dataset.v986RichCommand;
       if(cmd==='createLink'){
         const val=window.prompt('Link URL (https:// or mailto:)','https://');
         if(!val||!(/^(https?:|mailto:)/i.test(val)))return;
         document.execCommand(cmd,false,val);
       }else document.execCommand(cmd,false,null);
       visualDirty=true;
       save();
     });
   });
   states.set(editor,{save});
   show();
   save();
 }
 function setupAttachments(form){
   const select=form.querySelector('select.v971-file-picker[name="attachment_file_ids"]');
   if(!select || select.dataset.v986Enhanced)return;
   select.dataset.v986Enhanced='1';
   for(const option of select.options) option.selected=false; // Explicit opt-in.
   const oldLabel=select.closest('label');
   const wrap=document.createElement('div');
   wrap.className='v986-attachments';
   const details=document.createElement('details');
   const summary=document.createElement('summary');
   const choices=document.createElement('div');
   choices.className='v986-file-choices';
   const info=document.createElement('p');
   info.className='v986-attachment-info';
   info.textContent='Files in storage are never attached unless explicitly selected here.';
   const refresh=()=>{
     const n=Array.from(select.selectedOptions).length;
     summary.textContent='Attachments · '+n+' selected';
   };
   for(const option of select.options) {
     const label=document.createElement('label');
     const checkbox=document.createElement('input');
     checkbox.type='checkbox';
     checkbox.value=option.value;
     checkbox.checked=false;
     checkbox.addEventListener('change',()=>{option.selected=checkbox.checked;refresh();});
     label.append(checkbox,document.createTextNode(option.textContent||option.value));
     choices.append(label);
   }
   if(!select.options.length)choices.textContent='No stored files are available.';
   details.append(summary,choices,info);
   wrap.append(details);
   oldLabel?.after(wrap);
   if(oldLabel)oldLabel.hidden=true;
   refresh();
 }
 function init(){
   const inbox=document.querySelector('#panel-inbox.active');
   if(!inbox)return;
   const composer=inbox.querySelector('details.v971-floating-compose');
   if(composer){
     const heading=inbox.querySelector('.v986-inbox-head');
     if(heading && composer.previousElementSibling!==heading)heading.after(composer);
     composer.querySelectorAll('[data-v986-editor]').forEach(setupEditor);
     const form=composer.querySelector('form[data-v960-send]');
     if(form)setupAttachments(form);
   }
 }
 document.addEventListener('submit',ev=>{
   const editor=ev.target.querySelector?.('[data-v986-editor]');
   if(editor)states.get(editor)?.save();
 },true);
 const main=document.querySelector('.v970-workspace > main');
 if(main)new MutationObserver(init).observe(main,{childList:true,subtree:false});
 document.addEventListener('click',ev=>{
   if(ev.target.closest('[data-v985-compose]'))queueMicrotask(init);
 });
 init();
})();
</script>"""


def install_mail_gui_v986(v960: Any, shell: Any) -> None:
    if getattr(v960, "_v986_mail_installed", False):
        return
    original_compose = v960._compose_panel
    original_inbox = v960.render_inbox
    def compose(*args: Any, **kwargs: Any) -> str:
        return modern_compose_markup(original_compose(*args,**kwargs))
    def inbox(base: Any, request: Request) -> str:
        return modern_inbox_markup(original_inbox(base,request))
    v960._compose_panel = compose
    v960.render_inbox = inbox
    setattr(v960,"_v986_mail_installed",True)
    prev=shell._styles
    if not getattr(prev,"_v986_mail_style",False):
        def styles() -> str:
            return prev().rstrip()+"\n"+MAIL_STYLE
        styles._v986_mail_style=True
        shell._styles=styles
    if 'id="v986-mail-composer"' not in shell.SCRIPT:
        shell.SCRIPT+=MAIL_SCRIPT
