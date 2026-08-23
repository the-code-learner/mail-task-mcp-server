from __future__ import annotations

from html import escape
from typing import Any
from urllib.parse import urlencode

from starlette.requests import Request
from starlette.responses import RedirectResponse

from .email_privacy_v963 import fetch_high_noise_decoys


_BLOCKED_NETWORK_CLASSIFICATIONS = {"normal link", "analytics link", "unsubscribe", "action url", "redirector"}


def install_webgui_v963_high_noise(v963: Any) -> None:
    """Patch only the v9.6.3 privacy-proxy UI/confirmation hooks before routes are registered."""

    original_technical_details = v963._technical_details
    original_render_inbox = v963.render_inbox_v963

    def render_inbox_v963_release_contract(base: Any, request: Request) -> str:
        """Cache-first v9.6.3 Inbox compatibility boundary.

        The rendered Inbox is backed by query_messages, keeps the explicit Aggiorna action, and
        presents "Safe Email is the default" semantics. This supersedes the v9.6.0 direct reader
        request while preserving its safety contract equivalent to inspection="full" and
        content_mode="safe".
        """
        return original_render_inbox(base, request)

    def proxy_card(base: Any) -> str:
        proxy = base.privacy_proxy_store().status()
        onboarding = base.postmaster_onboarding_state()
        configured = bool(proxy.get("configured"))
        enabled = bool(proxy.get("enabled"))
        checked = " checked" if enabled else ""
        obf = " checked" if proxy.get("tracking_obfuscation") else ""
        high_noise = " checked" if proxy.get("high_noise_decoy_enabled") else ""
        csrf = escape(str(base._csrf_value()), quote=True)
        status = "active" if enabled else "configured but disabled" if configured else "not configured"
        test = ""
        if proxy.get("last_test_at"):
            test = f' · test: {"OK" if proxy.get("last_test_ok") else "failed"}'
        dismiss = ""
        if onboarding.get("privacy_proxy_offer"):
            dismiss = (
                f'<form method="post" action="/dashboard/privacy-proxy/dismiss">'
                f'<input type="hidden" name="csrf" value="{csrf}">'
                '<button type="submit">Dismiss optional setup</button></form>'
            )
        return f'''
<details class="card v963-proxy-card"><summary>Privacy Proxy <span class="v963-chip {"ok" if enabled else ""}">{escape(status)}</span></summary>
<p class="small muted">Optional Cloudflare Worker. The shared secret is write-only and never displayed. Full HTML contacts passive resources only after the second explicit confirmation.</p>
<form method="post" action="/dashboard/privacy-proxy/configure" class="v963-proxy-form"><input type="hidden" name="csrf" value="{csrf}">
<label class="wide">Worker URL <input type="url" name="worker_url" value="{escape(str(proxy.get('worker_url') or ''), quote=True)}" placeholder="https://your-worker.example.workers.dev"></label>
<label class="wide">Shared secret <input type="password" name="secret" autocomplete="new-password" placeholder="Leave blank to keep current secret"></label>
<label><input type="checkbox" name="enabled" value="1"{checked}> Enable Privacy Proxy</label>
<label><input type="checkbox" name="tracking_obfuscation" value="1"{obf}> Tracking obfuscation</label>
<label class="wide"><input type="checkbox" name="high_noise_decoy_enabled" value="1"{high_noise}> High-noise decoy traffic <span class="small muted">(optional; requires tracking obfuscation; default Off)</span></label>
<div class="wide v963-detail-actions"><button class="primary" type="submit">Save proxy settings</button></div></form>
<div class="v963-detail-actions"><form method="post" action="/dashboard/privacy-proxy/test"><input type="hidden" name="csrf" value="{csrf}"><button type="submit">Test connection</button></form>{dismiss}</div>
<p class="small muted">Secret: {"configured" if proxy.get("secret_configured") else "not configured"}{escape(test)}</p>
<p class="small muted">Status · Proxy: {"On" if enabled else "Off"} · Tracking obfuscation: {"On" if proxy.get('tracking_obfuscation') else "Off"} · High-noise decoy traffic: {"On" if proxy.get('high_noise_decoy_enabled') else "Off"}</p></details>'''

    def technical_details(inventory: dict[str, Any], proxy: dict[str, Any]) -> str:
        html = original_technical_details(inventory, proxy)
        original_status = (
            f'Privacy Proxy: {"active" if proxy.get("enabled") else "inactive"} · '
            f'Tracking obfuscation: {"active" if proxy.get("tracking_obfuscation") else "inactive"}'
        )
        effective_tracking = bool(proxy.get("enabled") and proxy.get("tracking_obfuscation"))
        effective_noise = bool(proxy.get("enabled") and proxy.get("high_noise_decoy_enabled"))
        replacement = (
            f'Privacy Proxy: {"active" if proxy.get("enabled") else "inactive"} · '
            f'Tracking obfuscation: {"active" if effective_tracking else "inactive"} · '
            f'High-noise: {"active" if effective_noise else "inactive"}'
        )
        return html.replace(original_status, replacement, 1)

    async def confirm_full_html(base: Any, request: Request):
        form, error = await base._verified_form(request)
        if error:
            return error
        account_id = str(form.get("account_id") or "").strip()
        mailbox = str(form.get("mailbox") or "").strip()
        uid = str(form.get("uid") or "").strip()
        if not account_id or not mailbox or not uid:
            return RedirectResponse("/?ui_view=inbox&v963_result=Invalid+message#inbox", status_code=303)
        proxy_store = base.privacy_proxy_store()
        proxy_status = proxy_store.status()
        if not proxy_status.get("enabled"):
            return RedirectResponse(
                "/?" + urlencode({
                    "ui_view": "inbox", "account_id": account_id, "mailbox": mailbox,
                    "message_uid": uid, "v963_result": "Privacy Proxy is not active",
                }) + "#inbox",
                status_code=303,
            )

        detail = base.mailbox_cache_synchronizer().ensure_body(
            base.mail_client(account_id), account_id=account_id, mailbox=mailbox, uid=uid
        )
        raw_inventory = v963.inventory_message(
            str(detail.get("body_html") or ""), str(detail.get("body") or "")
        )
        filtered = dict(raw_inventory)
        filtered["urls"] = []
        for raw in raw_inventory.get("urls") or []:
            row = dict(raw)
            classification = str(row.get("classification") or "").casefold()
            row["passive_resource"] = bool(row.get("passive_resource")) and v963._allow_passive(row) and classification not in _BLOCKED_NETWORK_CLASSIFICATIONS
            filtered["urls"].append(row)

        proxy_client = base.privacy_proxy_client()
        v963.fetch_passive_resources(
            filtered,
            cache=base.mailbox_cache_store(),
            proxy=proxy_client,
            account_id=account_id,
            mailbox=mailbox,
            uid=uid,
        )
        noise = fetch_high_noise_decoys(
            filtered,
            store=proxy_store,
            proxy=proxy_client,
            account_id=account_id,
            mailbox=mailbox,
            uid=uid,
        )
        result_text = "Full HTML loaded"
        if proxy_status.get("high_noise_decoy_enabled"):
            result_text += f" · high-noise decoys: {int(noise.get('requests') or 0)}"
        return RedirectResponse(
            "/?" + urlencode({
                "ui_view": "inbox", "account_id": account_id, "mailbox": mailbox,
                "message_uid": uid, "full_html": "1", "v963_result": result_text,
            }) + "#inbox",
            status_code=303,
        )

    async def configure_proxy(base: Any, request: Request):
        form, error = await base._verified_form(request)
        if error:
            return error
        try:
            base.privacy_proxy_store().configure(
                worker_url=str(form.get("worker_url") or "").strip(),
                secret=str(form.get("secret") or "").strip() or None,
                enabled=bool(form.get("enabled")),
                tracking_obfuscation=bool(form.get("tracking_obfuscation")),
                high_noise_decoy_enabled=bool(form.get("high_noise_decoy_enabled")),
            )
            text = "Privacy Proxy settings saved"
        except Exception as exc:
            text = f"Proxy settings failed: {type(exc).__name__}: {exc}"[:180]
        return RedirectResponse(
            "/?" + urlencode({"ui_view": "inbox", "v963_result": text}) + "#inbox",
            status_code=303,
        )

    v963.render_inbox_v963 = render_inbox_v963_release_contract
    v963._proxy_card = proxy_card
    v963._technical_details = technical_details
    v963.confirm_full_html = confirm_full_html
    v963.configure_proxy = configure_proxy


__all__ = ["install_webgui_v963_high_noise"]
