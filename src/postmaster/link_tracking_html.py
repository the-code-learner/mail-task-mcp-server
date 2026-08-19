from __future__ import annotations

import re
from html import escape, unescape
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlsplit, urlunsplit

_HREF_RE = re.compile(r"(?is)\bhref\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s>]+))")


def _href_value(raw_tag: str):
    match = _HREF_RE.search(raw_tag or "")
    if not match:
        return None, None
    for group in (1, 2, 3):
        value = match.group(group)
        if value is not None:
            return unescape(value).strip(), match
    return None, None


def replace_href(raw_tag: str, new_url: str) -> str:
    _, match = _href_value(raw_tag)
    if not match:
        return raw_tag
    for group in (1, 2, 3):
        if match.group(group) is not None:
            start, end = match.span(group)
            return raw_tag[:start] + escape(new_url, quote=True) + raw_tag[end:]
    return raw_tag


def eligible_web_url(url: str) -> bool:
    try:
        parts = urlsplit((url or "").strip())
    except ValueError:
        return False
    return parts.scheme.lower() in {"http", "https"} and bool(parts.netloc)


def already_tracked_url(url: str, public_base: str) -> bool:
    try:
        target, base = urlsplit(url), urlsplit(public_base)
    except ValueError:
        return False
    return (
        target.scheme.lower() == base.scheme.lower()
        and target.netloc.lower() == base.netloc.lower()
        and target.path.startswith("/t/c/")
    )


def normalized_url(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, parts.query, parts.fragment))


class _AnchorCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.anchors: list[dict[str, Any]] = []
        self._active: list[int] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        raw = self.get_starttag_text() or ""
        if tag.casefold() == "a":
            href, _ = _href_value(raw)
            self.anchors.append({"raw_tag": raw, "href": href or "", "text": []})
            self._active.append(len(self.anchors) - 1)
        elif tag.casefold() == "img" and self._active:
            alt = next((str(v or "") for k, v in attrs if str(k).casefold() == "alt"), "")
            if alt:
                self.anchors[self._active[-1]]["text"].append(alt)

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "a" and self._active:
            self._active.pop()

    def handle_data(self, data: str) -> None:
        if self._active:
            self.anchors[self._active[-1]]["text"].append(data)

    def handle_entityref(self, name: str) -> None:
        if self._active:
            self.anchors[self._active[-1]]["text"].append(unescape(f"&{name};"))

    def handle_charref(self, name: str) -> None:
        if self._active:
            self.anchors[self._active[-1]]["text"].append(unescape(f"&#{name};"))


def collect_anchors(html: str) -> list[dict[str, Any]]:
    parser = _AnchorCollector()
    try:
        parser.feed(html or "")
        parser.close()
    except Exception:
        return []
    for anchor in parser.anchors:
        anchor["anchor_text"] = re.sub(r"\s+", " ", "".join(anchor.pop("text", []))).strip()
    return parser.anchors


def rewrite_anchor_tags(html: str, replacements: list[tuple[str, str]]) -> str:
    cursor = 0
    pieces: list[str] = []
    for old_tag, new_tag in replacements:
        pos = html.find(old_tag, cursor)
        if pos < 0:
            continue
        pieces.extend((html[cursor:pos], new_tag))
        cursor = pos + len(old_tag)
    if not pieces:
        return html
    pieces.append(html[cursor:])
    return "".join(pieces)
