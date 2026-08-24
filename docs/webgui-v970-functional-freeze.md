# WebGUI v9.7.0 functional freeze

Baseline: protected `main` at `0c50bea06531320e14fb31ca4767c695b6db0f10`, release/tag `v9.6.9`, MCP command surface `97`.

This document is a pre-redesign guardrail. The v9.7.0 WebGUI refresh may change presentation, layout, responsive behavior, accessibility markup, visual navigation composition, table/inspector composition, and non-semantic micro-interactions only. It must not change the routes, methods, payload semantics, authorization requirements, side effects, or backend calls below.

## Current action map

| Current WebGUI action | Backend route/call | Input / payload | Existing side effect | v9.7.0 UI control |
| --- | --- | --- | --- | --- |
| Load shell / switch section | `GET /`, `GET /dashboard/view/{view}` -> existing lazy fragment renderers | query/hash (`ui_view` and current filters) | Read-only rendering; fragment load only | Enterprise sidebar / mobile bottom navigation, same `data-v962-nav` targets |
| Account save | `POST /dashboard/account/save` -> existing account-store handler | Existing account form + CSRF | Persist account settings | Dense account editor, same form/action/fields |
| Account test | `POST /dashboard/account/test` | Existing account id + CSRF | IMAP/SMTP connection test only | Row/inspector action, same form |
| Set default account | `POST /dashboard/account/default` | Existing account id + CSRF | Persist default selection | Row action, same form |
| Delete account | `POST /dashboard/account/delete` | Existing account id + CSRF | Existing account deletion semantics | Destructive row/inspector action, same form |
| AMP account state | `POST /dashboard/amp/state` | Existing AMP state fields + CSRF | Existing account AMP state mutation | Existing controls restyled only |
| Add/remove domain authorization | `POST /dashboard/domain/add`, `POST /dashboard/domain/remove` | Existing domain/note + CSRF | Existing authorization-store writes | Security/policy table actions, same forms |
| Add/remove recipient authorization | `POST /dashboard/recipient/add`, `POST /dashboard/recipient/remove` | Existing recipient/note + CSRF | Existing authorization-store writes | Security/policy table actions, same forms |
| Inbox/Sent filter/list/detail | `GET /dashboard/view/inbox` and legacy fragment `GET /dashboard/inbox/fragment` -> current v9.6.9 Inbox renderer | Existing account/mailbox/search/page/message query params | Read-only except the already-existing successful received-detail Seen boundary | Desktop list + reader + contextual privacy inspector; mobile list -> full-screen detail |
| Explicit mailbox refresh | `POST /dashboard/inbox/refresh` | Existing account/mailbox + CSRF | Existing cache/mailbox refresh | Toolbar refresh action, same form |
| Safe Email -> Full HTML confirmation | `POST /dashboard/inbox/full-html` -> shared v9.6.9 passive-content service | Existing account/mailbox/uid, optional existing `refresh_remote=1`, CSRF | Only the existing explicitly authorized passive-resource fetch/cache cycle | Privacy inspector warning/confirm or retry control, same form and two-step semantics |
| Cached passive resource render | `GET /dashboard/inbox/resource` | Existing opaque cache key | Read-only cache response | No new control; reader consumes existing endpoint |
| Reply/follow-up draft from selected message | `POST /dashboard/inbox/draft` | Existing thread fields + composer fields + CSRF | Existing draft operation | Full-screen/desktop composer surface, same payload |
| Compose send / reply / follow-up / draft | `POST /dashboard/compose/send` -> current v9.6.4 send wrapper and existing mail client methods | Existing `thread_mode`, `compose_action`, To/Cc/Bcc, subject/body/body_html/body_amp, stored file ids, tracking/newsletter/unsubscribe/DSN/idempotency/force fields, account/thread identifiers, CSRF | Existing send/draft semantics including suppression confirmation, recipient resolution, threading, canonical Sent archive and idempotency boundaries | Redesigned composer only; no field or semantic additions |
| Spam / not-spam | `POST /dashboard/mail/spam`, `POST /dashboard/mail/not-spam` | Existing account/mailbox/uid + CSRF | Existing mailbox move/flag behavior | Reader actions, same forms |
| Tracking / Deliveries drill-down | Existing read-only tracking renderer -> current analytics store methods | Existing account/window/campaign/delivery filters | Read-only analytics queries | Metric strip + first-class tables + inspector; no analytics semantic changes |
| Knowledge save/delete/reindex | `POST /dashboard/knowledge/save`, `POST /dashboard/knowledge/delete`, `POST /dashboard/knowledge/reindex` | Existing type/id/title/content/metadata/scope/priority/enabled/always_include fields + CSRF | Existing Knowledge CRUD/reindex semantics and revision behavior | List + editor/inspector composition, same forms/contracts |
| Knowledge lazy refresh | `GET /dashboard/knowledge/fragment` and `GET /dashboard/view/knowledge` | Existing filters/query params | Read-only render/search | Same search/filter inputs in dense toolbar |
| Project create/update/delete | `POST /dashboard/project/create`, `/dashboard/project/update`, `/dashboard/project/delete` | Existing project fields + CSRF | Existing project-store semantics | Project table/inspector controls, same forms |
| File upload/delete/download | `POST /dashboard/files/upload`, `POST /dashboard/files/delete`, `GET /dashboard/files/{file_id}/download` | Existing multipart/file metadata or file id + CSRF where applicable | Existing persistent File Store writes/read | File table + inspector/actions, same forms/links |
| Task list/calendar | Existing scheduler `list_jobs` / `list_due_jobs` through Tasks renderer | Existing project/show-completed/task-view/calendar-month query params | Read-only registry views; Calendar is representation only | List/Calendar segmented view of identical registry rows |
| Task view/edit | Existing scheduler `get_job`; `POST /dashboard/job/update` -> `scheduler().update_job` | Existing job id, title, description, payload JSON, schedule type/value, timezone, approval mode + CSRF | Existing scheduler update only | Context inspector / full-screen mobile editor, same form fields |
| Pause/resume task | `POST /dashboard/job/pause`, `POST /dashboard/job/resume` | Existing job id + CSRF | Existing scheduler state transition | Table/inspector action, same forms |
| Privacy Proxy configure/test/dismiss | `POST /dashboard/privacy-proxy/configure`, `/dashboard/privacy-proxy/test`, `/dashboard/privacy-proxy/dismiss` | Existing worker URL, write-only secret, enabled/obfuscation flags or test/dismiss CSRF payload | Existing provisioning/status semantics; secrets remain server-side | Security inspector/forms, same fields and secret boundaries |
| Runtime/System/MCP Coverage views | Existing read-only renderers and status calls | Existing query/filter state | Read-only | Dense status tables/strips; no runtime selector or MCP changes |
| Tracking pixel | `GET /track/open/{token}.gif` | Existing token | Existing tracking event semantics | No WebGUI control; unchanged |
| AMP live status | `GET /api/amp/status` | Existing request | Read-only status | Existing UI consumer only |

## Frozen invariants

- MCP command count: **97 -> 97**.
- MCP schemas: no changes.
- No backend/API route additions, removals, method changes, or payload semantic changes.
- No changes to mail recipient resolution, Reply/Reply All/Forward/follow-up semantics, Bcc privacy, canonical Sent behavior, tracking definitions, Privacy Proxy behavior, Full HTML authorization/cache behavior, lifecycle approvals, scheduler execution/approval/recurrence/timezone behavior, Knowledge persistence/revisions/search contracts, File Store contracts, or database schemas/migrations.
- `requirements.txt`, `postmaster-mcp.yml`, Cloudflare Worker, scheduler engine, Knowledge engine, mail pipeline, tracking engine, persistent stores, and MCP definitions remain unchanged.
- The prototype is a visual specification only. Any simulated control without a current repository capability is omitted or adapted to an existing action.

The v9.7.0 overlay must therefore be installable after all v9.6.9 functional WebGUI overlays and must not register or replace backend routes.