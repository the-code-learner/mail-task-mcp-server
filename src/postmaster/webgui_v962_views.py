from __future__ import annotations

from html import escape
from typing import Any
from urllib.parse import urlencode

from starlette.requests import Request
from starlette.responses import RedirectResponse

from . import webgui_v951 as v951
from . import webgui_v960 as v960
from .project_service import (
    ProjectDeletionBlocked,
    ProjectService,
    ProjectServiceError,
    slugify_project_name,
)
from .webgui_helpers import dashboard_url, owner_options, project_label_html
from .webgui_projects import files_fragment, project_overview_fragment
from .webgui_tasks import task_fragment
from .webgui_v962_perf import BoundedBaseProxy, accounts, active_projects, invalidate_structural_cache, owners


VIEWS = (
    "overview", "accounts", "mail-health", "inbox", "compose", "tracking",
    "deliveries", "suppressions", "security", "system", "coverage", "amp",
    "domains", "recipients", "projects", "knowledge", "files", "scheduler",
)


def _panel(view: str, content: str) -> str:
    return f'<section class="tab-panel" id="panel-{escape(view)}" data-panel="{escape(view)}">{content}</section>'


def _flash(request: Request) -> str:
    value = (request.query_params.get("flash") or "").strip()
    return f'<div class="flash">{escape(value)}</div>' if value else ""


def _metric(label: str, value: Any, note: str = "") -> str:
    return (
        '<div class="v951-metric">'
        f'<span>{escape(label)}</span><strong>{escape(str(value))}</strong><small>{escape(note)}</small>'
        '</div>'
    )


def _details_wrap(section_html: str, *, title: str, key: str, force_open: bool = False) -> str:
    open_attr = " open" if force_open else ""
    force = ' data-v962-force-open="1"' if force_open else ""
    return (
        f'<details class="v962-collapsible" data-v962-state-key="{escape(key, quote=True)}"{force}{open_attr}>'
        f'<summary>{escape(title)}</summary><div class="v962-collapsible-body">{section_html}</div></details>'
    )


def _wrap_section_by_heading(html: str, heading: str, *, key: str, force_open: bool = False) -> str:
    marker = f"<h2>{heading}</h2>"
    pos = html.find(marker)
    if pos < 0:
        marker = f"<h3>{heading}</h3>"
        pos = html.find(marker)
    if pos < 0:
        return html
    start = html.rfind("<section", 0, pos)
    end = html.find("</section>", pos)
    if start < 0 or end < 0:
        return html
    end += len("</section>")
    return html[:start] + _details_wrap(html[start:end], title=heading, key=key, force_open=force_open) + html[end:]


def render_overview(base: Any, request: Request) -> str:
    build = base._safe_call(base.build_status)
    scheduler_status = base._safe_call(base.scheduler().status)
    knowledge_status = base._safe_call(base.context_engine().status)
    file_status = base._safe_call(base.file_store().status)
    tracking_status = base._safe_call(base.analytics_store().status)
    account_rows = accounts(base)
    project_rows = active_projects(base)
    job_counts = scheduler_status.get("job_counts", {}) if isinstance(scheduler_status, dict) else {}
    knowledge_total = "—"
    if isinstance(knowledge_status, dict):
        knowledge_total = int(knowledge_status.get("memories", 0)) + int(knowledge_status.get("skills", 0))
    metrics = "".join((
        _metric("Runtime", build.get("version", "unknown") if isinstance(build, dict) else "unknown", "read-only snapshot"),
        _metric("Accounts", len(account_rows), "structural cache"),
        _metric("Projects", len(project_rows), "active registry"),
        _metric("Scheduled tasks", job_counts.get("scheduled", 0), "registry count"),
        _metric("Knowledge", knowledge_total, "inventory count"),
        _metric("Files", file_status.get("files", 0) if isinstance(file_status, dict) else "—", "inventory count"),
        _metric("Tracking", "ok" if isinstance(tracking_status, dict) and tracking_status.get("ok", True) else "degraded", "status only"),
    ))
    return _panel("overview", f'''{_flash(request)}<div class="v951-pagehead"><div><h2>Operations Dashboard</h2><p>Fast point-in-time shell: other tabs are not queried until opened.</p></div></div>
<div class="v951-metrics">{metrics}</div><div class="notice"><strong>v9.6.2 lazy rendering:</strong> this dashboard deliberately avoids loading mail, tracking event lists, task rows, files or Knowledge rows for hidden tabs.</div>''')


def _account_security_options(current: str) -> str:
    return "".join(
        f'<option value="{value}"{" selected" if current == value else ""}>{escape(label)}</option>'
        for value, label in (("ssl", "SSL/TLS"), ("starttls", "STARTTLS"), ("plain", "Plain / no TLS"))
    )


def render_accounts(base: Any, request: Request) -> str:
    rows = accounts(base)
    edit_id = (request.query_params.get("edit_account") or "").strip()
    current: dict[str, Any] = {}
    if edit_id:
        try:
            current = dict(base.account_store().get_account(edit_id))
        except Exception:
            current = {}
    table_rows = []
    for row in rows:
        aid = str(row.get("id") or "")
        edit_url = f'/?{urlencode({"ui_view":"accounts","edit_account":aid})}#accounts'
        table_rows.append(
            f'<tr><td><strong>{escape(str(row.get("label") or row.get("email_address") or aid))}</strong><div class="small muted mono">{escape(aid)}</div></td>'
            f'<td>{escape(str(row.get("email_address") or ""))}</td><td>{"enabled" if row.get("enabled", True) else "disabled"}</td>'
            f'<td class="actions"><a href="{escape(edit_url, quote=True)}"><button type="button">Edit</button></a>'
            f'<form method="post" action="/dashboard/account/test"><input type="hidden" name="csrf" value="{escape(base._csrf_value(), quote=True)}"><input type="hidden" name="account_id" value="{escape(aid, quote=True)}"><button type="submit">Test</button></form>'
            f'<form method="post" action="/dashboard/account/default"><input type="hidden" name="csrf" value="{escape(base._csrf_value(), quote=True)}"><input type="hidden" name="account_id" value="{escape(aid, quote=True)}"><button type="submit">Default</button></form>'
            f'<form method="post" action="/dashboard/account/delete" onsubmit="return confirm(\'Delete this account configuration?\');"><input type="hidden" name="csrf" value="{escape(base._csrf_value(), quote=True)}"><input type="hidden" name="account_id" value="{escape(aid, quote=True)}"><button class="danger" type="submit">Delete</button></form></td></tr>'
        )
    def value(key: str, default: str = "") -> str:
        return escape(str(current.get(key, default)), quote=True)
    edit = bool(current)
    form = f'''<section class="card wide"><div class="panel-title"><h2>{"Edit account" if edit else "Add email account"}</h2><span class="small muted">Connection configuration</span></div>
<form method="post" action="/dashboard/account/save"><input type="hidden" name="csrf" value="{escape(base._csrf_value(), quote=True)}"><div class="row">
<div class="field"><label>Account ID</label><input type="text" name="account_id" value="{value('id')}" {'readonly' if edit else ''} required></div><div class="field"><label>Label</label><input type="text" name="label" value="{value('label')}"></div><div class="field"><label>Email / From</label><input type="text" name="email_address" value="{value('email_address')}" required></div></div>
<div class="form-section"><h3>IMAP</h3><div class="row"><div class="field"><label>Host</label><input name="imap_host" value="{value('imap_host')}" required></div><div class="field"><label>Port</label><input type="number" name="imap_port" value="{value('imap_port','993')}" required></div><div class="field"><label>Security</label><select name="imap_security">{_account_security_options(str(current.get('imap_security') or 'ssl'))}</select></div><div class="field"><label>Username</label><input name="imap_username" value="{value('imap_username')}"></div><div class="field"><label>Password</label><input type="password" name="imap_password" placeholder="{'leave blank to keep saved password' if edit else 'required'}"></div></div></div>
<div class="form-section"><h3>SMTP</h3><div class="row"><div class="field"><label>Host</label><input name="smtp_host" value="{value('smtp_host')}" required></div><div class="field"><label>Port</label><input type="number" name="smtp_port" value="{value('smtp_port','465')}" required></div><div class="field"><label>Security</label><select name="smtp_security">{_account_security_options(str(current.get('smtp_security') or 'ssl'))}</select></div><div class="field"><label>Username</label><input name="smtp_username" value="{value('smtp_username')}"></div><div class="field"><label>Password</label><input type="password" name="smtp_password" placeholder="{'leave blank to keep saved password' if edit else 'blank = IMAP password'}"></div></div></div>
<div class="form-section"><h3>Mailbox names</h3><div class="row"><div class="field"><label>Inbox</label><input name="inbox_mailbox" value="{value('inbox_mailbox','INBOX')}"></div><div class="field"><label>Sent</label><input name="sent_mailbox" value="{value('sent_mailbox','INBOX.Sent')}"></div><div class="field"><label>Drafts</label><input name="draft_mailbox" value="{value('drafts_mailbox','INBOX.Drafts')}"></div><div class="field"><label>Junk / Spam</label><input name="junk_mailbox" value="{value('junk_mailbox','INBOX.Junk')}"></div></div></div>
<div class="row" style="margin-top:12px"><label><input type="checkbox" name="enabled" value="1"{' checked' if (not edit or current.get('enabled')) else ''}> Enabled</label><label><input type="checkbox" name="make_default" value="1"{' checked' if current.get('is_default') else ''}> Make default</label><label><input type="checkbox" name="tracking_default" value="1"{' checked' if current.get('tracking_default') else ''}> Track opens by default</label><button class="primary" type="submit">Save account</button><a href="/?ui_view=accounts#accounts">Cancel</a></div></form></section>'''
    form = _details_wrap(form, title="Edit account" if edit else "Add email account", key="accounts-editor", force_open=edit)
    return _panel("accounts", f'''{_flash(request)}<div class="v951-pagehead"><div><h2>Accounts</h2><p>Configured mail accounts. The editor is collapsed when not needed.</p></div></div>{form}<section class="card wide"><div class="scroll"><table><thead><tr><th>Account</th><th>Email</th><th>Status</th><th></th></tr></thead><tbody>{''.join(table_rows) or '<tr><td colspan="4" class="muted">No accounts configured</td></tr>'}</tbody></table></div></section>''')


def render_amp(base: Any, request: Request) -> str:
    cards = []
    csrf = escape(base._csrf_value(), quote=True)
    for row in accounts(base):
        aid = str(row.get("id") or "")
        notes = escape(str(row.get("amp_notes") or ""), quote=True)
        form = f'''<section class="card"><h3>{escape(str(row.get('label') or row.get('email_address') or aid))}</h3><div class="small muted mono">{escape(aid)}</div><form method="post" action="/dashboard/amp/state"><input type="hidden" name="csrf" value="{csrf}"><input type="hidden" name="account_id" value="{escape(aid, quote=True)}"><div class="v951-checks"><label><input type="checkbox" name="enabled" value="1"{' checked' if row.get('amp_enabled') else ''}> Enable AMP capability</label><label><input type="checkbox" name="tested" value="1"{' checked' if row.get('amp_tested') else ''}> Gmail dev-tested</label><label><input type="checkbox" name="registered" value="1"{' checked' if row.get('amp_registered') else ''}> Google registered</label><label><input type="checkbox" name="review_sent" value="1"> Mark review email sent now</label></div><input type="text" name="notes" value="{notes}" placeholder="AMP notes"><button class="primary" type="submit">Save AMP state</button></form></section>'''
        cards.append(_details_wrap(form, title=f"Configure {str(row.get('label') or aid)}", key=f"amp-{aid}"))
    return _panel("amp", f'''{_flash(request)}<div class="v951-pagehead"><div><h2>AMP</h2><p>Per-account AMP capability configuration.</p></div></div><div class="v951-grid">{''.join(cards) or '<section class="card"><span class="muted">No accounts configured</span></section>'}</div>''')


def _policy_rows(value: Any, key: str) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [row for row in value if isinstance(row, dict)]
    if isinstance(value, dict) and isinstance(value.get(key), list):
        return [row for row in value[key] if isinstance(row, dict)]
    return []


def render_domains(base: Any, request: Request) -> str:
    rows = _policy_rows(base._safe_call(base.list_authorized_domains), "domains")
    items = []
    for row in rows:
        domain = str(row.get("domain") or "")
        items.append(f'<tr><td class="mono">{escape(domain)}</td><td>{escape(str(row.get("note") or ""))}</td><td><form method="post" action="/dashboard/domain/remove"><input type="hidden" name="csrf" value="{escape(base._csrf_value(), quote=True)}"><input type="hidden" name="domain" value="{escape(domain, quote=True)}"><button class="danger" type="submit">Remove</button></form></td></tr>')
    add = f'''<section class="card"><h2>Add authorized domain</h2><form method="post" action="/dashboard/domain/add"><input type="hidden" name="csrf" value="{escape(base._csrf_value(), quote=True)}"><div class="row"><div class="field"><label>Domain</label><input name="domain" required></div><div class="field"><label>Note</label><input name="note"></div><button class="primary" type="submit">Add</button></div></form></section>'''
    return _panel("domains", f'''{_flash(request)}<div class="v951-pagehead"><div><h2>Authorized domains</h2><p>Sender-domain policy.</p></div></div>{_details_wrap(add,title='Add authorized domain',key='domains-add')}<section class="card wide"><div class="scroll"><table><thead><tr><th>Domain</th><th>Note</th><th></th></tr></thead><tbody>{''.join(items) or '<tr><td colspan="3" class="muted">No domains configured</td></tr>'}</tbody></table></div></section>''')


def render_recipients(base: Any, request: Request) -> str:
    rows = _policy_rows(base._safe_call(base.list_authorized_recipients), "recipients")
    items = []
    for row in rows:
        email = str(row.get("email") or row.get("email_address") or row.get("recipient") or "")
        items.append(f'<tr><td class="mono">{escape(email)}</td><td>{escape(str(row.get("note") or ""))}</td><td><form method="post" action="/dashboard/recipient/remove"><input type="hidden" name="csrf" value="{escape(base._csrf_value(), quote=True)}"><input type="hidden" name="email" value="{escape(email, quote=True)}"><button class="danger" type="submit">Remove</button></form></td></tr>')
    add = f'''<section class="card"><h2>Add authorized recipient</h2><form method="post" action="/dashboard/recipient/add"><input type="hidden" name="csrf" value="{escape(base._csrf_value(), quote=True)}"><div class="row"><div class="field"><label>Email</label><input type="email" name="email" required></div><div class="field"><label>Note</label><input name="note"></div><button class="primary" type="submit">Add</button></div></form></section>'''
    return _panel("recipients", f'''{_flash(request)}<div class="v951-pagehead"><div><h2>Authorized recipients</h2><p>Exact-recipient policy.</p></div></div>{_details_wrap(add,title='Add authorized recipient',key='recipients-add')}<section class="card wide"><div class="scroll"><table><thead><tr><th>Recipient</th><th>Note</th><th></th></tr></thead><tbody>{''.join(items) or '<tr><td colspan="3" class="muted">No recipients configured</td></tr>'}</tbody></table></div></section>''')


def render_tracking(base: Any, core: Any, request: Request) -> str:
    account_id = (request.query_params.get("account") or request.query_params.get("account_id") or "").strip() or None
    analytics = base.analytics_store()
    campaigns = analytics.list_campaigns(account_id=account_id, limit=201)
    deliveries = analytics.list_deliveries(account_id=account_id, limit=201)
    opens = analytics.list_open_events(account_id=account_id, limit=201)
    try:
        links = core.link_store().unified_events(account_id=account_id, limit=201)
        if isinstance(links, dict): links = links.get("events", [])
    except Exception:
        links = []
    campaigns = campaigns if isinstance(campaigns, list) else []
    deliveries = deliveries if isinstance(deliveries, list) else []
    opens = opens if isinstance(opens, list) else []
    links = links if isinstance(links, list) else []
    selector = '<option value="">All accounts</option>' + ''.join(f'<option value="{escape(str(row.get("id") or ""), quote=True)}"{" selected" if str(row.get("id") or "") == account_id else ""}>{escape(str(row.get("label") or row.get("email_address") or row.get("id") or ""))}</option>' for row in accounts(base))
    return _panel("tracking", f'''{_flash(request)}<div class="v951-pagehead"><div><h2>Tracking</h2><p>Bounded current activity. Detailed enrichment is not loaded until this tab is opened.</p></div></div><form method="get" action="/" class="v951-toolbar"><input type="hidden" name="ui_view" value="tracking"><label>Account <select name="account_id">{selector}</select></label><button type="submit">Filter</button></form><div class="v951-metrics">{_metric('Campaigns',min(len(campaigns),200),'latest bounded source')}{_metric('Deliveries',min(len(deliveries),200),'latest bounded source')}{_metric('Open events',min(len(opens),200),'observed telemetry')}{_metric('Link events',min(len(links),200),'observed telemetry')}</div><div class="notice">Each structural event list is requested with a bounded source limit (201 sentinel rows); tracking remains observed telemetry, not proof of reading.</div>''')


def render_deliveries(base: Any, request: Request) -> str:
    from .runtime_v950 import reliability_store
    account_id = (request.query_params.get("account") or request.query_params.get("account_id") or "").strip() or None
    deliveries = base.analytics_store().list_deliveries(account_id=account_id, limit=201)
    attempts = reliability_store().list_attempts(limit=201)
    deliveries = deliveries if isinstance(deliveries, list) else []
    attempts = attempts if isinstance(attempts, list) else []
    rows = []
    for row in deliveries[:200]:
        rows.append(f'<tr><td class="mono">{escape(str(row.get("id") or ""))}</td><td>{escape(str(row.get("recipient") or ""))}</td><td>{escape(str(row.get("delivery_state") or "submitted"))}</td><td>{escape(str(row.get("sent_at") or ""))}</td></tr>')
    return _panel("deliveries", f'''{_flash(request)}<div class="v951-pagehead"><div><h2>Deliveries</h2><p>Latest delivery rows with bounded source reads.</p></div></div><div class="v951-metrics">{_metric('Deliveries shown',min(len(deliveries),200),'limit 201')}{_metric('Retry attempts loaded',min(len(attempts),200),'limit 201')}</div><div class="scroll"><table><thead><tr><th>Delivery</th><th>Recipient</th><th>State</th><th>Sent</th></tr></thead><tbody>{''.join(rows) or '<tr><td colspan="4" class="muted">No deliveries</td></tr>'}</tbody></table></div><div class="notice">Ambiguous post-DATA outcomes remain delivery-uncertain and are never presented as blindly retryable.</div>''')


def render_suppressions(base: Any, request: Request) -> str:
    from .runtime_v950 import reliability_store
    rows = reliability_store().list_suppressions(active_only=True, limit=101)
    rows = rows if isinstance(rows, list) else []
    csrf = escape(base._csrf_value(), quote=True)
    table = []
    for row in rows[:100]:
        recipient = str(row.get("recipient") or "")
        table.append(f'<tr><td>{escape(recipient)}</td><td>{escape(str(row.get("reason") or ""))}</td><td>{escape(str(row.get("updated_at") or ""))}</td><td><form method="post" action="/dashboard/suppression/unsuppress"><input type="hidden" name="csrf" value="{csrf}"><input type="hidden" name="recipient" value="{escape(recipient, quote=True)}"><button type="submit">Unsuppress</button></form></td></tr>')
    add = f'''<section class="card"><h2>Add suppression</h2><form method="post" action="/dashboard/suppression/suppress"><input type="hidden" name="csrf" value="{csrf}"><div class="row"><div class="field"><label>Recipient</label><input type="email" name="recipient" required></div><div class="field"><label>Reason</label><select name="reason"><option value="manual">manual</option><option value="unsubscribe">unsubscribe</option></select></div><button class="primary" type="submit">Suppress</button></div></form></section>'''
    return _panel("suppressions", f'''{_flash(request)}<div class="v951-pagehead"><div><h2>Suppressions</h2><p>Current active suppressions.</p></div></div>{_details_wrap(add,title='Add suppression',key='suppressions-add')}<div class="scroll"><table><thead><tr><th>Recipient</th><th>Reason</th><th>Updated</th><th></th></tr></thead><tbody>{''.join(table) or '<tr><td colspan="4" class="muted">No active suppressions</td></tr>'}</tbody></table></div>''')


def _project_service(base: Any) -> ProjectService:
    return ProjectService(base.scheduler(), base.file_store())


def _project_return(**values: str) -> str:
    params = {"ui_view": "projects", **{k: v for k, v in values.items() if v}}
    return "/?" + urlencode(params) + "#projects"


def render_projects(base: Any, request: Request) -> str:
    service = _project_service(base)
    projects = active_projects(base)
    owner_rows = owners(base)
    edit_id = (request.query_params.get("edit_project") or "").strip()
    delete_id = (request.query_params.get("delete_project") or "").strip()
    selected = (request.query_params.get("project") or "").strip()
    default_owner = str(owner_rows[0].get("id") or "") if owner_rows else ""
    new_form = f'''<section class="card wide"><h2>New project</h2><p class="small muted">Project ID is suggested from Name, remains editable before creation, and becomes immutable afterward.</p><form method="post" action="/dashboard/project/create"><input type="hidden" name="csrf" value="{escape(base._csrf_value(), quote=True)}"><div class="row"><div class="field"><label>Owner</label><select name="owner_id" required>{owner_options(owner_rows, default_owner)}</select></div><div class="field"><label>Name</label><input type="text" name="name" data-v962-project-name required></div><div class="field"><label>Project ID / slug</label><input type="text" name="project_id" data-v962-project-slug pattern="[a-z0-9][a-z0-9-]{{0,63}}" required></div></div><div class="field" style="margin-top:10px"><label>Description (optional)</label><textarea name="description" rows="3"></textarea></div><button class="primary" type="submit">Create project</button></form></section>'''
    controls = [_details_wrap(new_form, title="New project", key="projects-new")]
    if edit_id:
        try:
            current = service.get(edit_id)
            edit = f'''<section class="card wide"><h2>Edit project</h2><form method="post" action="/dashboard/project/update"><input type="hidden" name="csrf" value="{escape(base._csrf_value(), quote=True)}"><input type="hidden" name="project_id" value="{escape(edit_id, quote=True)}"><div class="row"><div class="field"><label>Project ID / slug</label><input value="{escape(edit_id, quote=True)}" readonly></div><div class="field"><label>Name</label><input type="text" name="name" value="{escape(str(current.get('name') or ''), quote=True)}" required></div></div><div class="field"><label>Description</label><textarea name="description" rows="3">{escape(str(current.get('description') or ''))}</textarea></div><div class="row"><button class="primary" type="submit">Save project</button><a href="{escape(_project_return(), quote=True)}">Cancel</a></div></form></section>'''
            controls.append(_details_wrap(edit, title="Edit project", key=f"project-edit-{edit_id}", force_open=True))
        except ProjectServiceError as exc:
            controls.append(f'<div class="flash">{escape(str(exc))}</div>')
    if delete_id:
        try:
            impact = service.impact(delete_id)
            project = impact["project"]
            stats = f'''<div class="v951-metrics">{_metric('Memories',impact['memories'],'kept')}{_metric('Skills',impact['skills'],'kept')}{_metric('Files',impact['files'],'kept / unassigned')}{_metric('Jobs',impact['jobs'],'blocking ref')}{_metric('Execution profiles',impact['execution_profiles'],'blocking ref')}</div>'''
            if impact["blocked"]:
                action = f'<div class="flash"><strong>Delete blocked.</strong> {escape(str(impact["blocked_reason"]))}</div><a href="{escape(_project_return(), quote=True)}"><button type="button">Cancel</button></a>'
            else:
                action = f'''<p>Second confirmation: type <code>{escape(delete_id)}</code> exactly. Memory, Skill and File content will not be deleted. Items with other project scopes retain them; items with no remaining project become <strong>Unassigned</strong>, never Global.</p><form method="post" action="/dashboard/project/delete"><input type="hidden" name="csrf" value="{escape(base._csrf_value(), quote=True)}"><input type="hidden" name="project_id" value="{escape(delete_id, quote=True)}"><div class="row"><div class="field"><label>Type project_id</label><input name="confirm_project_id" autocomplete="off" required></div><button class="danger" type="submit">Delete permanently</button><a href="{escape(_project_return(), quote=True)}"><button type="button">Cancel</button></a></div></form>'''
            delete = f'''<section class="card wide v962-danger"><h2>Delete project</h2><p><strong>{escape(str(project.get('name') or delete_id))}</strong> · <code>{escape(delete_id)}</code></p>{stats}<div class="notice">Deleting this registry project is non-destructive to content. The stable project ID is never reassigned.</div>{action}</section>'''
            controls.append(_details_wrap(delete, title="Delete project", key=f"project-delete-{delete_id}", force_open=True))
        except ProjectServiceError as exc:
            controls.append(f'<div class="flash">{escape(str(exc))}</div>')
    cards = []
    for row in projects:
        pid = str(row.get("id") or "")
        open_url = dashboard_url(request, tab="projects", project=pid)
        edit_url = _project_return(edit_project=pid)
        delete_url = _project_return(delete_project=pid)
        cards.append(f'''<section class="card"><div class="panel-title"><h2>{project_label_html(str(row.get('name') or pid),pid)}</h2><div class="row"><a href="{escape(open_url, quote=True)}"><button type="button">Open</button></a><a href="{escape(edit_url, quote=True)}"><button type="button">Edit</button></a><a href="{escape(delete_url, quote=True)}"><button class="danger" type="button">Delete</button></a></div></div><div class="small muted mono">{escape(str(row.get('owner_id') or ''))} / {escape(pid)}</div><p>{escape(str(row.get('description') or ''))}</p></section>''')
    overview = ""
    if selected:
        overview = project_overview_fragment(BoundedBaseProxy(base), request)
        prefix = '<section class="tab-panel" id="panel-projects" data-panel="projects">'
        if overview.startswith(prefix) and overview.endswith('</section>'):
            overview = overview[len(prefix):-len('</section>')]
    return _panel("projects", f'''{_flash(request)}<div class="v951-pagehead"><div><h2>Projects</h2><p>Persistent registry CRUD. Slugs are stable identifiers and cannot be edited.</p></div><span class="badge">{len(projects)} active</span></div>{''.join(controls)}<div class="v951-grid">{''.join(cards) or '<section class="card"><span class="muted">No active projects</span></section>'}</div>{overview}''')


async def dashboard_project_create(base: Any, request: Request):
    form, error = await base._verified_form(request)
    if error:
        return error
    try:
        name = str(form.get("name") or "").strip()
        project_id = str(form.get("project_id") or "").strip() or slugify_project_name(name)
        _project_service(base).create(
            owner_id=str(form.get("owner_id") or "").strip(),
            project_id=project_id,
            name=name,
            description=str(form.get("description") or ""),
        )
        invalidate_structural_cache("projects")
        message = f"Project {project_id} created"
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
    return RedirectResponse(_project_return(flash=message), status_code=303)


async def dashboard_project_update(base: Any, request: Request):
    form, error = await base._verified_form(request)
    if error:
        return error
    project_id = str(form.get("project_id") or "").strip()
    try:
        _project_service(base).update(
            project_id=project_id,
            name=str(form.get("name") or ""),
            description=str(form.get("description") or ""),
        )
        invalidate_structural_cache("projects")
        message = f"Project {project_id} updated"
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
    return RedirectResponse(_project_return(flash=message), status_code=303)


async def dashboard_project_delete(base: Any, request: Request):
    form, error = await base._verified_form(request)
    if error:
        return error
    project_id = str(form.get("project_id") or "").strip()
    try:
        result = _project_service(base).delete(
            project_id=project_id,
            confirmation=str(form.get("confirm_project_id") or ""),
        )
        invalidate_structural_cache("projects")
        message = (
            f"Project {project_id} deleted from registry; content deleted=0, "
            f"unassigned knowledge={result.get('unassigned', 0)}, files={result.get('files_unassigned', 0)}"
        )
    except (ProjectDeletionBlocked, ProjectServiceError) as exc:
        message = str(exc)
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
    return RedirectResponse(_project_return(flash=message), status_code=303)


def render_view(base: Any, core: Any, request: Request, view: str) -> str:
    if view not in VIEWS:
        raise ValueError(f"Unknown dashboard view: {view}")
    proxy = BoundedBaseProxy(base)
    if view == "overview":
        return render_overview(base, request)
    if view == "accounts":
        return render_accounts(base, request)
    if view == "mail-health":
        return v960.render_mail_health(proxy, request)
    if view == "inbox":
        # install_webgui_v961 patched v960.render_inbox to preserve the Safe Reader and
        # bounded 26/51/76/100 prefetch contract.
        return v960.render_inbox(proxy, request)
    if view == "compose":
        return v960.render_compose(proxy, request)
    if view == "tracking":
        return render_tracking(base, core, request)
    if view == "deliveries":
        return render_deliveries(proxy, request)
    if view == "suppressions":
        return render_suppressions(base, request)
    if view == "security":
        return v951.render_security(proxy, request)
    if view == "system":
        return v951.render_system(proxy, request)
    if view == "coverage":
        return v951.render_coverage(proxy, request)
    if view == "amp":
        return render_amp(base, request)
    if view == "domains":
        return render_domains(base, request)
    if view == "recipients":
        return render_recipients(base, request)
    if view == "projects":
        return render_projects(base, request)
    if view == "knowledge":
        html = v960.knowledge_fragment(proxy, request)
        heading = "Edit knowledge item" if (request.query_params.get("edit_knowledge") or "").strip() else "Add memory / skill"
        return _wrap_section_by_heading(html, heading, key="knowledge-editor", force_open=bool((request.query_params.get("edit_knowledge") or "").strip()))
    if view == "files":
        html = files_fragment(proxy, request)
        return _wrap_section_by_heading(html, "Upload file", key="files-upload")
    if view == "scheduler":
        html = task_fragment(proxy, request)
        return _wrap_section_by_heading(html, "Edit task", key="task-editor", force_open=bool((request.query_params.get("edit_job") or "").strip()))
    raise AssertionError(view)
