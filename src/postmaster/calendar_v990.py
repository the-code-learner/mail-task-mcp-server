from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email.message import Message
from email.policy import default
from email.parser import BytesParser
from typing import Any, Iterable
from urllib.parse import urlparse

_CALENDAR_TYPES = {
    "text/calendar",
    "application/calendar",
    "application/ics",
    "application/icalendar",
    "text/x-vcalendar",
}
_VIDEO_HOSTS = {
    "meet.google.com",
    "teams.microsoft.com",
    "teams.live.com",
    "zoom.us",
    "www.zoom.us",
    "webex.com",
    "www.webex.com",
    "whereby.com",
    "jitsi.org",
    "meet.jit.si",
}
_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.I)


class CalendarError(ValueError):
    pass


def _unfold_ical(text: str) -> list[str]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    out: list[str] = []
    for line in normalized.split("\n"):
        if line.startswith((" ", "\t")) and out:
            out[-1] += line[1:]
        else:
            out.append(line)
    return out


def _split_property(line: str) -> tuple[str, dict[str, str], str] | None:
    if ":" not in line:
        return None
    left, value = line.split(":", 1)
    bits = left.split(";")
    name = bits[0].strip().upper()
    if not name:
        return None
    params: dict[str, str] = {}
    for bit in bits[1:]:
        if "=" in bit:
            k, v = bit.split("=", 1)
            params[k.strip().upper()] = v.strip().strip('"')
    return name, params, value.strip()


def _ical_unescape(value: str) -> str:
    return (
        value.replace("\\N", "\n")
        .replace("\\n", "\n")
        .replace("\\,", ",")
        .replace("\\;", ";")
        .replace("\\\\", "\\")
    )


def _ical_escape(value: str) -> str:
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\r", "\\n")
        .replace("\n", "\\n")
    )


def _parse_dt(value: str, params: dict[str, str]) -> dict[str, Any]:
    raw = value.strip()
    timezone = params.get("TZID") or ""
    date_only = params.get("VALUE", "").upper() == "DATE" or bool(re.fullmatch(r"\d{8}", raw))
    dt: datetime | None = None
    try:
        if date_only:
            dt = datetime.strptime(raw[:8], "%Y%m%d").replace(tzinfo=UTC)
        elif raw.endswith("Z"):
            dt = datetime.strptime(raw, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
        elif re.fullmatch(r"\d{8}T\d{6}", raw):
            # A TZID value is kept explicitly instead of pretending the local wall time is UTC.
            dt = datetime.strptime(raw, "%Y%m%dT%H%M%S")
    except ValueError:
        dt = None
    return {
        "raw": raw,
        "timezone": timezone or None,
        "date_only": date_only,
        "iso": dt.isoformat() if dt else None,
    }


def _address(value: str) -> str:
    v = value.strip()
    if v.lower().startswith("mailto:"):
        return v[7:]
    return v


def _urls(values: Iterable[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        for match in _URL_RE.findall(value or ""):
            url = match.rstrip(".,);]>")
            if url not in seen:
                seen.add(url)
                out.append(url)
    return out


def _video_urls(urls: Iterable[str]) -> list[str]:
    result: list[str] = []
    for url in urls:
        try:
            host = (urlparse(url).hostname or "").lower()
        except ValueError:
            continue
        if host in _VIDEO_HOSTS or any(host.endswith("." + item) for item in _VIDEO_HOSTS):
            result.append(url)
            continue
        if any(token in host for token in ("zoom", "meet", "teams", "webex", "whereby", "jitsi")):
            result.append(url)
    return result


def parse_ics(text: str) -> dict[str, Any]:
    """Parse VEVENT fields locally without resolving URLs or contacting organizers."""
    lines = _unfold_ical(text)
    method = ""
    events: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    raw_props: list[tuple[str, dict[str, str], str]] = []

    for line in lines:
        parsed = _split_property(line)
        if not parsed:
            continue
        name, params, value = parsed
        if name == "METHOD" and current is None:
            method = value.upper()
            continue
        if name == "BEGIN" and value.upper() == "VEVENT":
            current = {
                "attendees": [],
                "urls": [],
                "video_urls": [],
                "x_properties": {},
            }
            raw_props = []
            continue
        if name == "END" and value.upper() == "VEVENT" and current is not None:
            text_values = [
                str(current.get("url") or ""),
                str(current.get("location") or ""),
                str(current.get("description") or ""),
            ]
            for key, val in current.get("x_properties", {}).items():
                if any(token in key for token in ("CONFERENCE", "MEETING", "HANGOUT", "ONLINE")):
                    text_values.append(str(val))
            urls = _urls(text_values)
            current["urls"] = urls
            current["video_urls"] = _video_urls(urls)
            events.append(current)
            current = None
            raw_props = []
            continue
        if current is None:
            continue
        raw_props.append(parsed)
        clean = _ical_unescape(value)
        if name == "UID":
            current["uid"] = clean
        elif name == "SUMMARY":
            current["summary"] = clean
        elif name == "DESCRIPTION":
            current["description"] = clean
        elif name == "LOCATION":
            current["location"] = clean
        elif name == "URL":
            current["url"] = clean
        elif name == "DTSTART":
            current["start"] = _parse_dt(value, params)
        elif name == "DTEND":
            current["end"] = _parse_dt(value, params)
        elif name == "DTSTAMP":
            current["dtstamp"] = _parse_dt(value, params)
        elif name == "SEQUENCE":
            try:
                current["sequence"] = int(value)
            except ValueError:
                current["sequence"] = value
        elif name == "STATUS":
            current["status"] = value.upper()
        elif name == "ORGANIZER":
            current["organizer"] = {
                "address": _address(clean),
                "name": params.get("CN") or None,
            }
        elif name == "ATTENDEE":
            current["attendees"].append(
                {
                    "address": _address(clean),
                    "name": params.get("CN") or None,
                    "role": params.get("ROLE") or None,
                    "partstat": params.get("PARTSTAT") or None,
                    "rsvp": params.get("RSVP") or None,
                }
            )
        elif name.startswith("X-"):
            current["x_properties"][name] = clean

    invitation = bool(events) and method in {"REQUEST", "PUBLISH", "ADD", "CANCEL", "REPLY", "COUNTER", "DECLINECOUNTER"}
    return {
        "ok": True,
        "method": method or None,
        "is_calendar": bool(events),
        "is_invitation": invitation,
        "events": events,
        "network_access": False,
        "external_actions": False,
    }


def _part_filename(part: Message) -> str:
    try:
        return str(part.get_filename() or "")
    except Exception:
        return ""


def detect_calendar_parts(message_or_bytes: Message | bytes) -> dict[str, Any]:
    """Inspect headers and MIME parts. This function is deliberately zero-network/read-only."""
    message = (
        BytesParser(policy=default).parsebytes(message_or_bytes)
        if isinstance(message_or_bytes, (bytes, bytearray))
        else message_or_bytes
    )
    hints: list[str] = []
    headers = {str(k).lower(): str(v) for k, v in message.items()}
    for name in ("content-class", "x-microsoft-cdo-message-class", "x-ms-has-attach"):
        value = headers.get(name, "")
        if "calend" in value.lower() or "appointment" in value.lower():
            hints.append(f"header:{name}")

    parts: list[dict[str, Any]] = []
    walk = list(message.walk()) if message.is_multipart() else [message]
    for index, part in enumerate(walk):
        content_type = str(part.get_content_type() or "").lower()
        filename = _part_filename(part)
        calendar_like = content_type in _CALENDAR_TYPES or filename.lower().endswith((".ics", ".ifb", ".vcs"))
        if not calendar_like:
            continue
        try:
            payload = part.get_payload(decode=True)
        except Exception:
            payload = None
        if payload is None:
            try:
                content = part.get_content()
                text = content if isinstance(content, str) else str(content)
            except Exception:
                text = ""
        else:
            charset = part.get_content_charset() or "utf-8"
            try:
                text = payload.decode(charset, errors="replace")
            except LookupError:
                text = payload.decode("utf-8", errors="replace")
        parsed = parse_ics(text) if text else {"ok": True, "is_calendar": True, "is_invitation": False, "events": []}
        parts.append(
            {
                "part_index": index,
                "content_type": content_type,
                "content_disposition": str(part.get_content_disposition() or ""),
                "filename": filename or None,
                "method_parameter": str(part.get_param("method") or "").upper() or None,
                "sha256": hashlib.sha256((payload or text.encode("utf-8", "replace"))).hexdigest(),
                "calendar": parsed,
            }
        )
    return {
        "ok": True,
        "calendar_candidate": bool(parts or hints),
        "calendar_parts": parts,
        "header_hints": hints,
        "is_invitation": any(bool(p.get("calendar", {}).get("is_invitation")) for p in parts),
        "network_access": False,
        "external_actions": False,
    }


def _fold_ical_line(line: str, limit: int = 75) -> list[str]:
    # RFC 5545 fold limit is octets. Keep ASCII output where possible; UTF-8-safe fallback.
    encoded = line.encode("utf-8")
    if len(encoded) <= limit:
        return [line]
    out: list[str] = []
    current = ""
    current_len = 0
    for ch in line:
        size = len(ch.encode("utf-8"))
        if current and current_len + size > (limit if not out else limit - 1):
            out.append(current)
            current = " " + ch
            current_len = 1 + size
        else:
            current += ch
            current_len += size
    if current:
        out.append(current)
    return out


def build_calendar_request(
    *,
    uid: str,
    summary: str,
    start_utc: datetime,
    end_utc: datetime,
    organizer_email: str,
    attendees: Iterable[str],
    description: str = "",
    location: str = "",
    video_url: str | None = None,
    sequence: int = 0,
    prodid: str = "-//Postmaster MCP//v9.9//EN",
) -> str:
    if start_utc.tzinfo is None or end_utc.tzinfo is None:
        raise CalendarError("Calendar invitation start/end must be timezone-aware")
    start = start_utc.astimezone(UTC)
    end = end_utc.astimezone(UTC)
    if end <= start:
        raise CalendarError("Calendar invitation end must be after start")
    attendee_list = [str(x).strip() for x in attendees if str(x).strip()]
    if not attendee_list:
        raise CalendarError("Calendar invitation requires at least one attendee")
    if not organizer_email.strip():
        raise CalendarError("Calendar invitation requires organizer email")

    def stamp(dt: datetime) -> str:
        return dt.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")

    desc = description
    if video_url and video_url not in desc:
        desc = (desc + "\n\n" if desc else "") + f"Video call: {video_url}"
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{_ical_escape(prodid)}",
        "CALSCALE:GREGORIAN",
        "METHOD:REQUEST",
        "BEGIN:VEVENT",
        f"UID:{_ical_escape(uid)}",
        f"DTSTAMP:{stamp(datetime.now(UTC))}",
        f"DTSTART:{stamp(start)}",
        f"DTEND:{stamp(end)}",
        f"SEQUENCE:{int(sequence)}",
        "STATUS:CONFIRMED",
        f"SUMMARY:{_ical_escape(summary)}",
        f"ORGANIZER:mailto:{organizer_email.strip()}",
    ]
    for attendee in attendee_list:
        lines.append(f"ATTENDEE;ROLE=REQ-PARTICIPANT;RSVP=TRUE:mailto:{attendee}")
    if location:
        lines.append(f"LOCATION:{_ical_escape(location)}")
    if desc:
        lines.append(f"DESCRIPTION:{_ical_escape(desc)}")
    if video_url:
        lines.append(f"URL:{video_url}")
        lines.append(f"X-POSTMASTER-VIDEOCALL:{video_url}")
    lines.extend(["END:VEVENT", "END:VCALENDAR"])
    folded: list[str] = []
    for line in lines:
        folded.extend(_fold_ical_line(line))
    return "\r\n".join(folded) + "\r\n"


def job_calendar_window(job: dict[str, Any], *, default_duration_minutes: int = 30) -> tuple[datetime, datetime]:
    """Resolve explicit calendar payload first, then one-time ISO schedule metadata."""
    payload = dict(job.get("payload") or {})
    cal = dict(payload.get("calendar") or {})
    start_raw = cal.get("start") or payload.get("calendar_start")
    end_raw = cal.get("end") or payload.get("calendar_end")
    if not start_raw and str(job.get("schedule_type") or "").lower() in {"once", "at", "datetime"}:
        start_raw = job.get("schedule_value")
    if not start_raw:
        raise CalendarError("Task does not contain a concrete calendar start time")

    def parse(value: Any) -> datetime:
        text = str(value or "").strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            raise CalendarError("Calendar task times must include a timezone offset")
        return dt

    start = parse(start_raw)
    end = parse(end_raw) if end_raw else start + timedelta(minutes=int(cal.get("duration_minutes") or default_duration_minutes))
    if end <= start:
        raise CalendarError("Calendar task end must be after start")
    return start, end
