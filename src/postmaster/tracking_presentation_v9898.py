"""Read-only link between tracking analytics and the cached Sent mailbox.

A delivery Message-ID is not an IMAP UID. A clickable email destination is
shown only after resolving its precise (account_id, message_id) to a cached
Sent mailbox message. No implicit IMAP sync is performed.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import urlencode


def attach_sent_email_links(base: Any, events: list[dict[str, Any]]) -> None:
    requests = {
        (str(row.get("account_id") or "").strip(),
         str(row.get("message_id") or "").strip())
        for row in events
        if str(row.get("event_kind") or "").startswith("email_")
        and row.get("account_id") and row.get("message_id")
    }
    if not requests:
        return
    try:
        store = base.mailbox_cache_store()
        with store._connect() as conn:
            messages: dict[tuple[str, str], tuple[str, str]] = {}
            # All query arguments are parameters; constrain to cached Sent
            # roles so a reply from Inbox cannot be linked accidentally.
            accounts = sorted({account for account, _ in requests})
            for account in accounts:
                ids = sorted({mid for a, mid in requests if a == account})
                for i in range(0, len(ids), 150):
                    batch = ids[i:i + 150]
                    marks = ",".join("?" for _ in batch)
                    matches = conn.execute(
                        "SELECT m.message_id,m.mailbox,m.uid FROM mailbox_cache_messages m "
                        "JOIN mailbox_cache_mailboxes b ON b.account_id=m.account_id "
                        "AND b.name=m.mailbox WHERE m.account_id=? AND b.role='sent' "
                        f"AND m.message_id IN ({marks}) "
                        "ORDER BY m.date_value DESC,m.uid_int DESC",
                        (account, *batch),
                    ).fetchall()
                    for match in matches:
                        key = account, str(match["message_id"] or "")
                        if key not in messages:
                            messages[key] = str(match["mailbox"]), str(match["uid"])
    except (OSError, ValueError, Exception):
        # Analytics must remain usable when mail cache is not initialized;
        # never invent links to missing messages.
        return
    for row in events:
        key = (str(row.get("account_id") or "").strip(),
               str(row.get("message_id") or "").strip())
        if key not in messages:
            continue
        mailbox, uid = messages[key]
        args = {
            "ui_view": "inbox", "mailbox": mailbox, "account_id": key[0],
            "message_uid": uid, "since_days": "3650",
        }
        row["email_href"] = "/?" + urlencode(args) + "#inbox"
