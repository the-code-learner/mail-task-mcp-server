# Link tracking and clean Sent copies (v9.4)

Postmaster v9.4 adds per-link click telemetry to the existing per-recipient analytics pipeline. The existing open-tracking pixel remains unchanged: its route, event format, enrichment, client fingerprint, country/source, browser/OS parsing, campaign/delivery correlation and dashboard continue to work as before.

## Recipient versus Sent architecture

```text
canonical message
        |
        +-----------------------------+
        |                             |
        v                             v
recipient variant                Sent variant
existing tracking pixel          no recipient pixel
HTTP/HTTPS -> /t/c/<token>        original HTTP/HTTPS URLs
recipient AMP callback            no recipient AMP callback part
        |                             |
        +-------- MIME build ----------+
```

Tracking instrumentation belongs to the recipient delivery, not to the sender's archived Sent copy. v9.4 does not rewrite historical Sent messages.

Link tracking follows the existing `track_opens` tracking opt-in (including the account default used by existing send/reply tools), so v9.4 does not add a second required send parameter.

## Public click endpoint

The only new public callback path required by v9.4 is:

```text
GET /t/c/<token>
```

The public base is resolved exactly like existing mail callbacks: `PUBLIC_EMAIL_BASE_URL`, otherwise `https://<PUBLIC_MCP_HOST>`.

The token is random and opaque; it does not encode recipient data or the destination. `/t/c/<token>` resolves a server-side `tracking_links` record, records a `link` event and issues a `302` to the stored `original_url`. Request/query parameters are never used as the destination, so `?url=https://...` cannot create an open redirect. Invalid tokens return `404` without a destination hint.

## HTML rewriting

Recipient HTML rewrites eligible `http://` and `https://` anchors only. Postmaster does not rewrite `mailto:`, `tel:`, `cid:`, `data:`, `javascript:`, `#fragment` or other non-web URLs. A URL already pointing to this deployment's `/t/c/` path is not wrapped twice. A legitimate third-party URL whose path happens to contain `/t/c/` remains eligible.

The original href is stored after normal HTML entity decoding, preserving query and fragment semantics. For example `https://example.com/page?a=1&amp;b=2#section` is stored/redirected as `https://example.com/page?a=1&b=2#section`. Visible anchor text is unchanged. The original anchor index is retained, so repeated header/footer occurrences remain distinguishable while `normalized_url` supports destination aggregation.

## Additive analytics schema

Existing pixel tables are not replaced. v9.4 adds:

`tracking_links`: `id`, `link_id`, random `tracking_token`, `campaign_id`, `delivery_id`, `account_id`, `recipient`, `message_id`, `original_url`, `normalized_url`, `destination_host`, `position`, `anchor_text`, `created_at`.

`tracking_clicks`: `id`, `link_occurrence_id`, `link_id`, `delivery_id`, `campaign_id`, `account_id`, `recipient`, `observed_at`, `event_type=link`, `user_agent`, `client_fingerprint`, `country_code`, `browser`, `os`, `client_source`, `metadata_confidence`.

The click fingerprint reuses the existing analytics HMAC derivation; raw IP is not stored. Country/source/browser/OS parsing reuses the pixel enrichment helpers. The browser field continues to contain the parsed browser/version label used by the existing pipeline.

## Unique click

A v9.4 unique click is:

```text
delivery_id + link_id + client_fingerprint
```

If both IP and User-Agent are unavailable, the existing keyed fingerprint pipeline produces the stable HMAC of the empty pair; repeated unknown fetches for that delivery/link therefore collapse consistently. v9.4 does not classify human/bot/scanner clicks. User-Agent, source and fingerprint are retained for a future evidence-based classifier.

## Analytics, MCP and dashboard

Available analytics include total clicks, unique clicks, unique recipients, first/last click, campaign/delivery/link filtering, event detail, top links and destination host.

Existing `tracking_status` and `get_tracking_campaign` are extended. v9.4 adds read-only `get_tracking_summary`, `list_tracking_links` and `list_tracking_events`. Listing/event tools never return opaque click tokens.

The existing pixel dashboard is preserved. v9.4 adds **Top links** and a unified **Tracking events** table distinguishing `pixel`, `amp_xhr` and `link`; link rows include label, `link_id`, destination host and position.

## Clean Sent behavior and historical finding

Pre-v9.4, the individualized `EmailMessage` sent via SMTP was also serialized directly for IMAP APPEND to Sent. Therefore a tracked Sent copy could contain the recipient pixel and create a false self-open when the sender viewed it.

v9.4 builds outbound and Sent MIME independently from the same canonical body/attachment inputs. Outbound keeps the existing pixel and tracked URLs. Sent keeps original URLs, no active recipient pixel, no `/t/c/<token>` and no recipient AMP callback alternative. `Message-ID` and `Date` are synchronized; `Subject`, `In-Reply-To` and `References` come from the same canonical inputs. Attachment specs are reused and regression-tested for byte identity. No regex rewriting is performed on a serialized MIME blob.

This is the primary self-open/self-click defense; Postmaster does not guess sender identity from IP, country, User-Agent, browser or fingerprint.

## Cloudflare Access — manual deployment requirement

Keep Postmaster protected by default. Existing public callback paths remain:

```text
/track/open/*
/api/amp/*
```

v9.4 adds exactly one new required public path:

```text
/t/c/*
```

**Cloudflare Access must bypass `/t/c/*`.** Without this bypass a recipient reaches the Access login/challenge instead of Postmaster and the redirect fails.

Keep `/mcp`, dashboard/admin/private APIs, mail/task/memory/skill/file-management routes and tracking analytics protected. Do not disable Access globally.

### Separate v9.3 file-handoff note

`/files/{file_id}` is a pre-existing v9.3 signed HTTP file-handoff route. v9.4 does not add `/files/*` to its Cloudflare bypass or change that policy. If a deployment intentionally uses signed HTTP file handoff, the operator must separately ensure that pre-existing route is reachable according to the deployment's proxy policy.

## Live preflight

After release/deploy and after the operator adds the `/t/c/*` bypass:

1. `build_status` reports `9.4.0`, `link_tracking=true`, `sent_copy_tracking_sanitized=true`.
2. Send a tracked email with at least two distinct HTTP/HTTPS URLs.
3. Recipient MIME contains the unchanged `/track/open/...gif` pixel and distinct `/t/c/<token>` URLs.
4. Sent MIME contains neither recipient pixel nor `/t/c/`, and contains the original URLs.
5. Anonymous `GET https://<PUBLIC_HOST>/t/c/<valid-token>` reaches Postmaster without Cloudflare login, records a `link` event and redirects to the exact stored destination.
6. Invalid token plus `?url=https://evil.example/` does not redirect.
7. Link #1/#2 are independently visible in total/unique/top-link analytics with enrichment fields.
8. Clicking a URL extracted from Sent goes directly to the original destination and creates no Postmaster click event.
9. `/mcp`, dashboard and all other private paths remain protected by Cloudflare Access.

The container cannot prove Cloudflare policy by itself. v9.4 is fully operational only after the external `/t/c/*` bypass is configured and the anonymous live redirect test succeeds.
