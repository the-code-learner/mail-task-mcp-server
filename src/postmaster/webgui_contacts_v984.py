"""v9.8.4 Contacts: Inbox review, Sent contacts, saved book and global sending policies.

Compatibility recipient/domain endpoints remain routable, but their control panels
live under Contacts in primary navigation. The underlying MCP authorization remains
instance-wide; owner/project/account are presentation and query selectors only.
"""
from __future__ import annotations

from html import escape
from typing import Any
from urllib.parse import urlencode

from starlette.requests import Request
from starlette.responses import PlainTextResponse, RedirectResponse, Response
from starlette.routing import Mount, Route

_TABS = (
    ("incoming", "From Inbox"),
    ("sent", "From Sent"),
    ("saved", "Address book"),
    ("ignored", "Ignored"),
    ("recipients", "Authorized recipients"),
    ("domains", "Authorized domains"),
)


def _e(value: Any) -> str:
    return escape(str(value or ""), quote=True)


def _href(tab: str, account: str = "", flash: str = "") -> str:
    params = {"ui_view": "contacts", "contact_tab": tab}
    if account:
        params["account_id"] = account
    if flash:
        params["flash"] = flash[:180]
    return "/?" + urlencode(params) + "#contacts"


def _post(csrf: str, action: str, email: str, account: str = "",
          label: str = "", *, name: str = "") -> str:
    text = _e(label or action.replace("_", " ").title())
    return (
        '<form method="post" action="/dashboard/contacts/action" style="display:inline">'
        f'<input type="hidden" name="csrf" value="{_e(csrf)}">'
        f'<input type="hidden" name="action" value="{_e(action)}">'
        f'<input type="hidden" name="email" value="{_e(email)}">'
        f'<input type="hidden" name="name" value="{_e(name)}">'
        f'<input type="hidden" name="account_id" value="{_e(account)}">'
        f'<button type="submit">{text}</button></form>'
    )


def _rows(result: Any, key: str) -> list[dict[str, Any]]:
    if isinstance(result, list):
        return [dict(x) for x in result if isinstance(x, dict)]
    if isinstance(result, dict):
        return [dict(x) for x in result.get(key, []) if isinstance(x, dict)]
    return []


def render_contacts(base: Any, core: Any, request: Request) -> str:
    from .webgui_v962_views import _panel
    tab = str(request.query_params.get("contact_tab") or "incoming")
    if tab not in {x[0] for x in _TABS}:
        tab = "incoming"
    account = str(
        request.query_params.get("account_id") or request.query_params.get("account") or ""
    ).strip()
    accounts = base.account_store().list_accounts()
    options = '<option value="">All accounts</option>' + "".join(
        f'<option value="{_e(x.get("id"))}"'
        f'{" selected" if str(x.get("id") or "") == account else ""}>'
        f'{_e(x.get("label") or x.get("email_address") or x.get("id"))}</option>'
        for x in accounts
    )
    csrf = str(base._csrf_value())
    tabs = "".join(
        f'<a class="v984-contact-tab{" selected" if tab==key else ""}" '
        f'href="{_e(_href(key,account))}">{_e(title)}</a>'
        for key, title in _TABS
    )
    info = (
        "<p class='small muted'>Saving a contact does not authorize outbound mail. "
        "Recipient and domain authorizations apply globally across configured sender accounts.</p>"
    )
    body = []
    if tab == "incoming":
        result = core.list_incoming_contact_candidates(account_id=account or None, limit=100)
        error = (result or {}).get("error") if isinstance(result, dict) else ""
        errors = result.get("mailbox_errors", []) if isinstance(result, dict) else []
        if error:
            body.append(f'<div class="flash">Unable to read Inbox senders: {_e(error)}</div>')
        elif errors:
            body.append('<div class="notice">Some mailboxes could not be queried; '
                        'other results are still available.</div>')
        candidates = _rows(result, "contacts")
        for x in candidates:
            email = str(x.get("email") or "")
            state = str(x.get("status") or "candidate")
            account_id = str(x.get("account_id") or "")
            if state == "candidate":
                actions = _post(csrf, "save", email, account_id, "Add to address book", name=str(x.get("name") or ""))
                actions += _post(csrf, "ignore", email, account_id, "Ignore")
            elif state == "saved":
                actions = '<span class="badge ok">Saved</span>'
            else:
                actions = _post(csrf, "restore", email, account_id, "Restore")
            body.append(
                '<div class="v984-contact-row"><div class="grow">'
                f'<strong>{_e(x.get("name") or email)}</strong><br>'
                f'<span class="small mono">{_e(email)}</span><br>'
                f'<span class="small muted">Account: {_e(account_id)} · '
                f'Last message: {_e(x.get("last_message_at") or "Unknown")}</span>'
                f'</div><div class="actions">{actions}</div></div>'
            )
        if not candidates:
            body.append('<p class="muted">No Inbox senders found in the bounded IMAP sample.</p>')
        body.append('<p class="small muted">On-demand IMAP search, recent maximum 100 per account. '
                    'This is a review queue, not a continuous mailbox sync.</p>')
    elif tab == "sent":
        target = [x for x in accounts if not account or str(x.get("id") or "") == account]
        for acct in target:
            aid = str(acct.get("id") or "")
            try:
                result = base.mail_client(aid).list_known_contacts()
                addresses = result.get("addresses", []) if isinstance(result, dict) else []
            except Exception:
                body.append(f'<p class="muted">Sent history unavailable for {_e(aid)}.</p>')
                continue
            body.append(f'<h3>{_e(acct.get("label") or aid)}</h3>')
            for email in addresses[:100]:
                body.append(
                    '<div class="v984-contact-row"><span class="mono grow">'
                    f'{_e(email)}</span>{_post(csrf, "save", str(email), aid, "Add to address book")}</div>'
                )
        if not body:
            body.append('<p class="muted">No recent Sent recipients.</p>')
    elif tab in {"saved", "ignored"}:
        result = core.list_contacts(account_id=account or None, status=tab, limit=500)
        contacts = _rows(result, "contacts")
        for item in contacts:
            email = str(item.get("email") or "")
            action = (
                _post(csrf, "ignore", email, str(item.get("source_account_id") or ""), "Ignore")
                if tab == "saved"
                else _post(csrf, "restore", email, str(item.get("source_account_id") or ""), "Restore")
            )
            body.append(
                f'<div class="v984-contact-row"><div class="grow"><b>{_e(item.get("name") or email)}</b>'
                f'<br><span class="small mono">{_e(email)}</span>'
                f'<br><span class="small muted">{_e(item.get("note"))}</span></div>{action}</div>'
            )
        if not contacts:
            body.append('<p class="muted">No contacts in this section.</p>')
        if tab == "saved":
            body.append(
                f'<details><summary>Add contact manually</summary>'
                f'<form method="post" action="/dashboard/contacts/action">'
                f'<input type="hidden" name="csrf" value="{_e(csrf)}">'
                '<input type="hidden" name="action" value="save">'
                f'<input type="hidden" name="account_id" value="{_e(account)}">'
                '<label>Email <input type="email" name="email" required></label>'
                '<label>Name <input name="name"></label>'
                '<label>Note <input name="note"></label>'
                '<button type="submit">Save contact</button></form></details>'
            )
    elif tab == "recipients":
        result = base.list_authorized_recipients()
        authorized = _rows(result, "recipients")
        for x in authorized:
            email = str(x.get("email") or x.get("email_address") or x.get("recipient") or "")
            body.append(
                f'<div class="v984-contact-row"><div class="grow"><b>{_e(email)}</b>'
                f'<p class="small muted">{_e(x.get("note"))}</p></div>'
                f'<form method="post" action="/dashboard/recipient/remove">'
                f'<input type="hidden" name="csrf" value="{_e(csrf)}">'
                f'<input type="hidden" name="email" value="{_e(email)}">'
                '<button class="danger" type="submit" onclick="return confirm(\'Revoke global recipient authorization?\')">Revoke</button>'
                '</form></div>'
            )
        body.append(
            f'<details><summary>Authorize recipient globally</summary>'
            f'<form method="post" action="/dashboard/recipient/add">'
            f'<input type="hidden" name="csrf" value="{_e(csrf)}">'
            '<label>Email <input name="email" type="email" required></label>'
            '<label>Note <input name="note"></label><button type="submit">Authorize</button>'
            '</form></details>'
        )
    elif tab == "domains":
        result = base.list_authorized_domains()
        authorized = _rows(result, "domains")
        for x in authorized:
            domain = str(x.get("domain") or "")
            body.append(
                f'<div class="v984-contact-row"><div class="grow"><b>{_e(domain)}</b>'
                f'<p class="small muted">{_e(x.get("note"))}</p></div>'
                f'<form method="post" action="/dashboard/domain/remove">'
                f'<input type="hidden" name="csrf" value="{_e(csrf)}">'
                f'<input type="hidden" name="domain" value="{_e(domain)}">'
                '<button class="danger" type="submit" onclick="return confirm(\'Revoke global domain authorization?\')">Revoke</button>'
                '</form></div>'
            )
        body.append(
            f'<details><summary>Authorize domain globally</summary>'
            f'<form method="post" action="/dashboard/domain/add">'
            f'<input type="hidden" name="csrf" value="{_e(csrf)}">'
            '<label>Domain <input name="domain" required></label>'
            '<label>Note <input name="note"></label><button type="submit">Authorize</button>'
            '</form></details>'
        )
    css = """<style>
.v984-contacts .v984-contact-tabs{display:flex;gap:6px;flex-wrap:wrap;margin:14px 0}
.v984-contacts .v984-contact-tab{padding:8px 11px;border:1px solid var(--line);border-radius:8px;text-decoration:none}
.v984-contacts .v984-contact-tab.selected{background:var(--accent);color:#061324}
.v984-contacts .v984-contact-row{border-bottom:1px solid var(--line);padding:12px 0;display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.v984-contacts .v984-contact-row .grow{flex:1;min-width:130px}
.v984-contacts details{padding:12px;margin:13px 0;border:1px solid var(--line);border-radius:9px}
.v984-contacts details form{display:flex;gap:9px;flex-wrap:wrap;margin:12px 0}
</style>"""
    markup = (
        f'<div class="v984-contacts">{css}<div class="v951-pagehead">'
        '<h2>Contacts</h2><p>Address book, Inbox review and global sending authorizations</p></div>'
        f'<form action="/" method="get" class="v951-toolbar">'
        '<input type="hidden" name="ui_view" value="contacts">'
        f'<input type="hidden" name="contact_tab" value="{_e(tab)}">'
        f'<label>Email account <select name="account_id">{options}</select></label>'
        '<button type="submit">Apply</button></form>'
        f'<nav class="v984-contact-tabs">{tabs}</nav>{info}'
        f'<section class="card wide">{"".join(body)}</section></div>'
    )
    return _panel("contacts", markup)


def install_contacts_webgui_v984(app: Any, base: Any, core: Any,
                                v962: Any, views: Any, v970: Any) -> None:
    # Contacts is a declared lazy view; installation adds its route and handler once.
    # Do not use membership in VIEWS as an installation sentinel: consumers may
    # import the view tuple before runtime composition.
    if any(
        isinstance(route, Route) and route.path == "/dashboard/contacts/action"
        for route in app.router.routes
    ):
        return
    if "contacts" not in views.VIEWS:
        views.VIEWS = tuple(views.VIEWS) + ("contacts",)
    v962.VIEWS = views.VIEWS
    if not any(view == "contacts" for view, _label in v962.NAV):
        v962.NAV = tuple(v962.NAV) + (("contacts", "Contacts"),)

    # Preserve legacy domains/recipients views for old URLs but move primary links to Contacts.
    new_groups = []
    for heading, links in v970.NAV_GROUPS:
        kept = [x for x in links if x[0] not in {"domains", "recipients"}]
        if heading == "Organize" and not any(view == "contacts" for view, _label, _code in kept):
            kept.append(("contacts", "Contacts", "CT"))
        new_groups.append((heading, tuple(kept)))
    v970.NAV_GROUPS = tuple(new_groups)
    v970.VIEW_LABELS["contacts"] = (
        "Contacts", "Inbox sender review, address book and sending authorizations",
    )
    # The v9.7 enterprise script embeds a JSON literal produced before this overlay.
    # Add the new label by extending the actual inline mapping; no runtime network.
    v962.SCRIPT += """
<script id="v984-contacts-context-title">
(() => { document.addEventListener('click', e => {
  const link=e.target.closest('a[data-v970-nav="contacts"]');
  if(link) {
    const title=document.querySelector('[data-v970-context-title]');
    const subtitle=document.querySelector('[data-v970-context-subtitle]');
    if(title) title.textContent='Contacts';
    if(subtitle) subtitle.textContent='Inbox senders, address book and authorization';
  }
});})();
</script>
"""
    previous_render = v962.render_view

    def route_render(b: Any, c: Any, req: Request, view: str) -> str:
        if view == "contacts":
            return render_contacts(b, c, req)
        return previous_render(b, c, req, view)

    v962.render_view = route_render
    views.render_view = route_render

    async def contact_action(req: Request) -> Response:
        form, error = await base._verified_form(req)
        if error is not None:
            return error
        action = str(form.get("action") or "").strip()
        email = str(form.get("email") or "").strip()
        account = str(form.get("account_id") or "").strip()
        try:
            if action == "save":
                result = core.save_contact(
                    email=email, name=str(form.get("name") or ""),
                    note=str(form.get("note") or ""), account_id=account,
                )
            elif action in {"ignore", "restore"}:
                result = core.set_contact_ignored(
                    email=email, ignored=action == "ignore", account_id=account,
                )
            else:
                return PlainTextResponse("Unsupported contact action", status_code=400)
            if not isinstance(result, dict) or not result.get("ok"):
                raise ValueError(str(result.get("error") if isinstance(result,dict) else "Unable to update contact"))
        except Exception:
            return PlainTextResponse("Contact update failed", status_code=400)
        return RedirectResponse(
            _href("incoming" if action != "save" else "saved", account),
            status_code=303,
        )

    idx = next((i for i, r in enumerate(app.router.routes) if isinstance(r, Mount)),
               len(app.router.routes))
    app.router.routes.insert(
        idx, Route("/dashboard/contacts/action", contact_action, methods=["POST"]),
    )
