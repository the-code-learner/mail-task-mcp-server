# File handoff

Postmaster keeps `file_id` as the canonical private identifier for every FileStore record. File handoff reuses the original SHA-256 content-addressed blob; transfer mechanisms do not create a converted copy or a second File Store.

## Preferred file handoff hierarchy

1. native ResourceLink/file reference
2. HTTPS terminal capability on the public tracking path
3. MCP `resources/read`
4. chunked/Base64 fallback
5. inline Base64 only as last resort

**Never resize, recompress or transcode an asset merely to make it transferable to the client.**

The ChatGPT-to-Postmaster input path is unchanged: `save_uploaded_file`, `save_uploaded_files`, OpenAI `_meta["openai/fileParams"]`, safe server-side HTTPS download, portable `save_file(content_base64)` and WebGUI multipart upload continue to use the same FileStore.

## Native MCP ResourceLink

`get_stored_file_resource(file_id, transport="auto")` returns a real MCP `ResourceLink` content block rather than JSON serialized into text. The link includes the stored filename as `name`, MIME type, size, description when available, and one of these URIs:

```text
postmaster://files/{file_id}
https://configured-public-host/t/c/sfc1_<random-capability>
```

Transport behavior:

- `auto`: use the opaque public HTTPS capability when a public file base can be resolved from `FILE_STORE_PUBLIC_BASE_URL` or the existing `PUBLIC_MCP_HOST`; otherwise use the MCP resource URI.
- `http`: require one of those HTTPS public-base settings and return the opaque public capability URL.
- `mcp`: always return `postmaster://files/{file_id}`.

Constructing an HTTPS link reads FileStore metadata but does not read the stored blob. It creates a private capability record in the existing analytics database and returns a fresh high-entropy bearer token. The public URL contains no Stored File ID, UUID, recipient, delivery ID, SHA, `/files/<uuid>`, expiry query parameter or file-derived signature.

## DB-first terminal public Stored File capability

New public Stored File ResourceLinks use `/t/c/sfc1_<random-capability>`. The bearer token is generated randomly; it is not algorithmically derived from the Stored File. Postmaster stores only a domain-separated hash of that public bearer token for direct lookup.

The existing analytics database receives an additive private `stored_file_capabilities` table. A capability binds the private exact incarnation fields:

- `file_id`;
- immutable file SHA-256;
- immutable `created_at` value;
- status/revocation/optional expiry metadata.

The public route is DB-first and terminal:

```text
GET /t/c/<stored-file-token>
  -> hash the bearer token
  -> indexed capability lookup in the analytics DB
  -> fail closed if unknown/inactive
  -> obtain private file_id + expected sha256 + expected created_at
  -> FileStore get_info(file_id)
  -> verify exact incarnation
  -> FileStore raw_bytes(file_id)
  -> return the file bytes directly
```

An unknown/random token never triggers `list_files()`, FileStore enumeration or HMAC recomputation across files. FileStore access starts only after a successful indexed DB lookup.

The route does **not** redirect to `/files/<uuid>` and it does not issue an HTTP, DNS or reverse-proxy request back into Postmaster. Local bytes are read directly from the persistent FileStore.

Responses use the stored MIME type, sanitized attachment disposition, `X-Content-Type-Options: nosniff` and private/no-store cache policy. `HEAD` returns metadata without a response body.

As a result:

- each direct ResourceLink creation receives a new random public bearer token;
- deleting the FileStore record makes its capabilities stop resolving immediately;
- deleting and later re-creating the same `file_id` does not resurrect an old capability, even when the replacement bytes are identical, because `created_at` is part of the exact incarnation binding;
- metadata edits such as filename/description/tag changes do not change the immutable incarnation identity.

Possession of a valid public capability is authorization for that exact Stored File incarnation. Treat the URL as sensitive and distribute it only to the intended recipient.

## Mail click tracking and Stored Files

A public ResourceLink is an intermediate DB-backed capability, not the recipient-facing mail occurrence. When a Stored File link is composed into outbound HTML, Postmaster resolves the capability locally and creates a fresh existing tracking occurrence for each delivery/recipient:

```text
mail/campaign + delivery + recipient + stored-file capability
    -> /t/c/<fresh tracking occurrence token>
```

Two recipients of the same file therefore receive different `/t/c` tokens. Separate deliveries to the same recipient also receive different tokens. The recipient token contains no file ID, recipient, delivery ID or SHA.

The tracking row identifies campaign, delivery, account and recipient and references the private Stored File capability. Opening that tracked Stored File URL:

1. performs the indexed tracking-token DB lookup;
2. identifies the exact campaign/delivery/recipient occurrence;
3. records click telemetry through the normal tracking pipeline;
4. resolves the referenced Stored File capability in the DB;
5. verifies status/revocation/expiry and exact FileStore incarnation;
6. reads `raw_bytes(file_id)` directly from the persistent FileStore;
7. returns the file directly with safe download headers.

Normal tracked HTTP/HTTPS destinations keep their historical behavior: `/t/c/<tracking-token>` records the click and then returns HTTP `302` to the original external destination. Ordinary web-link normalization and instrumentation do not open the FileStore.

The migration is additive and uses the existing analytics database. It adds the private capability table plus capability/incarnation reference fields to `tracking_links`; it is initialized through the normal lazy tracking-store lifecycle, not eagerly during runtime composition.

## v9.7.1 and legacy `/files` capability compatibility

Postmaster retains the historical signed `/files/{file_id}` endpoint for compatibility. Existing durable v9.7.1 URLs have this form:

```text
GET  /files/{file_id}?expires=0&sig=<durable-capability>
HEAD /files/{file_id}?expires=0&sig=<durable-capability>
```

The v9.7.1 durable signature remains bound to the exact record incarnation (`file_id`, SHA-256 and `created_at`). Direct requests keep their existing validation and streaming/range semantics.

A v9.7.1 `/t/c/<tracking-token>` whose stored destination is a valid local `/files/...` capability is fixed forward after upgrade: Postmaster resolves the tracking row, records the click, validates that legacy capability locally and serves FileStore bytes directly. It no longer redirects the recipient to `/files/*`.

Postmaster also continues to verify the older explicitly expiring URL format so already-generated links remain usable until their original expiry:

```text
GET  /files/{file_id}?expires=<future-unix>&sig=<legacy-hmac>
HEAD /files/{file_id}?expires=<future-unix>&sig=<legacy-hmac>
```

Legacy HMAC validation still binds `file_id` and `expires`, uses constant-time comparison, rejects expired/tampered capabilities and preserves the historical hard maximum of 24 hours for deliberately generated legacy URLs. `FILE_STORE_DOWNLOAD_URL_TTL_SECONDS` remains a compatibility setting for those explicit legacy helpers only.

## MCP resources/read

Postmaster registers this resource template:

```text
postmaster://files/{file_id}
```

Reading it returns the original verified FileStore bytes. For binary content the MCP Python SDK serializes the byte return value as protocol `BlobResourceContents`; any Base64 required by the wire protocol is therefore produced by the SDK rather than generated and reinserted manually by the model.

## Historical `/files` streaming behavior

The `/files/{file_id}` path opens the canonical content-addressed FileStore blob directly. It does not Base64-encode, resize, recompress, transcode or create a temporary transfer copy.

Responses include the stored MIME type, sanitized attachment disposition, `X-Content-Type-Options: nosniff`, private/no-store cache policy and byte-range support. `HEAD` returns metadata without a body. A valid single `Range: bytes=...` request returns `206 Partial Content`; an invalid/unsatisfiable range returns `416` with `Content-Range: bytes */<size>`.

Malformed or forged historical capabilities return `403`. A durable historical capability whose stored-file record has been deleted no longer resolves.

## Configuration and Cloudflare Access

The normal single-YAML deployment needs no new required environment variables. Postmaster reuses the existing public MCP hostname when available:

```text
PUBLIC_MCP_HOST
```

`FILE_STORE_PUBLIC_BASE_URL` may optionally override the public base used to construct file ResourceLinks. The public capability itself is served at `/t/c/*` on that configured origin.

Advanced deployments may optionally override the file base or historical `/files` signing secret with process environment variables:

```text
FILE_STORE_PUBLIC_BASE_URL
FILE_STORE_DOWNLOAD_SECRET
```

The following historical setting remains accepted for legacy expiring capability helpers only:

```text
FILE_STORE_DOWNLOAD_URL_TTL_SECONDS
```

`FILE_STORE_PUBLIC_BASE_URL` must be an externally reachable HTTPS base URL and takes precedence over `PUBLIC_MCP_HOST`. If neither is configured, `transport=auto` falls back to the MCP resource path and `transport=http` returns a clear configuration error.

`FILE_STORE_DOWNLOAD_SECRET` remains relevant to historical signed `/files/*` capabilities. The v9.7.2 public `/t/c` bearer token is random and DB-backed rather than derived from that secret or from FileStore identity.

No `postmaster-mcp.yml`, Cloudflare Worker or dependency change is required for this fix. The persistent DB change is additive and internal to the existing analytics database.

Cloudflare Access does not need to be weakened. `/t/c/*` remains the intentionally public mail/click/download capability path. `/files/*`, `/mcp`, the dashboard and other protected application surfaces may remain behind Access according to deployment policy. The new public flow never relies on an anonymous recipient reaching `/files/*`.

## Compatibility fallbacks

Existing tools remain supported:

- `get_file_info`
- `read_text_file`
- `get_file_base64`
- `save_file`
- `save_text_file`
- `save_uploaded_file`
- `save_uploaded_files`
- `update_file_metadata`
- `delete_stored_file`

`get_file_base64` is retained for generic/debug clients. Applications should prefer a ResourceLink or `resources/read` so large encoded payloads do not need to pass through model text context.
