"""GitHub release channel policy shared by runtime, Settings and startup.

Stable: vMAJOR.MINOR.PATCH with GitHub prerelease=false.
Beta:   vMAJOR.MINOR.PATCH-beta.NUMBER with GitHub prerelease=true.

Drafts and inconsistent flags are never eligible. A stable release always outranks
its beta candidates; beta of a higher base version outranks an older stable.
"""
from __future__ import annotations

import re
from typing import Any

STABLE="stable"
STABLE_AND_BETA="stable_beta"
CHANNELS=frozenset({STABLE,STABLE_AND_BETA})
_TAG_RE=re.compile(
    r"^v?(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-beta\.([1-9][0-9]*))?$"
)


def valid_channel(value: str | None) -> str:
    channel=str(value or STABLE).strip().lower()
    if channel not in CHANNELS:
        raise ValueError("release channel must be stable or stable_beta")
    return channel


def parse_release_tag(value: str | None) -> tuple[tuple[int,int,int,int,int], str] | None:
    match=_TAG_RE.fullmatch(str(value or "").strip())
    if not match:
        return None
    major,minor,patch,beta=match.groups()
    version=(int(major),int(minor),int(patch))
    if beta is None:
        return (*version,1,0),STABLE
    return (*version,0,int(beta)), "beta"


def canonical_tag(value: str | None) -> str:
    parsed=parse_release_tag(value)
    if not parsed:
        raise ValueError("release version must be vX.Y.Z or vX.Y.Z-beta.N")
    order,kind=parsed
    suffix=f"-beta.{order[4]}" if kind=="beta" else ""
    return f"v{order[0]}.{order[1]}.{order[2]}{suffix}"


def release_tags(payload: Any, *, channel: str=STABLE) -> list[str]:
    channel=valid_channel(channel)
    matches:dict[tuple[int,int,int,int,int],str]={}
    for row in payload if isinstance(payload,list) else []:
        if not isinstance(row,dict) or row.get("draft"):
            continue
        parsed=parse_release_tag(row.get("tag_name"))
        if not parsed:
            continue
        key,kind=parsed
        # Refuse accidentally misclassified or still-unpublished releases.
        is_pre=row.get("prerelease") is True
        if (kind=="beta") != is_pre:
            continue
        if kind=="beta" and channel==STABLE:
            continue
        matches[key]=canonical_tag(str(row["tag_name"]))
    return [matches[key] for key in sorted(matches,reverse=True)]


def release_is_allowed(tag: str, channel: str) -> bool:
    parsed=parse_release_tag(tag)
    if parsed is None:
        return False
    return valid_channel(channel)==STABLE_AND_BETA or parsed[1]==STABLE


def newer_than(candidate: str | None, current: str | None) -> bool | None:
    a=parse_release_tag(candidate)
    b=parse_release_tag(current)
    if a is None or b is None:
        return None
    return a[0]>b[0]


# The v9.8.5 Portainer YAML accepts only numeric vX.Y.Z selectors.
# Each beta is additionally published at this deterministic, reserved numeric
# Git tag pointing to the EXACT SAME commit as its public -beta.N release.
# No deployment-YAML edit, source-cache mutation or privileged Docker API is needed.
_TRANSPORT_MAJOR = 999999999
_TRANSPORT_PART = 1000000


def bootstrap_transport_tag(value: str) -> str:
    """Return the numeric Git tag understood by the unmodified bootstrap."""
    tag = canonical_tag(value)
    parsed = parse_release_tag(tag)
    assert parsed is not None
    key, kind = parsed
    if kind != "beta":
        return tag
    major, minor, patch, _, ordinal = key
    if patch >= _TRANSPORT_PART or ordinal >= _TRANSPORT_PART:
        raise ValueError("beta patch and ordinal must be below one million")
    packed = (minor * _TRANSPORT_PART + patch) * _TRANSPORT_PART + ordinal
    return f"v{_TRANSPORT_MAJOR}.{major}.{packed}"
