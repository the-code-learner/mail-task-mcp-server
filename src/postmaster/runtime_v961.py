from __future__ import annotations

import re
from typing import Any

from . import mail_v960


_BASE_CLASSIFY_MAILBOX_ROLE = mail_v960.classify_mailbox_role
_HIERARCHY_RE = re.compile(r"[./\\]+")
_ROLE_ALIASES: dict[str, set[str]] = {
    "received": {"inbox", "posta in arrivo", "in arrivo"},
    "sent": {
        "sent", "sent items", "sent messages", "sent mail", "posta inviata",
        "inviata", "inviati",
    },
    "spam": {
        "junk", "junk e-mail", "junk email", "spam", "posta indesiderata",
        "indesiderata",
    },
    "drafts": {"drafts", "draft", "bozze", "bozza"},
    "trash": {
        "trash", "deleted", "deleted items", "deleted messages", "bin",
        "wastebasket", "cestino",
    },
}


def _mailbox_name_candidates(name: str) -> list[str]:
    """Return stable logical-name candidates for provider/hierarchical mailboxes."""
    normalized = " ".join(str(name or "").strip().strip('"').casefold().split())
    if not normalized:
        return []
    parts = [part.strip() for part in _HIERARCHY_RE.split(normalized) if part.strip()]
    candidates = [normalized]
    candidates.extend(reversed(parts))
    return list(dict.fromkeys(candidates))


def classify_mailbox_role_v961(name: str, flags: list[str], settings: Any) -> str:
    """Keep v9.6 Special-Use/config rules, then classify hierarchical leaf names."""
    role = _BASE_CLASSIFY_MAILBOX_ROLE(name, flags, settings)
    if role != "other":
        return role
    for candidate in _mailbox_name_candidates(name):
        for logical_role, aliases in _ROLE_ALIASES.items():
            if candidate in aliases:
                return logical_role
    return "other"


def install_runtime_v961() -> None:
    """Install the v9.6.1 role-consistency hotfix without changing MCP names."""
    mail_v960.classify_mailbox_role = classify_mailbox_role_v961
