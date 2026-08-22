from __future__ import annotations

import re
from html import unescape
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlparse

import bleach

from .inbound_inspection_rules import URL_TEXT_RE, unique_strings
from .inbound_inspection_urls import inspect_url

_CSS_URL_RE = re.compile(r'''(?is)url\(\s*(['"]?)(.*?)\1\s*\)''')
_DIM_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(?:px)?\s*$", re.I)

_SAFE_TAGS = [
    "a", "abbr", "b", "blockquote", "br", "code", "div", "em", "h1", "h2",
    "h3", "h4", "h5", "h6", "hr", "i", "li", "ol", "p", "pre", "span",
    "strong", "table", "tbody", "td", "th", "thead", "tr", "u", "ul",
]
_SAFE_ATTRS = {
    "a": ["href", "title"],
    "abbr": ["title"],
    "td": ["colspan", "rowspan"],
    "th": ["colspan", "rowspan"],
}


def _remote(value: str) -> bool:
    return urlparse(unescape(value or "").strip()).scheme.casefold() in {"http", "https"}


def _embedded(value: str) -> bool:
    return urlparse(unescape(value or "").strip()).scheme.casefold() in {"cid", "data"}


def _small_dimension(value: str | None) -> bool:
    if not value:
        return False
    match = _DIM_RE.match(value)
    return bool(match and float(match.group(1)) <= 2.0)


def _hidden_style(style: str) -> bool:
    compact = re.sub(r"\s+", "", (style or "").casefold())
    return any(
        marker in compact
        for marker in (
            "display:none", "visibility:hidden", "opacity:0", "height:0",
            "width:0", "font-size:0", "max-height:0",
        )
    )


class _Inspector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.remote_images: list[dict[str, Any]] = []
        self.tracking_pixels: list[dict[str, Any]] = []
        self.remote_css: list[str] = []
        self.remote_backgrounds: list[str] = []
        self.embedded_resources: list[dict[str, str]] = []
        self.links: list[dict[str, Any]] = []
        self._anchors: list[dict[str, Any]] = []
        self._style_depth = 0
        self._style_parts: list[str] = []

    def _inspect_css(self, css: str, *, origin: str) -> None:
        for _, value in _CSS_URL_RE.findall(css or ""):
            value = unescape(value).strip()
            if _remote(value):
                self.remote_backgrounds.append(value)
            elif _embedded(value):
                self.embedded_resources.append({"kind": f"css:{origin}", "value": value[:500]})

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.casefold()
        data = {str(k).casefold(): str(v or "") for k, v in attrs}
        style = data.get("style", "")
        if style:
            self._inspect_css(style, origin="style-attribute")
        background = data.get("background", "")
        if background:
            if _remote(background):
                self.remote_backgrounds.append(background)
            elif _embedded(background):
                self.embedded_resources.append({"kind": "background", "value": background[:500]})

        if tag == "style":
            self._style_depth += 1
        elif tag == "link":
            rel = data.get("rel", "").casefold()
            href = data.get("href", "")
            if "stylesheet" in rel and _remote(href):
                self.remote_css.append(href)
        elif tag == "img":
            src = data.get("src", "")
            record = {
                "url": src,
                "width": data.get("width", ""),
                "height": data.get("height", ""),
                "style": style,
                "alt": data.get("alt", ""),
            }
            if _remote(src):
                self.remote_images.append(record)
                if (
                    _small_dimension(record["width"])
                    or _small_dimension(record["height"])
                    or _hidden_style(style)
                    or "hidden" in data
                ):
                    self.tracking_pixels.append(record)
            elif _embedded(src):
                self.embedded_resources.append({"kind": "img", "value": src[:500]})
        elif tag == "a":
            href = data.get("href", "")
            self._anchors.append({"href": href, "text": []})
        else:
            for name in ("src", "href", "poster"):
                value = data.get(name, "")
                if _embedded(value):
                    self.embedded_resources.append({"kind": f"{tag}:{name}", "value": value[:500]})

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if tag == "style" and self._style_depth:
            self._style_depth -= 1
            if self._style_depth == 0:
                self._inspect_css("".join(self._style_parts), origin="style-block")
                self._style_parts.clear()
        elif tag == "a" and self._anchors:
            anchor = self._anchors.pop()
            href = str(anchor.get("href") or "").strip()
            text = "".join(anchor.get("text") or []).strip()
            if href:
                self.links.append(inspect_url(href, visible_text=text))

    def handle_data(self, data: str) -> None:
        if self._style_depth:
            self._style_parts.append(data)
        if self._anchors:
            self._anchors[-1]["text"].append(data)


def inspect_html(html: str) -> dict[str, Any]:
    parser = _Inspector()
    try:
        parser.feed(html or "")
        parser.close()
    except Exception:
        pass
    urls = [row["original_url"] for row in parser.links if row.get("original_url")]
    return {
        "remote_images": parser.remote_images,
        "remote_image_count": len(parser.remote_images),
        "tracking_pixels": parser.tracking_pixels,
        "tracking_pixel_count": len(parser.tracking_pixels),
        "remote_css": unique_strings(parser.remote_css),
        "remote_background_images": unique_strings(parser.remote_backgrounds),
        "embedded_resources": parser.embedded_resources,
        "links": parser.links,
        "link_count": len(parser.links),
        "external_urls": unique_strings(urls + parser.remote_css + parser.remote_backgrounds + [r["url"] for r in parser.remote_images]),
    }


def sanitize_html(html: str) -> str:
    """Return display-safe HTML without automatic remote-resource elements.

    Links are retained because navigation is a user action; images, style/link tags,
    forms and active/embed elements are not allowed.
    """
    return bleach.clean(
        html or "",
        tags=_SAFE_TAGS,
        attributes=_SAFE_ATTRS,
        protocols=["http", "https", "mailto", "cid"],
        strip=True,
        strip_comments=True,
    )


def urls_from_text(text: str) -> list[dict[str, Any]]:
    return [inspect_url(match.group(0)) for match in URL_TEXT_RE.finditer(text or "")]
