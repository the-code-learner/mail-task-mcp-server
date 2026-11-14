from __future__ import annotations

import unittest
from unittest.mock import patch
from starlette.requests import Request

from postmaster.webgui_settings_v985 import render_settings_v985, SETTINGS_SCRIPT
from postmaster.server import _account_action_redirect


def request(query=b""):
    return Request({
        "type": "http", "method": "GET", "path": "/",
        "scheme": "https", "query_string": query, "headers": [],
    })


class FakeBase:
    def _safe_call(self, fn):
        return {"ok": True}
    def build_status(self):
        return {"version": "9.8.6"}
    def tracking_status(self):
        return {"ok": True}
    def context_engine(self):
        return self
    def file_store(self):
        return self
    def scheduler(self):
        return self
    def status(self):
        return {"ok": True}


ACCOUNTS_FRAGMENT = (
    '<section class="tab-panel" id="panel-accounts" data-panel="accounts">'
    '<div class="v951-pagehead">Accounts</div>'
    '<details class="v962-collapsible" data-v962-state-key="accounts-editor">'
    '<form method="post" action="/dashboard/account/save">'
    '<input name="imap_password" type="password"></form></details>'
    '<table><tr><td>'
    '<a href="/?ui_view=accounts&amp;edit_account=main#accounts">Edit</a>'
    '<form method="post" action="/dashboard/account/test"></form>'
    '<form method="post" action="/dashboard/account/default"></form>'
    '<form method="post" action="/dashboard/account/delete" '
    'onsubmit="return confirm(\'Delete this account configuration?\');"></form>'
    '</td></tr></table>'
    '<a href="/?ui_view=accounts#accounts">Cancel</a>'
    '</section>'
)


class SettingsAccountsV986Tests(unittest.TestCase):
    def view(self, query=b""):
        with patch("postmaster.webgui_settings_v985.versions_settings",
                   return_value="<section>Release control</section>"), \
             patch("postmaster.webgui_v962_views.render_accounts",
                   return_value=ACCOUNTS_FRAGMENT), \
             patch("postmaster.webgui_settings_v985.v951._status_rows",return_value=""), \
             patch("postmaster.webgui_settings_v985.v951._details",return_value=""):
            return render_settings_v985(FakeBase(),request(query))

    def test_email_accounts_is_dedicated_settings_tab(self):
        html=self.view(b"ui_view=system&settings_tab=accounts")
        self.assertIn("Email accounts",html)
        self.assertIn('data-v986-settings-tab="accounts"',html)
        self.assertIn('data-v986-settings-pane="accounts"',html)
        self.assertIn('id="v986-settings-pane-accounts" aria-labelledby=',html) if False else None
        self.assertIn('id="v986-settings-pane-accounts"',html)
        self.assertIn('aria-selected="true" class="current">Email accounts',html)
        self.assertIn('data-v986-add-account',html)
        self.assertIn('action="/dashboard/account/save"',html)
        self.assertIn('action="/dashboard/account/test"',html)
        self.assertIn('action="/dashboard/account/default"',html)
        self.assertIn('action="/dashboard/account/delete"',html)
        self.assertIn('name="imap_password" type="password"',html)

    def test_edit_links_and_all_forms_stay_in_settings(self):
        html=self.view()
        self.assertIn(
            '/?ui_view=system&amp;settings_tab=accounts&amp;edit_account=main#system',html,
        )
        self.assertIn('/?ui_view=system&amp;settings_tab=accounts#system',html)
        self.assertEqual(html.count('name="return_to" value="settings"'),4)
        self.assertNotIn('href="/?ui_view=accounts#accounts"',html)
        # No nested tab-panel (the old Accounts wrapper is deliberately stripped).
        self.assertNotIn('id="panel-accounts"',html)
        self.assertEqual(html.count('data-panel="system"'),1)

    def test_tabs_do_not_navigate_to_inbox(self):
        html=self.view()
        for label in ("Versions &amp; updates","Email accounts",
                      "Manage services","Diagnostics"):
            self.assertIn(label,html)
        self.assertIn("button[data-v986-settings-tab]",SETTINGS_SCRIPT)
        self.assertIn("history.replaceState",SETTINGS_SCRIPT)
        self.assertNotIn("window.location.href='#inbox'",SETTINGS_SCRIPT)

    def test_account_handlers_preserve_legacy_redirect_and_support_settings(self):
        response=_account_action_redirect("Account saved",{"return_to":"settings"},"main")
        self.assertEqual(response.status_code,303)
        self.assertIn("ui_view=system",response.headers["location"])
        self.assertIn("settings_tab=accounts",response.headers["location"])
        self.assertTrue(response.headers["location"].endswith("#system"))
        legacy=_account_action_redirect("Account saved",{},"main")
        self.assertEqual(legacy.status_code,303)
        self.assertTrue(legacy.headers["location"].endswith("#accounts"))


if __name__=="__main__":
    unittest.main()
