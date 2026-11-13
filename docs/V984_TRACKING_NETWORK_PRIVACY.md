# Postmaster MCP v9.8.4 — tracking network telemetry (draft)

This source-branch document describes the new persistent tracking telemetry and its
operational limits. v9.8.4 is **not a stable release** until CI, review and the
integrated WebGUI have passed acceptance. Nothing in this document authorizes a
production deploy or Portainer change.

## Data recorded

- Email opens and email link clicks: existing campaign/delivery/recipient,
  timestamp, client fingerprint, browser/OS, user-agent, and a private
  per-event network row with the **raw source IPv4/IPv6 address**, optional
  GeoIP city/region/postal code, estimated latitude/longitude/accuracy radius,
  ASN, ISP/organization, source and confidence.
- Generic tracked HTTP(S) URLs and tracked Stored Files: records the same
  network enrichment in `generic_tracking_network_v984`, linked to the
  existing `generic_tracking_clicks` event ID and tracked-link identity.
  File callbacks are **requests**, not proof of successful human downloads.
- The existing recipient and delivery event semantics are unchanged.
- Older email events whose private telemetry has already expired cannot be
  reconstructed. Older generic links did not store raw IP; past city/ASN/IP
  cannot be backfilled.

The existing 30-day automatic purge of the email private telemetry sidecar is
disabled in v9.8.4. Neither new email nor generic-network event data is
automatically deleted after a fixed interval. The explicit historical manual
purge helper remains available as an administrative primitive.

**Indefinite technical retention is not a legal retention exemption.** Operators
must establish an appropriate lawful basis and deletion/erasure procedure for
their jurisdiction, notify recipients as required, restrict access, secure DB
backups, and include raw IPs in data subject exports or deletions when
applicable. Do not advertise this as anonymous tracking.

## Local offline GeoIP database provisioning

Enrichment never makes a live external API call.

On the application volume (usually `/data`) install approved licensed
MaxMind-compatible MMDB files:

| Environment variable | Default path | Information |
|---|---|---|
| `POSTMASTER_GEOIP_CITY_DB` | `/data/GeoLite2-City.mmdb` | Country, region, city, approximate coordinates, accuracy radius |
| `POSTMASTER_GEOIP_ASN_DB` | `/data/GeoLite2-ASN.mmdb` | ASN and AS organization |
| `POSTMASTER_GEOIP_ISP_DB` | `/data/GeoIP2-ISP.mmdb` | Optional commercial ISP and organization details |

The `geoip2` Python reader is available in the application requirements, but
**no bundled proprietary MMDB** is shipped and no automatic database download
occurs. GeoIP answers report missing fields as unknown when a database is
absent or its record does not match.

Provision new/updated MMDBs using the operator's chosen compliant licensing
and file updater. After changing the database paths/content, restart the
runtime to refresh cached local readers.

## Trust and accuracy

- The input IP is the address observed by Postmaster's existing proxied
  callback handlers. GeoIP/ASN identify that **network request**, which may
  belong to Google Image Proxy, an email security scanner, or a VPN.
- Google Image Proxy opens more than 60 seconds after sending may be classified
  as likely human-triggered but are **not proof of a human reading**.
- Approximate City/coordinates do **not** establish the real recipient location.
  If unavailable, only the Cloudflare country header may be displayed.
- Keep Cloudflare Access on the dashboard, `/mcp` and all tracking analytics;
  only the established callback paths remain public. Public redirects,
  pixels and downloads **must not expose raw IP records**.
- MCP authorization remains **instance-global**, just as for other existing
  commands. Optional owner/project/account arguments are organizational filters
  rather than access-control identities. All clients with MCP access can query
  the full raw-IP event history.
- Structured client attribution and raw IPs are personal/network identifiers.
  Keep logs, exported MCP responses, backups and copied data appropriately
  protected. Avoid repeating them into public prompts, analytics exports
  without consent, or third-party systems.

## MCP

- `list_tracking_network_events`: global event listing, pagination and
  optional account/owner/project/event-kind/time filters.
- `get_tracking_network_event`: details of a specific event kind + event ID.
- `list_tracked_link_events`: existing generic-link tool, now with enriched
  network details (where captured).
- Original tracking tools remain available, unchanged.

The WebGUI lazily renders Tracking periods of 24 hours, 7/28/90 days or a
custom date range, expanding events to show raw IP, GeoIP/ASN and user-agent.
The overview timeline is a **bounded preview**; page the MCP tool for large
histories. Incoming-mail heatmaps have an explicitly limited IMAP sample,
not inferred events.

## Deployment

`postmaster-mcp.yml` remains **byte-for-byte unchanged**. v9.8.4 remains
source-only until approved merge/tag/release and separately authorized
deployment. The 9.9.0 WhatsApp work is excluded.
