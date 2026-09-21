from __future__ import annotations

"""Clean-room WhatsApp group metadata/listing helpers.

The wire query follows the current public Web companion protocol: an IQ get to @g.us in
namespace w:g2 with a participating request asking for participants and description.
This module is read-only: it does not create, mutate, join, leave or send to groups.
"""

from typing import Any

from .binary import BinaryNode
from .jid import parse_jid


class WhatsAppGroupError(ValueError):
    pass


def build_participating_groups_query() -> BinaryNode:
    return BinaryNode(
        "iq",
        {"to": "@g.us", "xmlns": "w:g2", "type": "get"},
        [
            BinaryNode(
                "participating",
                {},
                [
                    BinaryNode("participants"),
                    BinaryNode("description"),
                ],
            )
        ],
    )


def build_group_metadata_query(jid: str) -> BinaryNode:
    parsed = parse_jid(str(jid))
    if not parsed.is_group:
        raise WhatsAppGroupError("WhatsApp group metadata query requires a g.us JID")
    return BinaryNode(
        "iq",
        {"to": str(parsed), "xmlns": "w:g2", "type": "get"},
        [BinaryNode("query", {"request": "interactive"})],
    )


def _text(node: BinaryNode | None) -> str | None:
    if node is None:
        return None
    value = node.content
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return value.decode("utf-8", errors="replace")
    if isinstance(value, str):
        return value
    return None


def _int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalized_group_id(raw: str) -> str:
    value = str(raw or "").strip()
    if not value:
        raise WhatsAppGroupError("WhatsApp group metadata is missing id")
    return value if "@" in value else f"{value}@g.us"


def parse_group_metadata(group: BinaryNode) -> dict[str, Any]:
    if group.tag != "group":
        raise WhatsAppGroupError(f"Expected WhatsApp group node, got {group.tag!r}")

    description = group.child("description")
    desc_body = description.child("body") if description is not None else None
    ephemeral = group.child("ephemeral")

    participants: list[dict[str, Any]] = []
    for participant in group.children("participant"):
        jid = str(participant.attrs.get("jid") or "").strip()
        if not jid:
            continue
        row: dict[str, Any] = {"id": jid}
        if participant.attrs.get("phone_number"):
            row["phone_number"] = participant.attrs["phone_number"]
        if participant.attrs.get("lid"):
            row["lid"] = participant.attrs["lid"]
        username = participant.attrs.get("participant_username") or participant.attrs.get("username")
        if username:
            row["username"] = username
        if participant.attrs.get("type"):
            row["admin"] = participant.attrs["type"]
        participants.append(row)

    group_id = _normalized_group_id(str(group.attrs.get("id") or ""))
    return {
        "id": group_id,
        "subject": group.attrs.get("subject"),
        "notify": group.attrs.get("notify"),
        "addressing_mode": "lid" if group.attrs.get("addressing_mode") == "lid" else "pn",
        "subject_owner": group.attrs.get("s_o"),
        "subject_owner_pn": group.attrs.get("s_o_pn"),
        "subject_owner_username": group.attrs.get("s_o_username"),
        "subject_time": _int(group.attrs.get("s_t")),
        "size": _int(group.attrs.get("size")) if group.attrs.get("size") is not None else len(participants),
        "creation": _int(group.attrs.get("creation")),
        "owner": group.attrs.get("creator"),
        "owner_pn": group.attrs.get("creator_pn"),
        "owner_username": group.attrs.get("creator_username"),
        "owner_country_code": group.attrs.get("creator_country_code"),
        "description": _text(desc_body),
        "description_id": description.attrs.get("id") if description else None,
        "description_owner": description.attrs.get("participant") if description else None,
        "description_owner_pn": description.attrs.get("participant_pn") if description else None,
        "description_owner_username": description.attrs.get("participant_username") if description else None,
        "description_time": _int(description.attrs.get("t")) if description else None,
        "linked_parent": group.child("linked_parent").attrs.get("jid") if group.child("linked_parent") else None,
        "restrict": group.child("locked") is not None,
        "announce": group.child("announcement") is not None,
        "is_community": group.child("parent") is not None,
        "is_community_announce": group.child("default_sub_group") is not None,
        "join_approval_mode": group.child("membership_approval_mode") is not None,
        "member_add_mode": _text(group.child("member_add_mode")) == "all_member_add",
        "ephemeral_duration": _int(ephemeral.attrs.get("expiration")) if ephemeral else None,
        "participants": participants,
    }


def parse_participating_groups(response: BinaryNode) -> list[dict[str, Any]]:
    error = response.child("error")
    if error is not None:
        code = error.attrs.get("code") or "unknown"
        message = error.attrs.get("text") or "group listing failed"
        raise WhatsAppGroupError(f"WhatsApp group listing failed ({code}): {message}")
    groups = response.child("groups")
    if groups is None:
        return []
    result: list[dict[str, Any]] = []
    for group in groups.children("group"):
        result.append(parse_group_metadata(group))
    return result


def parse_group_metadata_response(response: BinaryNode) -> dict[str, Any]:
    error = response.child("error")
    if error is not None:
        code = error.attrs.get("code") or "unknown"
        message = error.attrs.get("text") or "group metadata query failed"
        raise WhatsAppGroupError(f"WhatsApp group metadata failed ({code}): {message}")
    group = response.child("group")
    if group is None:
        raise WhatsAppGroupError("WhatsApp group metadata response has no group node")
    return parse_group_metadata(group)


__all__ = [
    "WhatsAppGroupError",
    "build_participating_groups_query",
    "build_group_metadata_query",
    "parse_group_metadata",
    "parse_participating_groups",
    "parse_group_metadata_response",
]
