from __future__ import annotations

import inspect
import unittest
from typing import get_type_hints


class V972FinalMailCompositionTests(unittest.TestCase):
    def test_final_runtime_mail_factory_is_v972_and_uses_v972_core_store(self) -> None:
        import postmaster.runtime as runtime
        from postmaster.stored_file_public_v972 import PostmasterV972MailClient

        hints = get_type_hints(runtime.mail_client)
        self.assertIs(hints.get("return"), PostmasterV972MailClient)

        closure = inspect.getclosurevars(runtime.mail_client)
        core = closure.nonlocals["core"]
        self.assertIs(runtime.link_store, core.link_store)
        self.assertEqual(core.link_store.__module__, "postmaster.stored_file_public_v972")
        self.assertEqual(core.link_store.__name__, "_store")

        tools = runtime.mcp._tool_manager.list_tools()
        self.assertEqual(len(tools), 130)
        names = {tool.name for tool in tools}
        self.assertTrue({
    "email_search_status",
    "search_emails_hybrid",
    "get_email_calendar_invites",
    "send_job_calendar_invite",
    "whatsapp_status",
    "whatsapp_start_pairing",
    "whatsapp_reconnect",
    "whatsapp_list_messages",
    "whatsapp_send_text",
    "whatsapp_send_media",
    "whatsapp_list_groups",
    "whatsapp_list_receipts",
} <= names)
        self.assertIn("send_email", names)
        self.assertIn("get_stored_file_resource", {tool.name for tool in tools})


if __name__ == "__main__":
    unittest.main()
