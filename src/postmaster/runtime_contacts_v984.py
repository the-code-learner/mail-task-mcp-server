"""Expose the instance-global contact book through the existing MCP control plane."""
from __future__ import annotations

import os
from typing import Any
from mcp.types import ToolAnnotations
from .contacts_v984 import ContactBook, incoming_candidates


def install_runtime_contacts_v984(base: Any, core: Any) -> dict[str, Any]:
    book: ContactBook | None = None

    def store() -> ContactBook:
        nonlocal book
        if book is None:
            book = ContactBook(os.getenv("POSTMASTER_CONTACTS_DB", "/data/contacts-v984.db"))
        return book

    def list_contacts(owner_id: str | None = None, project_id: str | None = None,
                      account_id: str | None = None, status: str | None = None,
                      limit: int = 200, offset: int = 0):
        """Read-only. Global contact book; optional scopes are organizational filters, not ACL."""
        try:
            return store().list(
                owner_id=owner_id, project_id=project_id,
                account_id=account_id, status=status, limit=limit, offset=offset,
            )
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    def list_incoming_contact_candidates(account_id: str | None = None,
                                         limit: int = 100, since_days: int = 365):
        """Read-only. Discover INBOX senders for review, without authorizing recipients."""
        try:
            return incoming_candidates(
                base, store(), account_id=account_id,
                limit=limit, since_days=since_days,
            )
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    def save_contact(email: str, name: str = "", note: str = "",
                     account_id: str = "", owner_id: str = "", project_id: str = ""):
        """WRITE. Add/update a contact; does NOT authorize the recipient for outbound email."""
        try:
            return store().save(
                email=email, name=name, note=note, source_account_id=account_id,
                owner_id=owner_id, project_id=project_id,
            )
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    def set_contact_ignored(email: str, ignored: bool = True, account_id: str = ""):
        """WRITE. Ignore or restore an INBOX sender candidate, without changing email policy."""
        try:
            return store().set_ignored(
                email=email, ignored=ignored, source_account_id=account_id,
            )
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    original_status = core.runtime_status

    def runtime_status():
        previous = original_status()
        return {
            **(previous if isinstance(previous, dict) else {}),
            "version_capability": "9.8.4",
            "mcp_command_count_expected": 134,
            "contact_book_v984": {
                "global_access": True, "incoming_review": True,
                "save_does_not_authorize_send": True,
            },
        }

    for name, fn, read in (
        ("list_contacts", list_contacts, True),
        ("list_incoming_contact_candidates", list_incoming_contact_candidates, True),
        ("save_contact", save_contact, False),
        ("set_contact_ignored", set_contact_ignored, False),
        ("runtime_status", runtime_status, True),
    ):
        if name == "runtime_status":
            core.mcp.remove_tool(name)
        core.mcp.add_tool(
            fn, name=name,
            annotations=ToolAnnotations(read_only_hint=read, idempotent_hint=read),
        )
        setattr(core, name, fn)
        setattr(base, name, fn)
    base.contact_book_v984 = store
    return {"runtime_status": runtime_status, "mcp_command_count_expected": 134}
