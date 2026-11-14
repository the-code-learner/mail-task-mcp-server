from __future__ import annotations

import unittest
from datetime import UTC,datetime,timedelta
from unittest.mock import patch
from starlette.requests import Request

from postmaster.webgui_contacts_v984 import render_contacts
from postmaster.webgui_tracking_v985 import _period_form


def req(query=b""):
    return Request({
        "type":"http","method":"GET","scheme":"https",
        "path":"/","query_string":query,"headers":[],
    })


class FakeAccounts:
    def list_accounts(self):
        return [
            {"id":"primary","label":"Primary", "email_address":"primary@invalid.test"},
            {"id":"secondary","label":"Secondary", "email_address":"second@invalid.test"},
        ]


class FakeBase:
    def account_store(self):
        return FakeAccounts()
    def _csrf_value(self):
        return "test-csrf"


class FakeCore:
    def list_incoming_contact_candidates(self,**kwargs):
        return {"ok":True,"contacts":[]}


class ContactAndPeriodV986Tests(unittest.TestCase):
    def test_contacts_only_uses_global_email_account_selector(self):
        html=render_contacts(FakeBase(),FakeCore(),req(b"account_id=secondary"))
        self.assertNotIn('name="account_id"><option',html)
        self.assertNotIn('class="v951-toolbar"',html)
        self.assertIn("Email history for",html)
        self.assertIn("Secondary",html)
        self.assertIn("global header",html)
        self.assertIn("From Inbox",html)
        self.assertIn("From Sent",html)

    def test_tracking_period_links_include_actual_selected_period(self):
        start=datetime(2026,11,1,tzinfo=UTC)
        end=start+timedelta(days=90)
        with patch("postmaster.webgui_tracking_v985._period",
                   return_value=(start,end,"90d")):
            html=_period_form(req(b"period=90d&account_id=primary"),"90d")
        self.assertIn("24 hours",html)
        self.assertIn("7 days",html)
        self.assertIn("28 days",html)
        self.assertIn("90 days",html)
        self.assertIn("Custom",html)
        self.assertIn("period=90d",html)
        self.assertIn('aria-current="page"',html)
        self.assertIn('name="period" value="custom"',html)
        self.assertIn('account_id=primary',html)
        self.assertNotIn('<button type="submit" name="period"',html)

    def test_tracking_custom_date_form_has_explicit_period(self):
        start=datetime(2026,11,1,tzinfo=UTC)
        end=start+timedelta(days=3)
        with patch("postmaster.webgui_tracking_v985._period",
                   return_value=(start,end,"custom")):
            html=_period_form(
                req(b"period=custom&from=2026-11-01&to=2026-11-03"),"custom"
            )
        self.assertIn('name="period" value="custom"',html)
        self.assertIn('type="date" name="from" value="2026-11-01"',html)
        self.assertIn('type="date" name="to" value="2026-11-03"',html)


if __name__=="__main__":
    unittest.main()
