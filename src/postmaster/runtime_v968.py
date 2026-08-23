from __future__ import annotations

import re
from html import escape
from typing import Any
from urllib.parse import quote, urlencode

from starlette.requests import Request
from starlette.responses import RedirectResponse

from .email_privacy_v963 import fetch_high_noise_decoys
from .mail_v960_unsubscribe import PostmasterV960NewsletterMailClient


_BLOCKED_NETWORK_CLASSIFICATIONS = {
    "normal link",
    "analytics link",
    "unsubscribe",
    "action url",
    "redirector",
}


def _install_final_outbound_normalization() -> None:
    """Make canonical detracking unavoidable at the final individualized-send boundary.

    v9.6.4 normalized the public LinkTrackingMailClient entry points. Higher mail-client layers can
    legitimately call the inherited individualized delivery method directly (automatic newsletter,
    stored-file and threaded paths), so the final composed class needs the same guard at that lower
    boundary. The canonical helper remains the single implementation of historical Postmaster
    resolution and is local/zero-network.
    """

    current = PostmasterV960NewsletterMailClient._send_individualized
    if getattr(current, "_postmaster_v968_final_normalization", False):
        return

    def _send_individualized(self: Any, *args: Any, **kwargs: Any):
        if "body_html" in kwargs:
            kwargs = dict(kwargs)
            kwargs["body_html"] = self._normalize_outbound_html(kwargs.get("body_html"))
        return current(self, *args, **kwargs)

    _send_individualized._postmaster_v968_final_normalization = True  # type: ignore[attr-defined]
    PostmasterV960NewsletterMailClient._send_individualized = _send_individualized  # type: ignore[assignment]


def _render_fetch_state(results: dict[str, dict[str, Any]]) -> tuple[str, int, int]:
    """Return a safe aggregate state without surfacing per-fetch diagnostics or secrets."""
    successes = 0
    failures = 0
    for row in results.values():
        ok = (
            int(row.get("http_status") or 0) == 200
            and row.get("body") is not None
            and not str(row.get("error_state") or "")
        )
        if ok:
            successes += 1
        else:
            failures += 1
    if failures == 0:
        return "success", successes, failures
    if successes:
        return "partial success", successes, failures
    return "failure", successes, failures


def _filtered_inventory(v963: Any, raw_inventory: dict[str, Any]) -> dict[str, Any]:
    filtered = dict(raw_inventory)
    filtered["urls"] = []
    for raw in raw_inventory.get("urls") or []:
        row = dict(raw)
        classification = str(row.get("classification") or "").casefold()
        row["passive_resource"] = (
            bool(row.get("passive_resource"))
            and bool(v963._allow_passive(row))
            and classification not in _BLOCKED_NETWORK_CLASSIFICATIONS
        )
        filtered["urls"].append(row)
    return filtered


def _install_webgui_privacy_boundary(v963: Any) -> None:
    if getattr(v963.confirm_full_html, "_postmaster_v968_privacy_boundary", False):
        return

    async def confirm_full_html(base: Any, request: Request):
        form, error = await base._verified_form(request)
        if error:
            return error
        account_id = str(form.get("account_id") or "").strip()
        mailbox = str(form.get("mailbox") or "").strip()
        uid = str(form.get("uid") or "").strip()
        if not account_id or not mailbox or not uid:
            return RedirectResponse(
                "/?ui_view=inbox&v963_result=Invalid+message#inbox",
                status_code=303,
            )

        proxy_store = base.privacy_proxy_store()
        proxy_status = proxy_store.status()
        if not proxy_status.get("enabled"):
            return RedirectResponse(
                "/?" + urlencode({
                    "ui_view": "inbox",
                    "account_id": account_id,
                    "mailbox": mailbox,
                    "message_uid": uid,
                    "v963_result": "Privacy Proxy is not active",
                }) + "#inbox",
                status_code=303,
            )

        detail = base.mailbox_cache_synchronizer().ensure_body(
            base.mail_client(account_id),
            account_id=account_id,
            mailbox=mailbox,
            uid=uid,
        )
        raw_inventory = v963.inventory_message(
            str(detail.get("body_html") or ""),
            str(detail.get("body") or ""),
        )
        filtered = _filtered_inventory(v963, raw_inventory)
        proxy_client = base.privacy_proxy_client()
        render_results = v963.fetch_passive_resources(
            filtered,
            cache=base.mailbox_cache_store(),
            proxy=proxy_client,
            account_id=account_id,
            mailbox=mailbox,
            uid=uid,
        )
        state, successes, failures = _render_fetch_state(render_results)

        noise_requests = 0
        noise_failed = False
        if proxy_status.get("high_noise_decoy_enabled"):
            try:
                noise = fetch_high_noise_decoys(
                    filtered,
                    store=proxy_store,
                    proxy=proxy_client,
                    account_id=account_id,
                    mailbox=mailbox,
                    uid=uid,
                )
                noise_requests = int(noise.get("requests") or 0)
            except Exception:
                # Full-HTML rendering remains independently diagnosable. Never copy a proxy
                # exception into the browser because it may contain sensitive implementation data.
                noise_failed = True

        result_text = (
            f"Full HTML fetch {state} · cached: {successes} · failed: {failures}"
        )
        if proxy_status.get("high_noise_decoy_enabled"):
            result_text += (
                " · high-noise decoys unavailable"
                if noise_failed
                else f" · high-noise decoys: {noise_requests}"
            )

        params = {
            "ui_view": "inbox",
            "account_id": account_id,
            "mailbox": mailbox,
            "message_uid": uid,
            "v963_result": result_text,
        }
        # If every genuine render fetch failed, remain in Safe Email instead of falsely presenting
        # the Full HTML state. Partial success renders only the successfully cached resources.
        if state != "failure":
            params["full_html"] = "1"
        return RedirectResponse("/?" + urlencode(params) + "#inbox", status_code=303)

    confirm_full_html._postmaster_v968_privacy_boundary = True  # type: ignore[attr-defined]
    v963.confirm_full_html = confirm_full_html


def _install_webgui_seen_boundary(v963: Any) -> None:
    current_detail = v963._detail
    if not getattr(current_detail, "_postmaster_v968_seen_boundary", False):

        def _detail(
            base: Any,
            params: dict[str, str],
            account_id: str,
            mailbox: str,
            role: str,
            uid: str,
            request: Request,
        ) -> str:
            rendered = current_detail(base, params, account_id, mailbox, role, uid, request)
            if role != "received":
                return rendered

            store = base.mailbox_cache_store()
            cached = store.get_message(account_id, mailbox, uid, include_body=False)
            if cached and not cached.get("seen"):
                # Only mark Seen after the detail has loaded successfully. If IMAP STORE fails the
                # exception propagates and the WebGUI reports that the detail could not be opened.
                base.mail_client(account_id).set_seen(mailbox, uid, True)
                flags = [str(value) for value in cached.get("flags") or []]
                if not any(value.casefold() == r"\seen" for value in flags):
                    flags.append(r"\Seen")
                store.update_flags(account_id, mailbox, {int(uid): flags})
            return rendered

        _detail._postmaster_v968_seen_boundary = True  # type: ignore[attr-defined]
        v963._detail = _detail

    current_renderer = v963.render_inbox_v963
    if getattr(current_renderer, "_postmaster_v968_seen_renderer", False):
        return

    def render_inbox_v963(base: Any, request: Request) -> str:
        rendered = current_renderer(base, request)
        uid = str(request.query_params.get("message_uid") or "").strip()
        account_id = str(request.query_params.get("account_id") or "").strip()
        mailbox = str(request.query_params.get("mailbox") or "").strip()
        if not uid or not account_id or not mailbox:
            return rendered
        cached = base.mailbox_cache_store().get_message(
            account_id,
            mailbox,
            uid,
            include_body=False,
        )
        if not cached or not cached.get("seen"):
            return rendered

        uid_marker = "message_uid=" + quote(uid, safe="")
        row_pattern = re.compile(
            r'<tr class="v963-mail-row unread" data-v960-href="[^"]*">.*?</tr>',
            re.DOTALL,
        )
        for match in row_pattern.finditer(rendered):
            row = match.group(0)
            if uid_marker not in row:
                continue
            cleaned = row.replace(
                'class="v963-mail-row unread"',
                'class="v963-mail-row"',
                1,
            ).replace(
                '<span class="v963-unread-dot" title="Unread"></span>',
                "",
                1,
            )
            return rendered[: match.start()] + cleaned + rendered[match.end() :]
        return rendered

    render_inbox_v963._postmaster_v968_seen_renderer = True  # type: ignore[attr-defined]
    v963.render_inbox_v963 = render_inbox_v963


def install_runtime_v968(base: Any, core: Any, webgui_v963: Any | None = None) -> None:
    """v9.6.8 final-composition privacy hardening without MCP surface changes."""
    _install_final_outbound_normalization()
    if webgui_v963 is not None:
        _install_webgui_privacy_boundary(webgui_v963)
        _install_webgui_seen_boundary(webgui_v963)


__all__ = ["install_runtime_v968"]
