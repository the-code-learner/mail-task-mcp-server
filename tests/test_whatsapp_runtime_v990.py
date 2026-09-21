from __future__ import annotations

from pathlib import Path
import sys
import types
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from starlette.applications import Starlette

mcp_mod = sys.modules.setdefault("mcp", types.ModuleType("mcp"))
mcp_types = sys.modules.setdefault("mcp.types", types.ModuleType("mcp.types"))
if not hasattr(mcp_types, "ToolAnnotations"):
    class ToolAnnotations:
        def __init__(self, **kwargs): self.__dict__.update(kwargs)
    mcp_types.ToolAnnotations = ToolAnnotations

from postmaster.whatsapp_runtime_v990 import MCP_WHATSAPP_COMMANDS_V990, install_whatsapp_runtime_v990
from postmaster.whatsapp_v990.service import WhatsAppService, WhatsAppEventStore
from postmaster.whatsapp_v990.store import EncryptedAuthStore
from postmaster.webgui_whatsapp_v990 import render_whatsapp_panel, qr_renderer_status, install_webgui_whatsapp_v990


class FakeMCP:
    def __init__(self): self.tools={}
    def remove_tool(self,name): self.tools.pop(name,None)
    def add_tool(self,fn,name=None,annotations=None): self.tools[name or fn.__name__]=(fn,annotations)


class FakeAdapter:
    def __init__(self): self.connected=False; self.calls=[]
    async def start_pairing(self): self.calls.append("pair"); return {"ok":True,"qr":"qr-secret-payload","private_key":"never-return-this"}
    async def reconnect(self): self.connected=True; self.calls.append("reconnect"); return {"ok":True,"connected":True}
    async def send_text(self, **kwargs): self.calls.append(("text",kwargs)); return {"ok":True,"message_id":"m1"}
    async def send_media(self, **kwargs): self.calls.append(("media",kwargs)); return {"ok":True,"message_id":"m2"}
    async def list_groups(self): return [{"jid":"123@g.us","subject":"Test"}]
    def status(self): return {"configured":True,"connected":self.connected}


class WhatsAppRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def make(self, td):
        adapter=FakeAdapter()
        service=WhatsAppService(
            EncryptedAuthStore(str(Path(td)/"auth.db")),
            WhatsAppEventStore(str(Path(td)/"events.db")),
            adapter,
        )
        core=SimpleNamespace(mcp=FakeMCP())
        base=SimpleNamespace()
        out=install_whatsapp_runtime_v990(base,core,lambda:{"ok":True,"version_capability":"9.9.0","mcp_command_count_expected":130},service=service)
        return base,core,adapter,service,out

    async def test_exact_eight_tools_and_safe_status(self):
        with TemporaryDirectory() as td:
            _,core,_,_,out=self.make(td)
            expected={"whatsapp_status","whatsapp_start_pairing","whatsapp_reconnect","whatsapp_list_messages","whatsapp_send_text","whatsapp_send_media","whatsapp_list_groups","whatsapp_list_receipts"}
            self.assertEqual(set(core.mcp.tools)-{"runtime_status"}, expected)
            self.assertEqual(out["tool_count"], MCP_WHATSAPP_COMMANDS_V990)
            status=core.whatsapp_status()
            self.assertFalse(status["auth_store"]["private_material_exposed"])
            runtime=core.runtime_status()
            self.assertTrue(runtime["whatsapp"]["read_receipts_asymmetric"])
            self.assertFalse(runtime["whatsapp"]["local_read_emits_receipt"])
            self.assertFalse(runtime["whatsapp"]["protocol_interop_verified"])
            self.assertFalse(runtime["whatsapp"]["signal_multidevice_verified"])
            self.assertFalse(runtime["whatsapp"]["controlled_account_interop_verified"])

    async def test_pairing_strips_private_material_and_persists_qr(self):
        with TemporaryDirectory() as td:
            _,core,_,service,_=self.make(td)
            result=await core.whatsapp_start_pairing()
            self.assertNotIn("private_key", result)
            self.assertFalse(result["private_material_exposed"])
            self.assertEqual(service.pairing_qr(), "qr-secret-payload")

    async def test_read_does_not_receipt_but_explicit_reply_may(self):
        with TemporaryDirectory() as td:
            _,core,adapter,service,_=self.make(td)
            service.record_incoming_message(message_id="incoming1",jid="123@s.whatsapp.net",text="hello")
            listed=core.whatsapp_list_messages()
            self.assertFalse(listed["read_receipt_emitted"])
            self.assertEqual(core.whatsapp_list_receipts()["receipts"], [])
            sent=await core.whatsapp_send_text("123@s.whatsapp.net","reply",reply_to_message_id="incoming1")
            self.assertTrue(sent["read_receipt_emitted"])
            self.assertTrue(adapter.calls[-1][1]["emit_read_receipt"])
            receipts=core.whatsapp_list_receipts()["receipts"]
            self.assertEqual(receipts[0]["source"],"outbound_reply")

    async def test_media_requires_stored_file_identifier(self):
        with TemporaryDirectory() as td:
            _,core,adapter,_,_=self.make(td)
            bad=await core.whatsapp_send_media("123@s.whatsapp.net","")
            self.assertFalse(bad["ok"])
            good=await core.whatsapp_send_media("123@g.us","file-123",caption="doc")
            self.assertTrue(good["ok"])
            self.assertEqual(adapter.calls[-1][1]["stored_file_id"],"file-123")

    async def test_webgui_fragment_renders_qr_locally_without_remote_service(self):
        panel=render_whatsapp_panel({"paired":False,"network":{"connected":False}},qr_payload='https://wa.me/settings/linked_devices#safe-test')
        qr_status=qr_renderer_status()
        self.assertTrue(qr_status["local"])
        self.assertFalse(qr_status["remote_service"])
        self.assertNotIn("data-qr-payload",panel)
        self.assertNotIn("wa.me/settings/linked_devices#safe-test",panel)
        self.assertNotIn("<script>",panel)
        if qr_status["available"]:
            self.assertIn("WhatsApp pairing QR",panel)
            self.assertIn("data:image/svg+xml;base64,",panel)
        else:
            self.assertIn("Pairing QR renderer unavailable",panel)
            self.assertNotIn("data:image/svg+xml;base64,",panel)

    async def test_webgui_installer_adds_view_nav_and_explicit_routes(self):
        with TemporaryDirectory() as td:
            base,core,adapter,service,_=self.make(td)
            base.whatsapp_service_v990=lambda: service
            async def verified_form(request): return ({}, None)
            base._verified_form=verified_form
            shell=SimpleNamespace(VIEWS=("inbox",),render_view=lambda b,c,r,v: "old")
            views=SimpleNamespace(VIEWS=("inbox",),render_view=shell.render_view)
            nav=SimpleNamespace(NAV_GROUPS=(("Communicate", (("inbox","Inbox","IN"),)),))
            app=Starlette()
            install_webgui_whatsapp_v990(app,base,shell,views,nav)
            self.assertIn("whatsapp",shell.VIEWS)
            self.assertIn("whatsapp",views.VIEWS)
            self.assertTrue(any(item[0]=="whatsapp" for _heading,links in nav.NAV_GROUPS for item in links))
            paths={getattr(route,"path",None) for route in app.router.routes}
            self.assertIn("/dashboard/whatsapp/pair",paths)
            self.assertIn("/dashboard/whatsapp/reconnect",paths)
            rendered=shell.render_view(base,core,None,"whatsapp")
            self.assertIn("data-panel=\"whatsapp\"",rendered)
            self.assertIn("Pair device",rendered)


if __name__ == "__main__": unittest.main()
