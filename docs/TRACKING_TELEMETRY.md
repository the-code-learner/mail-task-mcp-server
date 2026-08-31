# Sensitive tracking telemetry and WebGUI visibility

This post-v9.7.0 workstream extends the existing open/click analytics without changing the public MCP tracking command names or response schemas.

## Data model

Raw source IP and the network-derived geography estimate are persisted in the `tracking_sensitive_telemetry` sidecar table in the existing analytics SQLite database. The historical `tracking_opens` and `tracking_clicks` rows remain unchanged, so legacy MCP list/unified responses do not acquire sensitive fields by accident.

Each sensitive row is bound to one existing open/click event ID and records:

- account and delivery scope;
- event time and telemetry capture timestamp;
- validated source IP, when available;
- country-level geography estimate from the existing edge/network country signal;
- the estimate source and confidence.

No external geolocation service is contacted. A country signal is an estimate and may represent a mailbox provider, proxy, scanner or relay rather than the recipient.

## Retention

Sensitive sidecar rows are retained for **30 days**. The retention pass runs at runtime installation and after new sensitive telemetry is recorded. When the sidecar expires, the base event, keyed HMAC fingerprint and aggregate counts remain available for historical analytics.

This is deliberate data minimization: raw IP/geography is short-lived, while the already-existing pseudonymous fingerprint supports longer-lived repeat-event analysis.

## Authorization and visibility

Sensitive fields are not added to the public MCP tracking list/unified response shapes. They are read only by the authenticated WebGUI using a mandatory selected `account_id` scope.

The Inbox exposes an account-level Tracking action with recent activity. Sent message detail correlates tracking by the cached RFC `Message-ID`, not by subject guessing.

The expanded WebGUI event view can show subject, recipient, event time/count, exact clicked URL, keyed fingerprint, persisted source IP, capture timestamp, estimated classification and geography estimate.

## Human-vs-machine estimate

Click events reuse the existing reversible query-time provider classifier. Open/AMP events use a conservative qualitative estimate based on existing proxy/client-source evidence. Labels are deliberately probabilistic (`likely_*`, `uncertain`, `human_or_unclassified`) and never claim that a remote image fetch proves a human read.

## Logging

New telemetry code does not log raw source IP, geolocation values, fingerprint keys or private signing material. Failures in the read-only tracking enrichment layer do not make Inbox/Sent unusable.

## Deployment boundary

This workstream is source/runtime behavior only. It does not modify `postmaster-mcp.yml`, requirements, Worker code, public MCP command names or deployment state.
