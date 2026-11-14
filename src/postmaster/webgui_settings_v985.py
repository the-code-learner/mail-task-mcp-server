"""v9.8.5 Settings: real version management first, operational diagnostics on demand."""
from __future__ import annotations

from html import escape
import re
from typing import Any

from starlette.requests import Request

from . import webgui_v951 as v951
from .webgui_versions_v984 import versions_settings
from .webgui_v962_views import _panel


def render_settings_v985(base: Any, request: Request) -> str:
    version=versions_settings(base)
    actions=(
        ("Privacy and security","security","Inspect privacy proxy, authentication and mail policy"),
        ("MCP Catalog","coverage","Inspect the available MCP capabilities"),
        ("Contacts and sending permissions","contacts","Authorized recipients and domains"),
    )
    shortcuts="".join(
        '<a class="v985-settings-shortcut" href="#'+escape(view)+'" data-v962-nav="'+escape(view)+'">'
        '<strong>'+escape(title)+'</strong><span>'+escape(note)+'</span><b>→</b></a>'
        for title,view,note in actions
    )
    def status_card(title: str, fn: Any) -> str:
        try:
            data=base._safe_call(fn)
        except Exception:
            data={"status":"unavailable"}
        return (
            '<div class="v985-settings-status">'
            '<strong>'+escape(title)+'</strong>'
            +v951._status_rows(data)
            +v951._details(data)
            +'</div>'
        )
    diagnostics=(
        status_card("Runtime",base.build_status)
        +status_card("Tracking",base.tracking_status)
        +status_card("Knowledge",base.context_engine().status)
        +status_card("File Store",base.file_store().status)
        +status_card("Scheduler",base.scheduler().status)
    )
    # Reuse the production account editor and CSRF-protected actions. Do not
    # create an alternative credentials store or duplicate account-management API.
    from .webgui_v962_views import render_accounts
    account_panel = render_accounts(base, request)
    prefix = '<section class="tab-panel" id="panel-accounts" data-panel="accounts">'
    if not (account_panel.startswith(prefix) and account_panel.endswith("</section>")):
        raise RuntimeError("Unexpected registered account editor markup")
    account_panel = account_panel[len(prefix):-len("</section>")]
    account_panel = re.sub(
        r'href="/\?ui_view=accounts&amp;edit_account=([^"]+)#accounts"',
        lambda match: (
            'href="/?ui_view=system&amp;settings_tab=accounts&amp;edit_account='
            + match.group(1) + '#system"'
        ),
        account_panel,
    )
    account_panel = account_panel.replace(
        'href="/?ui_view=accounts#accounts"',
        'href="/?ui_view=system&amp;settings_tab=accounts#system"',
    )
    account_panel = re.sub(
        r'(<form\b[^>]*\baction="/dashboard/account/(?:save|test|default|delete)"[^>]*>)',
        r'\1<input type="hidden" name="return_to" value="settings">',
        account_panel,
    )
    current = str(request.query_params.get("settings_tab") or "versions").strip()
    if current not in {"versions","accounts","services","diagnostics"}:
        current = "versions"
    def tab(name: str, label: str) -> str:
        active = name == current
        return (
            '<button type="button" role="tab" id="v986-settings-tab-' + name + '" '
            'data-v986-settings-tab="' + name + '" '
            'aria-controls="v986-settings-pane-' + name + '" '
            'aria-selected="' + ("true" if active else "false") + '"'
            + (' class="current"' if active else '') + '>' + label + '</button>'
        )
    def pane_attrs(name: str) -> str:
        return (
            ' role="tabpanel" id="v986-settings-pane-' + name + '" '
            'aria-labelledby="v986-settings-tab-' + name + '" '
            'data-v986-settings-pane="' + name + '"'
            + ('' if name == current else ' hidden')
        )
    html=(
        '<div class="v985-settings" data-v986-settings>'
        '<div class="v985-pagehead"><div><h2>Settings</h2>'
        '<p>Release management, accounts, privacy and system configuration</p></div></div>'
        '<div class="v985-settings-tabs" role="tablist" aria-label="Settings categories">'
        +tab("versions","Versions &amp; updates")
        +tab("accounts","Email accounts")
        +tab("services","Manage services")
        +tab("diagnostics","Diagnostics")
        +'</div>'
        '<div'+pane_attrs("versions")+'>'+version+'</div>'
        '<div'+pane_attrs("accounts")+'>'
        '<div class="v986-accounts-head">'
        '<div><h3>Email accounts</h3><p>Add, edit, test, set default or remove a configured IMAP/SMTP account. '
        'Passwords and application tokens are never displayed.</p></div>'
        '<button type="button" data-v986-add-account>+ Add email account</button>'
        '</div>'
        +account_panel+'</div>'
        '<div'+pane_attrs("services")+'>'
        '<section class="v985-settings-card" id="v985-settings-other">'
        '<h3>Services &amp; permissions</h3><div class="v985-settings-shortcuts">'
        +shortcuts+'</div></section></div>'
        '<div'+pane_attrs("diagnostics")+'>'
        '<section class="v985-settings-card" id="v985-settings-diagnostics">'
        '<h3>Detailed service status</h3><p class="small muted">'
        'Live subsystem snapshots; each section can be inspected independently.</p>'
        '<div class="v985-settings-diagnostics">'+diagnostics+'</div></section></div>'
        '</div>'
    )
    return _panel("system",html)



SETTINGS_SCRIPT = r"""
<script id="v986-settings-tabs">
(() => {
  // Event delegation survives replacement of lazy settings fragments.
  document.addEventListener('click', event => {
    const button = event.target.closest('button[data-v986-settings-tab]');
    if (!button) return;
    const root = button.closest('[data-v986-settings]');
    if (!root) return;
    const chosen = button.dataset.v986SettingsTab;
    root.querySelectorAll('[data-v986-settings-pane]').forEach(pane => {
      pane.hidden = pane.dataset.v986SettingsPane !== chosen;
    });
    root.querySelectorAll('button[data-v986-settings-tab]').forEach(tab => {
      const selected = tab.dataset.v986SettingsTab === chosen;
      tab.classList.toggle('current', selected);
      tab.setAttribute('aria-selected', String(selected));
    });
    const url = new URL(location.href);
    url.searchParams.set('ui_view','system');
    url.searchParams.set('settings_tab',chosen);
    url.hash='system';
    history.replaceState(null,'',url.toString());
  });
  document.addEventListener('click', event => {
    const btn = event.target.closest('[data-v986-add-account]');
    if(!btn)return;
    const root = btn.closest('[data-v986-settings]');
    if(!root)return;
    if(new URL(location.href).searchParams.has('edit_account')){
      window.location.assign('/?ui_view=system&settings_tab=accounts#system');
      return;
    }
    const editor=root.querySelector('details[data-v962-state-key="accounts-editor"]');
    if(editor){
      editor.open=true;
      editor.scrollIntoView({block:'start',behavior:'smooth'});
      editor.querySelector('input[name="account_id"]')?.focus();
    }
  });
})();
</script>"""

SETTINGS_STYLE = r"""
.v985-settings{max-width:1380px;margin:auto}
.v985-settings-tabs{display:flex;flex-wrap:wrap;gap:7px;border-bottom:1px solid var(--v970-line);margin:15px 0}
.v985-settings-tabs button{color:var(--v970-muted)!important;padding:10px 13px!important;text-decoration:none;font-size:12px;
 border:0!important;border-bottom:2px solid transparent!important;background:transparent!important;border-radius:0!important}
.v985-settings-tabs button.current,.v985-settings-tabs button:hover{color:var(--v970-accent)!important;border-bottom-color:var(--v970-accent)!important}
.v985-settings [data-v986-settings-pane][hidden]{display:none!important}
.v986-accounts-head{display:flex;justify-content:space-between;gap:12px;align-items:center;flex-wrap:wrap;margin:16px 0}
.v986-accounts-head p{color:var(--v970-muted);font-size:11px;margin:4px 0}
.v986-accounts-head button{background:var(--v970-accent);color:white;border-radius:8px;padding:8px 14px}
.v985-settings [data-v986-settings-pane="accounts"] .v951-pagehead{display:none}
.v985-settings [data-v986-settings-pane="accounts"] .v962-collapsible{margin:12px 0}
.v985-settings [data-v986-settings-pane="accounts"] td.actions{display:flex;flex-wrap:wrap;gap:6px}
.v985-settings [data-v986-settings-pane="accounts"] td.actions form{display:inline-flex}
.v985-settings [data-v986-settings-pane="accounts"] .card.wide{padding:15px!important}
.v985-settings [data-v986-settings-pane="accounts"] .form-section{padding:12px 0;border-top:1px solid var(--v970-line)}
.v985-settings .card.wide,.v985-settings-card{border:1px solid var(--v970-line)!important;
 background:var(--v970-surface)!important;border-radius:10px!important;padding:18px!important;margin:14px 0!important}
.v985-settings h3{font-size:16px;margin:0 0 12px}
.v985-settings #v984-versions form{display:flex;gap:12px;flex-wrap:wrap;align-items:end}
.v985-settings #v984-versions form label{display:flex;flex-direction:column;gap:5px}
.v985-settings #v984-versions select{min-width:160px}
.v985-settings-shortcuts{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:11px}
.v985-settings-shortcut{display:grid;grid-template-columns:1fr auto;gap:4px 12px;border:1px solid var(--v970-line);
 border-radius:9px;padding:13px 16px;text-decoration:none;color:var(--v970-text);background:var(--v970-surface-2)}
.v985-settings-shortcut strong{font-size:12px}.v985-settings-shortcut span{font-size:10px;color:var(--v970-muted)}
.v985-settings-shortcut b{color:var(--v970-accent);grid-row:1/3;grid-column:2;align-self:center}
.v985-settings-card>summary{font-size:16px;font-weight:800;cursor:pointer}
.v985-settings-diagnostics{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:11px;margin-top:12px}
.v985-settings-status{border:1px solid var(--v970-line);border-radius:9px;padding:12px;overflow-wrap:anywhere}
.v985-settings-status>strong{display:block;font-size:13px;margin-bottom:9px}
@media(max-width:690px){.v985-settings-shortcuts{grid-template-columns:1fr}.v985-settings .card.wide{padding:12px!important}}
"""


def install_settings_v985(v951_mod: Any, v962: Any) -> None:
    v951_mod.render_system=render_settings_v985
    previous=v962._styles
    if getattr(previous,"_postmaster_v985_settings",False):return
    def styles()->str:
        return previous().rstrip()+"\n"+SETTINGS_STYLE
    styles._postmaster_v985_settings=True
    v962._styles=styles
    if 'id="v986-settings-tabs"' not in v962.SCRIPT:
        v962.SCRIPT += SETTINGS_SCRIPT
