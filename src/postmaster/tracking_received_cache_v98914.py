"""Tracking received-mail statistics from already-synchronized Inbox caches.

Works for All accounts and for a single selected account, with no IMAP calls.
All account IDs come from the enabled account registry, not browser input.
"""
from __future__ import annotations

from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

from .webgui_v953 import _account_rows, _account_id
from .webgui_tracking_v984 import _time

_MAX_ROWS = 20000


def cached_received_dates(
    base: Any, account_id: str, since: datetime, until: datetime
) -> tuple[list[str], str]:
    """Return timestamps for received-role cache messages in the given period."""
    enabled = [
        _account_id(row) for row in _account_rows(base)
        if _account_id(row)
    ]
    selected = [account_id] if account_id and account_id in enabled else (
        enabled if not account_id else []
    )
    if not selected:
        return [], "No enabled email accounts selected; configure an account in Settings."
    store = base.mailbox_cache_store()
    dates: list[str] = []
    # Use the registered and enabled account IDs, never an arbitrary value
    # supplied through query params. The mailbox role comes from the cache.
    params = ",".join("?" for _ in selected)
    with store._connect() as conn:
        rows = conn.execute(
            "SELECT m.date_value FROM mailbox_cache_messages AS m "
            "JOIN mailbox_cache_mailboxes AS b "
            "ON b.account_id=m.account_id AND b.name=m.mailbox "
            "WHERE b.role='received' "
            f"AND m.account_id IN ({params}) "
            "AND m.date_value IS NOT NULL AND m.date_value!='' "
            "ORDER BY m.date_value DESC LIMIT ?",
            (*selected, _MAX_ROWS + 1),
        ).fetchall()
    truncated = len(rows) > _MAX_ROWS
    for row in rows[:_MAX_ROWS]:
        raw = str(row["date_value"] or "")
        when = _time(raw)
        if when is None:
            try:
                when = parsedate_to_datetime(raw)
            except (ValueError, TypeError, IndexError):
                continue
        if when is None:
            continue
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        utc = when.astimezone(UTC)
        if since <= utc < until:
            dates.append(utc.isoformat())
    scope = ("all " + str(len(selected)) + " enabled accounts"
             if not account_id else "selected account")
    note = (
        "Cached Inbox dates from " + scope
        + "; reflects mailboxes already synchronized, without new IMAP requests."
    )
    if truncated:
        note += " Display capped at 20,000 cached messages; some dates may be omitted."
    return dates, note
