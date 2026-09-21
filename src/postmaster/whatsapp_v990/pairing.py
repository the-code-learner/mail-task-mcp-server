from __future__ import annotations

import base64
from dataclasses import dataclass


PAIRING_QR_PREFIX = "https://wa.me/settings/linked_devices#"


class PairingError(ValueError):
    pass


_BROWSER_TYPES = {
    "chrome": 1,
    "edge": 2,
    "firefox": 3,
    "ie": 4,
    "opera": 5,
    "safari": 6,
}


def companion_web_client_type(os_name: str, browser_name: str) -> int:
    os_name = str(os_name or "").strip().lower()
    browser_name = str(browser_name or "").strip()
    if browser_name.lower() == "desktop":
        return 8 if os_name == "windows" else 7
    return _BROWSER_TYPES.get(browser_name.lower(), 9)


def _b64(raw: bytes) -> str:
    return base64.b64encode(bytes(raw)).decode("ascii")


def build_pairing_qr_data(
    ref: str,
    noise_public_key: bytes,
    identity_public_key: bytes,
    adv_secret_key: bytes | str,
    *,
    os_name: str = "Linux",
    browser_name: str = "Chrome",
) -> str:
    ref = str(ref or "").strip()
    if not ref:
        raise PairingError("Pairing reference is required")
    if len(noise_public_key) != 32 or len(identity_public_key) != 32:
        raise PairingError("Pairing public keys must be 32 bytes")
    adv = adv_secret_key if isinstance(adv_secret_key, str) else _b64(adv_secret_key)
    if not str(adv).strip():
        raise PairingError("ADV secret is required for companion QR")
    fields = [
        ref,
        _b64(noise_public_key),
        _b64(identity_public_key),
        str(adv),
        str(companion_web_client_type(os_name, browser_name)),
    ]
    return PAIRING_QR_PREFIX + ",".join(fields)


@dataclass(frozen=True, slots=True)
class PairingQR:
    payload: str
    expires_at: str | None = None

    def public_status(self) -> dict[str, object]:
        # Payload is intentionally omitted from generic status; it belongs only to the explicit
        # pairing response/WebGUI view and is persisted encrypted when cached.
        return {"available": bool(self.payload), "expires_at": self.expires_at, "payload_exposed_in_status": False}


__all__ = [
    "PAIRING_QR_PREFIX", "PairingError", "PairingQR", "companion_web_client_type", "build_pairing_qr_data",
]
