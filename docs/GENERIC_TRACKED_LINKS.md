# Generic tracked links (v9.8.2)

Postmaster v9.8.2 exposes its existing click-redirect capability as a generic MCP resource, independent from email delivery.

A generic tracked link stores an HTTP/HTTPS destination on the Postmaster server and returns a complete opaque public URL under the existing callback family:

```text
https://<PUBLIC_POSTMASTER_HOST>/t/c/glnk_<opaque-random-token>
```

When that URL is opened, Postmaster records a click event and issues a `302` redirect to the destination stored server-side.

The destination is never accepted from the public request/query string, so the public callback cannot be turned into a query-controlled open redirect.

## Why this is separate from email tracking

Existing email click tracking is delivery/campaign/recipient aware and remains unchanged.

v9.8.2 adds separate additive tables for generic links instead of manufacturing synthetic email campaigns or deliveries:

- `generic_tracking_links`
- `generic_tracking_clicks`

The two systems share the public `/t/c/*` callback family and privacy-preserving click metadata pipeline, but their persistence and MCP semantics remain distinct.

Existing email and Stored File tracking tokens continue through the historical route implementation without schema or behavior changes.

## MCP tools

v9.8.2 adds five MCP commands.

### create_tracked_link

Creates one reusable generic tracked URL.

Required:

- `owner_id`
- `destination_url`: absolute HTTP/HTTPS destination
- `label`: human/agent-readable purpose

Optional:

- `project_id`: exact Postmaster project scope
- `idempotency_key`: stable retry key for exact create intent

Example intent:

```text
owner_id: davide
project_id: postmaster-mcp
destination_url: https://example.com/docs
label: Documentation CTA
idempotency_key: docs-cta-v1
```

The response intentionally includes the complete `tracked_url`. This is different from historical email-link listing APIs, which deliberately hide email delivery tokens.

The MCP client or LLM may then use the returned tracked URL in any user-authorized context: chat, a document, a social post, a QR payload, a message sent through another service, or another workflow.

### list_tracked_links

Lists generic tracked links inside an owner/project scope and returns each complete `tracked_url` plus aggregate click counters.

It supports:

- optional exact case-insensitive `label` filtering;
- active-only filtering;
- clicked-only filtering;
- bounded result limits.

Omitting `project_id` provides an owner-wide view after the existing owner authorization check.

### get_tracked_link

Returns one scoped generic tracked link, including:

- label and destination;
- complete tracked URL;
- status;
- total click count;
- unique fingerprint click count;
- first/last click time.

### list_tracked_link_events

Returns click events for generic tracked links, including the same privacy-conscious client metadata used by Postmaster tracking.

Raw source IP addresses are not persisted or returned.

### revoke_tracked_link

Revokes one generic tracked link.

This is an explicit destructive action and requires:

```text
confirm: true
```

Revocation preserves link metadata and click history while making the public token return `404`.

## Labels and scope

Every generic tracked link requires a non-empty label. Labels make links identifiable to humans and agents without inspecting the opaque token.

Links are always owner-scoped and can additionally be project-scoped. The MCP layer reuses Postmaster's established owner/project registry authorization.

## Idempotency

A non-empty `idempotency_key` is bound to the exact owner/project + destination + label intent.

Retrying the same intent returns the existing tracked link. Reusing the same key with a different destination or label fails closed.

## Privacy and click identity

Generic click events reuse Postmaster's existing keyed fingerprint pipeline.

The generic unique-click definition is:

```text
tracked_link_id + client_fingerprint
```

No raw IP column is added to the generic click table.

As with email analytics, a click event proves that the tracked URL was requested. It does not by itself prove the identity or intent of a human user; browsers, security scanners, proxies and automated clients can request links.

## Public callback / Cloudflare Access

v9.8.2 introduces **no new anonymous route**.

Generic links use the same existing callback family:

```text
GET /t/c/*
```

If a deployment already exposes `/t/c/*` for email click tracking or Stored File public handoff, no additional callback bypass is needed.

If `/t/c/*` is currently protected by an interactive Cloudflare Access login, generic tracked links will not be usable by arbitrary external recipients until the operator intentionally makes that narrow callback family reachable.

Keep the rest of Postmaster protected. In particular, do not expose `/mcp`, the dashboard or administrative APIs merely to enable generic links.

## Security properties

- Tokens are random and opaque.
- Generic tokens use the dedicated `glnk_` prefix so legacy callback types remain unambiguous.
- The public request cannot supply or override the redirect destination.
- Only active links resolve; revoked links return `404`.
- A same-origin Postmaster tracked URL is rejected as a new destination, preventing accidental double wrapping.
- Telemetry/history is preserved across revocation.
- Email tracking, Sent-clean behavior and Stored File public capabilities are unchanged.

## Deployment

The feature uses additive tables in the existing analytics SQLite database and the existing durable data volume.

v9.8.2 requires:

- no `postmaster-mcp.yml` change;
- no new Python dependency;
- no new public endpoint;
- no Cloudflare Worker change.

Publishing the source release does not itself activate a running Postmaster instance. Runtime activation remains a separate owner-controlled action.
