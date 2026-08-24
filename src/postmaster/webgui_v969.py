from __future__ import annotations

from email import policy
from email.parser import BytesParser
from html import escape
import re
from typing import Any
from urllib.parse import urlencode

from starlette.requests import Request
from starlette.responses import RedirectResponse

from .outbound_operations_v969 import outbound_operation_store
from .privacy_cache_v969 import PassiveContentService


def _sender_metadata(account_id: str, raw: bytes) -> dict[str, Any]:
    try:
        msg = BytesParser(policy=policy.default).parsebytes(raw)
    except Exception:
        return {}
    message_id = str(msg.get("Message-ID") or "")
    metadata = outbound_operation_store().by_message_id(account_id, message_id)
    if metadata:
        return metadata
    return {
        "to": [str(msg.get("To") or "")] if str(msg.get("To") or "").strip() else [],
        "cc": [str(msg.get("Cc") or "")] if str(msg.get("Cc") or "").strip() else [],
        "bcc": [],
    }


def _remote_refresh_form(
    base_obj: Any,
    *,
    account_id: str,
    mailbox: str,
    uid: str,
    label: str,
) -> str:
    csrf = escape(str(base_obj._csrf_value()), quote=True)
    return (
        '<form method="post" action="/dashboard/inbox/full-html" class="v963-refresh-remote">'
        f'<input type="hidden" name="csrf" value="{csrf}">'
        f'<input type="hidden" name="account_id" value="{escape(account_id, quote=True)}">'
        f'<input type="hidden" name="mailbox" value="{escape(mailbox, quote=True)}">'
        f'<input type="hidden" name="uid" value="{escape(uid, quote=True)}">'
        '<input type="hidden" name="refresh_remote" value="1">'
        f'<button type="submit">{escape(label)}</button></form>'
    )


def _install_webgui_v969(base: Any, v963: Any, service: PassiveContentService) -> None:
    async def confirm_full_html(base_obj: Any, request: Request):
        form, error = await base_obj._verified_form(request)
        if error:
            return error
        account_id = str(form.get("account_id") or "").strip()
        mailbox = str(form.get("mailbox") or "").strip()
        uid = str(form.get("uid") or "").strip()
        refresh = str(form.get("refresh_remote") or "").strip().casefold() in {
            "1", "true", "yes", "on",
        }
        if not account_id or not mailbox or not uid:
            return RedirectResponse(
                "/?ui_view=inbox&v963_result=Invalid+message#inbox", status_code=303
            )
        if not base_obj.privacy_proxy_store().status().get("enabled"):
            return RedirectResponse(
                "/?" + urlencode({
                    "ui_view": "inbox", "account_id": account_id, "mailbox": mailbox,
                    "message_uid": uid, "v963_result": "Privacy Proxy is not active",
                }) + "#inbox",
                status_code=303,
            )
        try:
            result = service.fetch_message(
                account_id=account_id, mailbox=mailbox, uid=uid, refresh=refresh,
            )
        except Exception as exc:
            result = {
                "ok": False, "render_state": "failure", "cache_only": False,
                "diagnostics": {
                    "discovered": 0, "genuine_attempted": 0, "genuine_succeeded": 0,
                    "genuine_failed": 0, "decoy_attempted": 0, "decoy_succeeded": 0,
                    "excluded_navigation_action": 0,
                },
                "safe_error": type(exc).__name__,
            }
        diag = dict(result.get("diagnostics") or {})
        state = str(result.get("render_state") or "failure")
        text = (
            f"Full HTML fetch {state} · discovered: {int(diag.get('discovered') or 0)}"
            f" · genuine: {int(diag.get('genuine_succeeded') or 0)}/"
            f"{int(diag.get('genuine_attempted') or 0)}"
            f" · cached: {int(diag.get('cached_succeeded') or 0)}"
            f" · decoys: {int(diag.get('decoy_succeeded') or 0)}/"
            f"{int(diag.get('decoy_attempted') or 0)}"
        )
        if result.get("cache_only"):
            text += " · cache-only"
        params = {
            "ui_view": "inbox", "account_id": account_id, "mailbox": mailbox,
            "message_uid": uid, "v963_result": text,
        }
        if state != "failure":
            params["full_html"] = "1"
        else:
            # The negative-cache state is intentionally reopen-safe (zero network), but
            # the user must retain an explicit way to authorize a fresh network cycle.
            params["remote_fetch_failed"] = "1"
        return RedirectResponse("/?" + urlencode(params) + "#inbox", status_code=303)

    confirm_full_html._postmaster_v969_shared_pipeline = True  # type: ignore[attr-defined]
    v963.confirm_full_html = confirm_full_html

    current_detail = v963._detail
    if getattr(current_detail, "_postmaster_v969_sent_recipients", False):
        return

    def _detail(
        base_obj: Any, params: dict[str, str], account_id: str, mailbox: str,
        role: str, uid: str, request: Request,
    ) -> str:
        rendered = current_detail(base_obj, params, account_id, mailbox, role, uid, request)
        if role == "sent":
            raw = base_obj.mailbox_cache_store().raw_message(account_id, mailbox, uid)
            if raw:
                metadata = _sender_metadata(account_id, raw)
                to_values = list(metadata.get("to") or [])
                cc_values = list(metadata.get("cc") or [])
                bcc_values = list(metadata.get("bcc") or [])
                lines = []
                if to_values:
                    lines.append("To: " + ", ".join(to_values))
                if cc_values:
                    lines.append("Cc: " + ", ".join(cc_values))
                if bcc_values:
                    lines.append("Bcc: " + ", ".join(bcc_values))
                if lines:
                    recipients = "<br>".join(escape(line) for line in lines)
                    rendered = re.sub(
                        r'(<p class="small muted">)To: .*?(?P<date> · [^<]*</p>)',
                        lambda match: match.group(1) + recipients + match.group("date"),
                        rendered,
                        count=1,
                        flags=re.DOTALL,
                    )

        full_render = request.query_params.get("full_html") == "1"
        explicit_failure = request.query_params.get("remote_fetch_failed") == "1"
        cached_failure = False
        if not full_render:
            try:
                cached = service.render_cached_message(
                    account_id=account_id,
                    mailbox=mailbox,
                    uid=uid,
                )
                diag = dict(cached.get("diagnostics") or {})
                cached_failure = bool(
                    cached.get("render_state") == "failure"
                    and int(diag.get("negative_cache_hits") or 0) > 0
                )
            except Exception:
                cached_failure = False

        if full_render:
            refresh_form = _remote_refresh_form(
                base_obj,
                account_id=account_id,
                mailbox=mailbox,
                uid=uid,
                label="Refresh remote content",
            )
            marker = '<span class="v963-chip warn">HTML completo · passive resources via local cache</span>'
            rendered = rendered.replace(marker, marker + refresh_form, 1)
        elif explicit_failure or cached_failure:
            retry_form = _remote_refresh_form(
                base_obj,
                account_id=account_id,
                mailbox=mailbox,
                uid=uid,
                label="Riprova contenuti remoti",
            )
            failure_ui = (
                '<div class="v963-warning v969-remote-failure">'
                '<strong>I contenuti remoti non sono disponibili dalla cache.</strong>'
                '<p class="small muted">La riapertura normale non effettua nuove richieste. '
                'Usa questa azione solo per autorizzare un nuovo tentativo tramite Privacy Proxy.</p>'
                + retry_form
                + '</div>'
            )
            marker = '<span class="v963-chip ok">Email sicura · default</span>'
            rendered = rendered.replace(marker, marker + failure_ui, 1)
        return rendered

    _detail._postmaster_v969_sent_recipients = True  # type: ignore[attr-defined]
    v963._detail = _detail
