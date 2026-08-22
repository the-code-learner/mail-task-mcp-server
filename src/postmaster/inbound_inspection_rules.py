from __future__ import annotations

import re
from typing import Iterable

REMOTE_SCHEMES = {"http", "https"}
EMBEDDED_SCHEMES = {"cid", "data"}

TRACKING_PARAMETERS = {
    "fbclid", "gclid", "dclid", "msclkid", "mc_cid", "mc_eid", "mkt_tok",
    "vero_id", "_hsenc", "_hsmi", "oly_anon_id", "oly_enc_id", "rb_clickid",
    "ef_id", "wickedid", "s_cid", "vero_conv", "vero_id",
}

REDIRECT_PARAMETER_NAMES = (
    "url", "u", "target", "dest", "destination", "redirect", "redirect_url",
    "redirect_uri", "r", "to", "link",
)

TRACKER_HOST_HINTS = (
    "track", "tracking", "click", "clicks", "redirect", "redir", "links",
    "email", "mail", "open", "pixel", "beacon", "analytics",
)

TRACKER_PATH_HINTS = (
    "/track", "/tracking", "/click", "/redirect", "/redir", "/open",
    "/pixel", "/beacon", "/r/", "/l/",
)

URL_TEXT_RE = re.compile(
    r"(?i)\b(?:https?://|www\.)[^\s<>'\"\]\)]+"
)

DOMAIN_TEXT_RE = re.compile(
    r"(?i)(?<![@\w.-])(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}(?![\w.-])"
)


def is_tracking_parameter(name: str) -> bool:
    key = (name or "").strip().casefold()
    return key.startswith("utm_") or key in TRACKING_PARAMETERS


def tracker_host_hint(host: str) -> bool:
    value = (host or "").casefold()
    return any(hint in value for hint in TRACKER_HOST_HINTS)


def tracker_path_hint(path: str) -> bool:
    value = (path or "").casefold()
    return any(hint in value for hint in TRACKER_PATH_HINTS)


def unique_strings(values: Iterable[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in values:
        value = str(raw or "").strip()
        if not value:
            continue
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(value)
    return out
