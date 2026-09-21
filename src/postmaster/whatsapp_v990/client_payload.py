from __future__ import annotations

"""Minimal clean-room protobuf builders for WhatsApp Web ClientPayload.

Field numbers/enums are pinned from the current public WhatsApp Web proto schema. The builders
cover only the registration/login subset Postmaster needs and deliberately preserve the generic
protobuf implementation in proto.py instead of importing generated third-party code.
"""

from dataclasses import dataclass
import hashlib
from typing import Iterable

from .proto import field_bytes, field_message, field_varint
from .signal_keys import SignedPreKey

CURRENT_WA_WEB_VERSION = (2, 3000, 1048032155)
DEVICE_PROPS_VERSION = (10, 15, 7)

# ClientPayload.UserAgent enums.
USER_AGENT_PLATFORM_WEB = 14
USER_AGENT_RELEASE_CHANNEL_RELEASE = 0
WEB_SUBPLATFORM_WEB_BROWSER = 0

# ClientPayload enums.
CONNECT_TYPE_WIFI_UNKNOWN = 1
CONNECT_REASON_USER_ACTIVATED = 1

# DeviceProps enums.
DEVICE_PLATFORM_CHROME = 1

# Signal/WhatsApp public-key bundle type.
KEY_BUNDLE_TYPE = b"\x05"


class ClientPayloadError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class RegistrationKeys:
    registration_id: int
    identity_public_key: bytes
    signed_pre_key: SignedPreKey


def parse_web_version(value: str | Iterable[int] | None) -> tuple[int, int, int]:
    if value is None:
        return CURRENT_WA_WEB_VERSION
    if isinstance(value, str):
        clean = value.strip().split("-", 1)[0]
        parts = clean.split(".")
    else:
        parts = [str(x) for x in value]
    if len(parts) != 3:
        raise ClientPayloadError("WhatsApp Web version must contain exactly three numeric components")
    try:
        parsed = tuple(int(x) for x in parts)
    except ValueError as exc:
        raise ClientPayloadError("WhatsApp Web version components must be numeric") from exc
    if any(x < 0 or x > 0xFFFFFFFF for x in parsed):
        raise ClientPayloadError("WhatsApp Web version component is outside uint32 range")
    return parsed  # type: ignore[return-value]


def encode_big_endian(value: int, width: int | None = None) -> bytes:
    value = int(value)
    if value < 0:
        raise ClientPayloadError("Big-endian value must be non-negative")
    if width is None:
        width = max(1, (value.bit_length() + 7) // 8)
    if width <= 0 or value >= (1 << (8 * width)):
        raise ClientPayloadError("Big-endian value does not fit requested width")
    return value.to_bytes(width, "big")


def encode_app_version(version: tuple[int, int, int]) -> bytes:
    primary, secondary, tertiary = version
    return b"".join((field_varint(1, primary), field_varint(2, secondary), field_varint(3, tertiary)))


def encode_user_agent(
    version: tuple[int, int, int],
    *,
    locale_language: str = "en",
    locale_country: str = "US",
    device: str = "Desktop",
    os_version: str = "0.1",
    os_build_number: str = "0.1",
) -> bytes:
    return b"".join(
        (
            field_varint(1, USER_AGENT_PLATFORM_WEB),
            field_message(2, [encode_app_version(version)]),
            field_bytes(3, "000"),
            field_bytes(4, "000"),
            field_bytes(5, os_version),
            field_bytes(7, device),
            field_bytes(8, os_build_number),
            field_varint(10, USER_AGENT_RELEASE_CHANNEL_RELEASE),
            field_bytes(11, locale_language),
            field_bytes(12, locale_country),
        )
    )


def encode_web_info() -> bytes:
    return field_varint(4, WEB_SUBPLATFORM_WEB_BROWSER)


def encode_history_sync_config() -> bytes:
    # Mirrors the currently published desktop registration capability subset. Optional fields
    # intentionally stay omitted rather than being serialized with guessed/default values.
    return b"".join(
        (
            field_varint(3, 10240),  # storageQuotaMb
            field_varint(4, 1),      # inlineInitialPayloadInE2EeMsg
            field_varint(6, 0),      # supportCallLogHistory
            field_varint(7, 1),      # supportBotUserAgentChatHistory
            field_varint(8, 1),      # supportCagReactionsAndPolls
            field_varint(9, 1),      # supportBizHostedMsg
            field_varint(10, 1),     # supportRecentSyncChunkMessageCountTuning
            field_varint(11, 1),     # supportHostedGroupMsg
            field_varint(12, 1),     # supportFbidBotChatHistory
            field_varint(14, 1),     # supportMessageAssociation
            field_varint(15, 0),     # supportGroupHistory
        )
    )


def encode_device_props(
    *,
    os_name: str = "Linux",
    platform_type: int = DEVICE_PLATFORM_CHROME,
    require_full_sync: bool = False,
) -> bytes:
    return b"".join(
        (
            field_bytes(1, os_name),
            field_message(2, [encode_app_version(DEVICE_PROPS_VERSION)]),
            field_varint(3, int(platform_type)),
            field_varint(4, 1 if require_full_sync else 0),
            field_message(5, [encode_history_sync_config()]),
        )
    )


def build_registration_payload(
    keys: RegistrationKeys,
    *,
    version: tuple[int, int, int] | str | None = None,
    os_name: str = "Linux",
    locale_country: str = "US",
    require_full_sync: bool = False,
) -> bytes:
    resolved = parse_web_version(version)
    identity = bytes(keys.identity_public_key)
    signed_pair = keys.signed_pre_key.key_pair
    if len(identity) != 32 or len(signed_pair.public) != 32:
        raise ClientPayloadError("WhatsApp registration identity/pre-key public keys must be 32 bytes")
    if len(keys.signed_pre_key.signature) != 64:
        raise ClientPayloadError("WhatsApp signed-pre-key signature must be 64 bytes")
    if not 0 <= int(keys.registration_id) <= 0x3FFF:
        raise ClientPayloadError("WhatsApp registration id must fit 14 bits")

    version_text = ".".join(str(x) for x in resolved).encode("ascii")
    build_hash = hashlib.md5(version_text, usedforsecurity=False).digest()
    pairing = b"".join(
        (
            field_bytes(1, encode_big_endian(keys.registration_id, 4)),
            field_bytes(2, KEY_BUNDLE_TYPE),
            field_bytes(3, identity),
            field_bytes(4, encode_big_endian(keys.signed_pre_key.key_id, 3)),
            field_bytes(5, signed_pair.public),
            field_bytes(6, keys.signed_pre_key.signature),
            field_bytes(7, build_hash),
            field_bytes(
                8,
                encode_device_props(
                    os_name=os_name,
                    platform_type=DEVICE_PLATFORM_CHROME,
                    require_full_sync=require_full_sync,
                ),
            ),
        )
    )
    return b"".join(
        (
            field_varint(3, 0),  # passive=false
            field_message(5, [encode_user_agent(resolved, locale_country=locale_country)]),
            field_message(6, [encode_web_info()]),
            field_varint(12, CONNECT_TYPE_WIFI_UNKNOWN),
            field_varint(13, CONNECT_REASON_USER_ACTIVATED),
            field_message(19, [pairing]),
            field_varint(33, 0),  # pull=false
        )
    )


def build_login_payload(
    *,
    username: int,
    device: int,
    version: tuple[int, int, int] | str | None = None,
    locale_country: str = "US",
) -> bytes:
    resolved = parse_web_version(version)
    if username < 0 or device < 0:
        raise ClientPayloadError("WhatsApp login username/device must be non-negative")
    return b"".join(
        (
            field_varint(1, int(username)),
            field_varint(3, 1),  # passive=true
            field_message(5, [encode_user_agent(resolved, locale_country=locale_country)]),
            field_message(6, [encode_web_info()]),
            field_varint(12, CONNECT_TYPE_WIFI_UNKNOWN),
            field_varint(13, CONNECT_REASON_USER_ACTIVATED),
            field_varint(18, int(device)),
            field_varint(33, 1),  # pull=true
            field_varint(41, 0),  # lidDbMigrated=false
        )
    )


__all__ = [
    "CURRENT_WA_WEB_VERSION",
    "DEVICE_PROPS_VERSION",
    "RegistrationKeys",
    "ClientPayloadError",
    "parse_web_version",
    "encode_big_endian",
    "encode_app_version",
    "encode_user_agent",
    "encode_web_info",
    "encode_history_sync_config",
    "encode_device_props",
    "build_registration_payload",
    "build_login_payload",
]
