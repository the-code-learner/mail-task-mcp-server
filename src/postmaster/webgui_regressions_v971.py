from __future__ import annotations

from html.parser import HTMLParser
from typing import Any

from .email_inventory_v963 import inventory_message
from .privacy_cache_v969 import _FullHtmlRewriterV969


REGRESSION_STYLE = r'''
/* webgui-v971-shared-regression-fixes */
/* Horizontal table containment must not swallow vertical wheel/trackpad chaining. */
.scroll{overscroll-behavior-x:contain;overscroll-behavior-y:auto}
/* Grid rows may grow for one expanded item, but siblings keep their intrinsic height. */
.grid,.v951-grid{align-items:start}
'''


class _RenderedContentProbe(HTMLParser):
    _NON_VISIBLE = {"head", "style", "title"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hidden_depth = 0
        self.usable = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.casefold()
        if tag in self._NON_VISIBLE:
            self.hidden_depth += 1
            return
        if self.hidden_depth:
            return
        values = {str(name).casefold(): str(value or "") for name, value in attrs}
        if str(values.get("alt") or "").strip():
            self.usable = True
        for name in ("src", "background", "poster"):
            target = str(values.get(name) or "").strip().casefold()
            if target.startswith(("/dashboard/inbox/resource?", "data:")):
                self.usable = True

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() in self._NON_VISIBLE and self.hidden_depth:
            self.hidden_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self.hidden_depth and str(data or "").strip():
            self.usable = True


def _rewrite_usable_document(
    service: Any,
    body_html: str,
    *,
    account_id: str,
    mailbox: str,
    uid: str,
    inventory: dict[str, Any],
) -> tuple[str, bool, bool]:
    """Return a Full HTML rewrite without silently substituting Safe Email.

    The v9.6.9 rewriter intentionally strips active content and only maps already-cached
    passive resources. A resource-map failure is isolated from the document rewrite and is
    reported only as an aggregate boolean; raw exception text is never returned.
    """

    if not str(body_html or "").strip():
        return "", False, False

    resource_map_failed = False
    try:
        resources = service._resource_map(
            inventory,
            account_id=account_id,
            mailbox=mailbox,
            uid=uid,
        )
    except Exception:
        resources = {}
        resource_map_failed = True

    parser = _FullHtmlRewriterV969(resources)
    try:
        parser.feed(body_html)
        parser.close()
    except Exception:
        return "", False, resource_map_failed

    rendered = "".join(parser.parts)
    probe = _RenderedContentProbe()
    try:
        probe.feed(rendered)
        probe.close()
    except Exception:
        return "", False, resource_map_failed
    return rendered, bool(probe.usable), resource_map_failed


def _normalize_full_html_result(
    service: Any,
    result: dict[str, Any],
    *,
    body_html: str,
    inventory: dict[str, Any],
    account_id: str,
    mailbox: str,
    uid: str,
) -> dict[str, Any]:
    normalized = dict(result or {})
    diagnostics = dict(normalized.get("diagnostics") or {})
    original_state = str(normalized.get("render_state") or "failure").casefold()

    rendered, usable, resource_map_failed = _rewrite_usable_document(
        service,
        body_html,
        account_id=account_id,
        mailbox=mailbox,
        uid=uid,
        inventory=inventory,
    )

    cached_failed = int(diagnostics.get("cached_failed") or 0)
    genuine_failed = int(diagnostics.get("genuine_failed") or 0)
    nested_failed = int(diagnostics.get("nested_failed") or 0)
    nested_negative = int(diagnostics.get("nested_negative_cache_hits") or 0)
    stylesheet_failed = int(diagnostics.get("stylesheet_failures") or 0)
    # The bounded CSS layer folds attempted nested/stylesheet failures into genuine_failed.
    # Negative nested cache hits are separate, so add only those after taking the largest
    # aggregate failure view. This keeps diagnostics useful without double counting.
    isolated_failures = max(
        cached_failed,
        genuine_failed,
        nested_failed + stylesheet_failed,
    ) + nested_negative
    if resource_map_failed:
        isolated_failures += 1

    diagnostics["document_renderable"] = bool(usable)
    diagnostics["isolated_render_failures"] = max(0, isolated_failures)
    diagnostics["resource_map_available"] = not resource_map_failed
    normalized["diagnostics"] = diagnostics

    if not usable:
        normalized["ok"] = False
        normalized["render_state"] = "failure"
        normalized["full_html_available"] = False
        normalized["rendered_html"] = ""
        return normalized

    # Resource availability and document availability are separate. Even when every passive
    # resource is unavailable, a useful document can still be rendered with those references
    # omitted. That is partial success, not a reason to collapse back to Safe Email.
    if original_state == "success" and not isolated_failures:
        state = "success"
    else:
        state = "partial"
    normalized["ok"] = True
    normalized["render_state"] = state
    normalized["full_html_available"] = True
    normalized["rendered_html"] = rendered
    return normalized


def install_full_html_partial_success_v971(service: Any) -> Any:
    """Patch the shared v9.6.9 service instance used by both WebGUI and MCP."""

    if getattr(service, "_postmaster_v971_partial_success", False):
        return service

    original_fetch_inventory = service.fetch_inventory
    original_render_cached = service.render_cached_message

    def fetch_inventory(
        inventory: dict[str, Any],
        *,
        account_id: str,
        mailbox: str,
        uid: str,
        refresh: bool = False,
        body_html: str = "",
    ) -> dict[str, Any]:
        result = original_fetch_inventory(
            inventory,
            account_id=account_id,
            mailbox=mailbox,
            uid=uid,
            refresh=refresh,
            body_html=body_html,
        )
        return _normalize_full_html_result(
            service,
            result,
            body_html=body_html,
            inventory=inventory,
            account_id=account_id,
            mailbox=mailbox,
            uid=uid,
        )

    def render_cached_message(
        *,
        account_id: str,
        mailbox: str,
        uid: str,
    ) -> dict[str, Any]:
        result = original_render_cached(
            account_id=account_id,
            mailbox=mailbox,
            uid=uid,
        )
        try:
            detail = service.base.mailbox_cache_store().get_message(
                account_id, mailbox, str(uid), include_body=True
            )
        except Exception:
            return result
        if not detail or not detail.get("body_cached"):
            return result
        body_html = str(detail.get("body_html") or "")
        inventory = inventory_message(body_html, str(detail.get("body") or ""))
        return _normalize_full_html_result(
            service,
            result,
            body_html=body_html,
            inventory=inventory,
            account_id=account_id,
            mailbox=mailbox,
            uid=uid,
        )

    service.fetch_inventory = fetch_inventory
    service.render_cached_message = render_cached_message
    service._postmaster_v971_partial_success = True
    return service


def install_webgui_interaction_regressions_v971(v962: Any) -> None:
    """Append final-cascade shared interaction fixes after the v9.7.0 visual overlay."""

    current_styles = v962._styles
    if getattr(current_styles, "_postmaster_v971_shared_regressions", False):
        return

    def styles() -> str:
        value = current_styles()
        if "webgui-v971-shared-regression-fixes" in value:
            return value
        return value + "\n" + REGRESSION_STYLE

    styles._postmaster_v971_shared_regressions = True  # type: ignore[attr-defined]
    v962._styles = styles


__all__ = [
    "REGRESSION_STYLE",
    "install_full_html_partial_success_v971",
    "install_webgui_interaction_regressions_v971",
]
