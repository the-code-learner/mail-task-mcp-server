from __future__ import annotations

from html import escape
from typing import Any
from urllib.parse import urlencode

from starlette.requests import Request
from starlette.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from starlette.routing import Mount, Route

from . import webgui_v951 as v951
from . import webgui_v960 as v960
from .thread_recipients import merge_thread_cc, sender_identity_addresses


_CONFIRM_FIELD = "confirm_suppressed"


def _hidden(name: str, value: Any) -> str:
    if value is None:
        return ""
    return (
        f'<input type="hidden" name="{escape(name, quote=True)}" '
        f'value="{escape(str(value), quote=True)}">'
    )


def _form_payload(form: Any) -> dict[str, Any]:
    """Normalize the existing v9.6 composer fields without inventing new send semantics."""
    return {
        "thread_mode": str(form.get("thread_mode") or "send").strip(),
        "compose_action": str(form.get("compose_action") or "send").strip(),
        "to": v960._split_addresses(form.get("to")),
        "cc": v960._split_addresses(form.get("cc")),
        "bcc": v960._split_addresses(form.get("bcc")),
        "attachments": [
            {"file_id": value}
            for value in v960._split_addresses(form.get("attachment_file_ids"))
        ],
        "body": str(form.get("body") or ""),
        "body_html": str(form.get("body_html") or "").strip() or None,
        "body_amp": str(form.get("body_amp") or "").strip() or None,
        "account_id": str(form.get("account_id") or "").strip() or None,
        "mailbox": str(form.get("thread_mailbox") or "").strip(),
        "uid": str(form.get("thread_uid") or "").strip(),
        "subject": str(form.get("subject") or ""),
        "track_opens": True if form.get("track_opens") else None,
        "campaign_id": str(form.get("campaign_id") or "").strip() or None,
        "newsletter_mode": bool(form.get("newsletter_mode")),
        "unsubscribe_url": str(form.get("unsubscribe_url") or "").strip() or None,
        "unsubscribe_email": str(form.get("unsubscribe_email") or "").strip() or None,
        "one_click_unsubscribe": bool(form.get("one_click_unsubscribe")),
        "automatic_unsubscribe": bool(form.get("automatic_unsubscribe")),
        "dsn_notify_success": bool(form.get("dsn_notify_success")),
        "idempotency_key": str(form.get("idempotency_key") or "").strip() or None,
        "force_send": bool(form.get("force_send")),
        "return_view": str(form.get("return_view") or "").strip(),
        "confirmed": str(form.get(_CONFIRM_FIELD) or "") == "1",
    }


def _thread_recipients(client: Any, payload: dict[str, Any]) -> list[str]:
    mode = str(payload["thread_mode"])
    resolved = client.resolve_thread_recipients(
        str(payload["mailbox"]),
        str(payload["uid"]),
        mode=mode,
    )
    identities = sender_identity_addresses(client.settings)
    cc = merge_thread_cc(
        resolved["to"],
        resolved["cc"],
        payload["cc"],
        sender_identities=identities,
    )
    bcc = client._clean_unlisted_recipients(payload["bcc"]) if payload["bcc"] else []
    return list(dict.fromkeys([*resolved["to"], *cc, *bcc]))


def _send_recipients(client: Any, payload: dict[str, Any]) -> list[str]:
    mode = str(payload["thread_mode"])
    if mode in {"reply", "follow_up"} and payload["mailbox"] and payload["uid"]:
        return _thread_recipients(client, payload)
    return client._clean_unlisted_recipients(
        [*payload["to"], *payload["cc"], *payload["bcc"]]
    )


def _confirmation_page(base: Any, form: Any, blocked: list[dict[str, Any]]) -> HTMLResponse:
    rows = "".join(
        "<li><strong>"
        + escape(str(row.get("recipient") or ""))
        + "</strong> — "
        + escape(str(row.get("reason") or "suppressed"))
        + (" · " + escape(str(row.get("source") or "")) if row.get("source") else "")
        + "</li>"
        for row in blocked
    )
    preserve_names = (
        "thread_mode", "compose_action", "to", "cc", "bcc", "attachment_file_ids",
        "body", "body_html", "body_amp", "account_id", "thread_mailbox", "thread_uid",
        "subject", "track_opens", "campaign_id", "newsletter_mode", "unsubscribe_url",
        "unsubscribe_email", "one_click_unsubscribe", "automatic_unsubscribe",
        "dsn_notify_success", "idempotency_key", "force_send", "return_view",
    )
    hidden = _hidden("csrf", base._csrf_value())
    for name in preserve_names:
        value = form.get(name)
        if value is not None and str(value) != "":
            hidden += _hidden(name, value)
    hidden += _hidden(_CONFIRM_FIELD, "1")
    return HTMLResponse(
        "<!doctype html><html><head><meta charset=\"utf-8\"><title>Confirm suppressed recipients</title></head>"
        "<body><main><h1>Suppressed recipient confirmation required</h1>"
        "<p>No email has been sent. These recipients are on the local suppression list:</p>"
        f"<ul>{rows}</ul>"
        "<p>Confirming authorizes only this send. It does not remove suppression and does not add "
        "the recipients to the automated-send allowlist.</p>"
        '<form method="post" action="/dashboard/compose/send">'
        f"{hidden}"
        '<button type="submit">Confirm and send this message</button></form>'
        '<p><a href="/?ui_view=compose#compose">Cancel</a></p>'
        "</main></body></html>",
        status_code=409,
        headers={"Cache-Control": "no-store"},
    )


def _result_redirect(form: Any, result: Any, success_text: str) -> RedirectResponse:
    ok = isinstance(result, dict) and result.get("ok") is not False
    text = success_text if ok else "Operation failed: " + str(
        result.get("error") if isinstance(result, dict) else result
    )[:180]
    view = "inbox" if str(form.get("return_view") or "") == "inbox" else "compose"
    return RedirectResponse(
        "/?" + urlencode({"ui_view": view, "compose_result": text}) + f"#{view}",
        status_code=303,
    )


async def compose_send(base: Any, request: Request):
    form, error = await base._verified_form(request)
    if error:
        return error
    payload = _form_payload(form)

    # Drafting is review-only and intentionally keeps the existing v9.6 behavior.
    if payload["compose_action"] == "draft":
        return await v960.compose_send(base, request)

    client = base.mail_client(payload["account_id"])
    mode = str(payload["thread_mode"])
    if mode not in {"send", "reply", "follow_up"}:
        return _result_redirect(form, {"ok": False, "error": "Unsupported send mode"}, "Message sent")
    if mode == "send" and not payload["to"]:
        view = "inbox" if payload["return_view"] == "inbox" else "compose"
        return RedirectResponse(
            "/?" + urlencode({"ui_view": view, "compose_result": "Recipient required"}) + f"#{view}",
            status_code=303,
        )

    try:
        recipients = _send_recipients(client, payload)
        blocked = client.suppressed_recipients(recipients)
    except Exception as exc:
        return _result_redirect(
            form,
            {"ok": False, "error": f"{type(exc).__name__}: {exc}"},
            "Message sent",
        )

    if blocked and not payload["confirmed"]:
        return _confirmation_page(base, form, blocked)

    authorized_suppressed = [str(row.get("recipient") or "") for row in blocked]
    common = {
        "body": payload["body"],
        "body_html": payload["body_html"],
        "attachments": payload["attachments"] or None,
    }
    with client.manual_webgui_send(
        authorized_suppressed_recipients=authorized_suppressed if payload["confirmed"] else None
    ):
        if mode in {"reply", "follow_up"} and payload["mailbox"] and payload["uid"]:
            fn = client.reply_email if mode == "reply" else client.follow_up_email
            result = v951._safe_call(
                base,
                fn,
                mailbox=payload["mailbox"],
                uid=payload["uid"],
                cc=payload["cc"] or None,
                bcc=payload["bcc"] or None,
                track_opens=payload["track_opens"],
                campaign_id=payload["campaign_id"],
                idempotency_key=payload["idempotency_key"],
                force_send=payload["force_send"],
                **common,
            )
        else:
            result = v951._safe_call(
                base,
                client.send_email,
                to=payload["to"],
                cc=payload["cc"] or None,
                bcc=payload["bcc"] or None,
                subject=payload["subject"],
                body_amp=payload["body_amp"],
                track_opens=payload["track_opens"],
                campaign_id=payload["campaign_id"],
                newsletter_mode=payload["newsletter_mode"],
                unsubscribe_url=payload["unsubscribe_url"],
                unsubscribe_email=payload["unsubscribe_email"],
                one_click_unsubscribe=payload["one_click_unsubscribe"],
                automatic_unsubscribe=payload["automatic_unsubscribe"],
                dsn_notify_success=payload["dsn_notify_success"],
                idempotency_key=payload["idempotency_key"],
                force_send=payload["force_send"],
                **common,
            )
    return _result_redirect(form, result, "Message sent")


def install_webgui_v964(app: Any, base: Any) -> None:
    """Replace only the compose-send POST route; all other v9.6.3 UI remains intact."""
    routes = app.router.routes
    routes[:] = [
        route
        for route in routes
        if not (isinstance(route, Route) and route.path == "/dashboard/compose/send")
    ]

    async def send_route(request: Request):
        if request.method != "POST":
            return PlainTextResponse(
                "Method Not Allowed",
                status_code=405,
                headers={"Allow": "POST"},
            )
        return await compose_send(base, request)

    mount_index = next((i for i, route in enumerate(routes) if isinstance(route, Mount)), len(routes))
    routes.insert(
        mount_index,
        Route("/dashboard/compose/send", send_route, methods=["POST"], name="v964_compose_send"),
    )


__all__ = ["compose_send", "install_webgui_v964"]
