# File handoff

Postmaster keeps `file_id` as the canonical identifier for every FileStore record. File handoff reuses the original SHA-256 content-addressed blob; transfer mechanisms do not create a second cache or a converted copy.

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
https://configured-public-host/t/c/sfp1_<opaque-capability>
```

Transport behavior:

- `auto`: use the opaque public HTTPS capability when a public file base can be resolved from `FILE_STORE_PUBLIC_BASE_URL` or the existing `PUBLIC_MCP_HOST`; otherwise use the MCP resource URI.
- `http`: require one of those HTTPS public-base settings and return the opaque public capability URL.
- `mcp`: always return `postmaster://files/{file_id}`.

Constructing the link reads metadata only. It does not read the stored blob. The HTTPS public URL does not contain the Stored File ID, `/files/<uuid>`, expiry parameters or a client-visible file signature.

## Terminal public Stored File capability

New public Stored File ResourceLinks use `/t/c/sfp1_<opaque-capability>`. The opaque capability is an HMAC derived from Postmaster's existing persistent file-download secret and is bound to the exact immutable FileStore record incarnation:

- `file_id`;
- immutable file SHA-256;
- immutable `created_at` value;
- a version/domain-separation string for the public capability format.

The public route is terminal:

```text
GET /t/c/<stored-file-token>
  -> validate capability locally
  -> resolve the exact FileStore incarnation locally
  -> read the canonical persistent FileStore blob
  -> return the file bytes directly
```

It does **not** redirect to `/files/<uuid>` and it does not issue an HTTP, DNS or reverse-proxy request back into Postmaster. The FileStore remains the single persistence source.

Responses use the stored MIME type, sanitized attachment disposition, `X-Content-Type-Options: nosniff` and private/no-store cache policy. `HEAD` returns metadata without a response body.

As a result:

- a public capability remains valid for as long as that exact stored-file record continues to exist;
- deleting the FileStore record makes the capability stop resolving immediately;
- deleting and later re-creating the same `file_id` does not resurrect the old capability, even if the replacement has identical bytes, because its immutable creation identity is different;
- metadata edits such as filename/description/tag changes do not invalidate the capability because they do not change the immutable record identity;
- rotating/replacing the persistent file-download HMAC secret is an emergency global revocation mechanism for previously issued public capabilities.

Possession of a valid public capability is authorization for that exact Stored File incarnation. Treat the URL as sensitive and distribute it only to the intended recipient.

## Mail click tracking and Stored Files

When a Stored File ResourceLink is inserted into tracked outbound HTML, Postmaster resolves the public capability locally and creates the existing per-delivery opaque `/t/c/<tracking-token>` occurrence with `target_type=stored_file`. The recipient never receives a Stored File ID.

Opening that tracked Stored File URL:

1. validates the tracking target and Stored File incarnation;
2. records the click through the normal tracking telemetry pipeline;
3. reads the bytes directly from the persistent FileStore;
4. returns the file directly with safe download headers.

Normal tracked HTTP/HTTPS destinations keep their historical behavior: `/t/c/<tracking-token>` records the click and then returns an HTTP redirect to the external destination.

The additive tracking-store migration records immutable Stored File SHA-256 and creation identity for newly issued Stored File tracking occurrences. Existing pre-v9.7.2 rows remain readable and fail closed when a replacement record is detectably newer than the original link.

## v9.7.1 and legacy `/files` capability compatibility

Postmaster retains the historical signed `/files/{file_id}` endpoint for compatibility. Existing durable v9.7.1 URLs have this form:

```text
GET  /files/{file_id}?expires=0&sig=<durable-capability>
HEAD /files/{file_id}?expires=0&sig=<durable-capability>
```

The v9.7.1 durable signature remains bound to the exact record incarnation (`file_id`, SHA-256 and `created_at`). Direct requests keep their existing validation and streaming/range semantics.

A v9.7.1 `/t/c/<tracking-token>` whose stored destination is a valid local `/files/...` capability is fixed forward after upgrade: Postmaster records the click, validates that legacy capability locally and serves the FileStore bytes directly. It no longer redirects the recipient to `/files/*`.

Postmaster also continues to verify the v9.3 expiring URL format so previously generated links remain usable until their original expiry:

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

Malformed or forged capabilities return `403`. A durable capability whose stored-file record has been deleted no longer resolves. The same FileStore deletion therefore controls both persistence and capability availability without introducing a second share database.

## Configuration and Cloudflare Access

The normal single-YAML deployment needs no new required environment variables. Postmaster reuses the existing public MCP hostname when available:

```text
PUBLIC_MCP_HOST
```

`FILE_STORE_PUBLIC_BASE_URL` may optionally override the public base used to construct file ResourceLinks. The public capability itself is still served at `/t/c/*` on that configured origin.

Advanced deployments may optionally override the file base or signing secret with process environment variables:

```text
FILE_STORE_PUBLIC_BASE_URL
FILE_STORE_DOWNLOAD_SECRET
```

The following historical setting remains accepted for legacy expiring capability helpers only:

```text
FILE_STORE_DOWNLOAD_URL_TTL_SECONDS
```

`FILE_STORE_PUBLIC_BASE_URL` must be an externally reachable HTTPS base URL and takes precedence over `PUBLIC_MCP_HOST`. If neither is configured, `transport=auto` falls back to the MCP resource path and `transport=http` returns a clear configuration error.

`FILE_STORE_DOWNLOAD_SECRET` may be supplied by a private deployment. When it is absent, Postmaster creates a random persistent secret at `/data/file-store-download.secret` with restrictive permissions and reuses it across restarts. Keeping that secret persistent is required for already-issued durable capabilities to survive application restarts/upgrades.

No `postmaster-mcp.yml`, Cloudflare Worker or dependency change is required for the terminal public Stored File fix. The tracking store receives only an additive internal migration for immutable Stored File incarnation binding.

Cloudflare Access does not need to be weakened. `/t/c/*` remains the intentionally public mail/click/download capability path. `/files/*`, `/mcp`, the dashboard and other protected application surfaces may remain behind Access according to the deployment policy. The new public flow never relies on an anonymous recipient reaching `/files/*`.

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
