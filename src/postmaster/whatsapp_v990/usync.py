from __future__ import annotations

"""Minimal WhatsApp USync device/LID discovery for message fanout."""

from dataclasses import dataclass
import secrets
from typing import Iterable

from .binary import BinaryNode
from .jid import JID, parse_jid


class USyncError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class DeviceTarget:
    jid: str
    device: int
    key_index: int | None
    hosted: bool
    source_jid: str
    lid: str | None = None


def build_device_query(
    jids: Iterable[str],
    *,
    stanza_id: str | None = None,
    sid: str | None = None,
    context: str = "message",
) -> BinaryNode:
    users: list[BinaryNode] = []
    seen: set[str] = set()
    for value in jids:
        parsed = parse_jid(str(value)).normalized_user()
        bare = parsed.bare
        if bare in seen:
            continue
        seen.add(bare)
        users.append(BinaryNode("user", {"jid": bare}))
    if not users:
        raise USyncError("USync device query requires at least one JID")
    attrs = {"to": "s.whatsapp.net", "type": "get", "xmlns": "usync"}
    if stanza_id:
        attrs["id"] = stanza_id
    return BinaryNode(
        "iq",
        attrs,
        [
            BinaryNode(
                "usync",
                {
                    "context": str(context),
                    "mode": "query",
                    "sid": sid or ("pm-" + secrets.token_hex(8)),
                    "last": "true",
                    "index": "0",
                },
                [
                    BinaryNode("query", {}, [BinaryNode("devices", {"version": "2"}), BinaryNode("lid")]),
                    BinaryNode("list", {}, users),
                ],
            )
        ],
    )


def _server_for(base: JID, hosted: bool) -> str:
    if base.is_lid:
        return "hosted.lid" if hosted else "lid"
    return "hosted" if hosted else "s.whatsapp.net"


def parse_device_result(
    response: BinaryNode,
    *,
    own_jid: str | None = None,
    ignore_zero_devices: bool = False,
    prefer_lid: bool = True,
) -> list[DeviceTarget]:
    if response.attrs.get("type") != "result":
        raise USyncError("USync response is not a result")
    usync = response.child("usync")
    listing = usync.child("list") if usync is not None else None
    if listing is None:
        raise USyncError("USync result has no list")
    own = parse_jid(own_jid) if own_jid else None
    targets: list[DeviceTarget] = []
    seen: set[str] = set()
    for user in listing.children("user"):
        source = str(user.attrs.get("jid") or "").strip()
        if not source:
            continue
        base = parse_jid(source).normalized_user()
        lid_node = user.child("lid")
        lid_value = str(lid_node.attrs.get("val") or "").strip() if lid_node else ""
        lid_base: JID | None = None
        if lid_value:
            try:
                lid_base = parse_jid(lid_value).normalized_user()
            except ValueError:
                lid_base = None
        devices = user.child("devices")
        device_list = devices.child("device-list") if devices is not None else None
        if device_list is None:
            continue
        for node in device_list.children("device"):
            try:
                device = int(node.attrs.get("id", ""))
            except ValueError:
                continue
            if not 0 <= device <= 255:
                continue
            if ignore_zero_devices and device == 0:
                continue
            key_index: int | None
            try:
                key_index = int(node.attrs["key-index"]) if node.attrs.get("key-index") not in (None, "") else None
            except ValueError:
                key_index = None
            if device != 0 and key_index is None:
                continue
            hosted = str(node.attrs.get("is_hosted") or "").lower() == "true"
            chosen = lid_base if prefer_lid and lid_base is not None else base
            server = _server_for(chosen, hosted)
            target = JID(chosen.user, server, device=device, domain_type=chosen.domain_type)
            if own is not None and target.user == own.user and int(target.device or 0) == int(own.device or 0):
                continue
            text = str(target)
            if text in seen:
                continue
            seen.add(text)
            targets.append(
                DeviceTarget(
                    jid=text,
                    device=device,
                    key_index=key_index,
                    hosted=hosted,
                    source_jid=base.bare,
                    lid=lid_base.bare if lid_base is not None else None,
                )
            )
    return targets


__all__ = ["USyncError", "DeviceTarget", "build_device_query", "parse_device_result"]
