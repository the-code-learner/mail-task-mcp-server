"""Regression for the v9.6.9 pending-preview API used by WebGUI 9.8.6.

The v9.8.5 screen mistakenly required confirmation_token (removed in v9.6.9)
and passed an invalid keyword to execute. These route-level tests cover both
POSTs, explicit approval, CSRF and no mutation on preview.
"""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.testclient import TestClient

from postmaster.webgui_versions_v984 import install_versions_webgui_v984


class _Base:
    def _csrf_value(self) -> str:
        return "csrf-v986-check"

    async def _verified_form(self, request):
        form=await request.form()
        if form.get("csrf") != self._csrf_value():
            return form,PlainTextResponse("Invalid CSRF",status_code=403)
        return form,None


class _Core:
    def __init__(self):
        self.preview_calls=[]
        self.execute_calls=[]

    def runtime_version_change_preview(self,operation,target_version=None):
        self.preview_calls.append((operation,target_version))
        if operation not in {"update-latest","switch-version","pin-version","rollback-version"}:
            return {"ok":False,"error":"Unsupported operation"}
        return {
            "ok":True,"approval_required":True,
            "version_change_applied":False,
            "preview_id":"pv_demo_6fd25",
            "preview_id_is_authorization":False,
            "confirmation_expires_in_seconds":300,
            "action_preview":{
                "operation":operation,"current_version":"9.8.5",
                "current_build":"v9.8.5","current_selector":"latest",
                "requested_selector":"latest",
                "target_version_ref":"v9.8.5",
            },
        }

    def runtime_version_change_execute(self,operation,target_version,preview_id=None):
        self.execute_calls.append((operation,target_version,preview_id))
        if preview_id!="pv_demo_6fd25" or target_version!="v9.8.5":
            return {"ok":False,"error":"Invalid preview"}
        return {"ok":True,"target_version_ref":target_version,"restart_scheduled":True}


def _client():
    app=Starlette()
    base=_Base()
    core=_Core()
    views=SimpleNamespace(
        render_overview=lambda *args:"<section>Previous overview</section>",
        v951=SimpleNamespace(render_system=lambda *args:"<section>Previous system</section>"),
    )
    v962=SimpleNamespace(NAV=(("system","System"),))
    v970=SimpleNamespace(NAV_GROUPS=(("Control",(("system","System","SY"),)),),
                          VIEW_LABELS={})
    install_versions_webgui_v984(app,base,core,v962,views,v970)
    return TestClient(app),core,base


class WebguiVersionPendingPreviewV986(unittest.TestCase):
    def test_preview_renders_real_confirmation_with_server_side_preview_id(self):
        client,core,base=_client()
        with client:
            response=client.post("/dashboard/versions/preview",data={
                "csrf":base._csrf_value(),
                "operation":"update-latest",
                "target_version":"v9.8.5",
            })
        self.assertEqual(response.status_code,200,response.text[:500])
        self.assertIn("Confirm version change",response.text)
        self.assertIn('name="preview_id" value="pv_demo_6fd25"',response.text)
        self.assertIn('name="approved" value="yes" required',response.text)
        self.assertIn('name="target_version" value="v9.8.5"',response.text)
        self.assertNotIn('name="confirmation_token"',response.text)
        self.assertEqual(core.preview_calls,[("update-latest",None)])
        self.assertEqual(core.execute_calls,[])

    def test_preview_is_read_only_and_csrf_protected(self):
        client,core,base=_client()
        with client:
            response=client.post("/dashboard/versions/preview",data={
                "csrf":"wrong","operation":"update-latest"})
        self.assertEqual(response.status_code,403)
        self.assertFalse(core.preview_calls)
        self.assertFalse(core.execute_calls)

    def test_execute_requires_explicit_second_post_and_exact_pending_id(self):
        client,core,base=_client()
        with client:
            denied=client.post("/dashboard/versions/execute",data={
                "csrf":base._csrf_value(),"operation":"update-latest",
                "target_version":"v9.8.5","preview_id":"pv_demo_6fd25",
            })
            invalid=client.post("/dashboard/versions/execute",data={
                "csrf":base._csrf_value(),"approved":"yes",
                "operation":"update-latest","target_version":"v9.8.5",
                "preview_id":"wrong",
            })
            approved=client.post("/dashboard/versions/execute",data={
                "csrf":base._csrf_value(),"approved":"yes",
                "operation":"update-latest","target_version":"v9.8.5",
                "preview_id":"pv_demo_6fd25",
            })
        self.assertEqual(denied.status_code,400)
        self.assertEqual(invalid.status_code,400)
        self.assertEqual(approved.status_code,200,approved.text[:500])
        self.assertIn("Version change scheduled",approved.text)
        self.assertEqual(core.execute_calls,[
            ("update-latest","v9.8.5","wrong"),
            ("update-latest","v9.8.5","pv_demo_6fd25"),
        ])

    def test_preview_error_returns_clear_failure_without_execute(self):
        client,core,base=_client()
        core.runtime_version_change_preview=lambda **kwargs: {
            "ok":False,"error":"No verified releases available",
        }
        with client:
            response=client.post("/dashboard/versions/preview",data={
                "csrf":base._csrf_value(),"operation":"update-latest",
            })
        self.assertEqual(response.status_code,400)
        self.assertIn("No verified releases available",response.text)
        self.assertEqual(core.execute_calls,[])


if __name__=="__main__":
    unittest.main()
