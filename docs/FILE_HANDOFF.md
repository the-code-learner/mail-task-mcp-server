# File handoff

Postmaster keeps `file_id` as the canonical identifier for every FileStore record. File handoff reuses the original SHA-256 content-addressed blob; transfer mechanisms do not create a second cache or a converted copy.

## Preferred file handoff hierarchy

1. native ResourceLink/file reference
2. HTTPS streaming
3. MCP `resources/read`
4. chunked/Base64 fallback
5. inline Base64 only as last resort

**Never resize, recompress or transcode an asset merely to make it transferable to the client.**

The ChatGPT-to-Postmaster input path is unchanged: `save_uploaded_file`, `save_uploaded_files`, OpenAI `_meta["openai/fileParams"]`, safe server-side HTTPS download, portable `save_file(content_base64)` and WebGUI multipart upload continue to use the same FileStore.

## Native MCP ResourceLink

`get_stored_file_resource(file_id, transport="auto")` returns a real MCP `ResourceLink` content block rather than JSON serialized into text. The link includes the stored filename as `name`, MIME type, size, description when available, and one of these URIs:

```text
postmaster://files/{file_id}
https://configured-public-host/files/{file_id}?expires=0&sig=<durable-capability>
```

Transport behavior:

- `auto`: use durable HTTPS when a public file base can be resolved from `FILE_STORE_PUBLIC_BASE_URL` or the existing `PUBLIC_MCP_HOST`; otherwise use the MCP resource URI.
- `http`: require one of those HTTPS public-base settings and return a durable capability URL.
- `mcp`: always return `postmaster://files/{file_id}`.

Constructing the link reads metadata only. It does not read the stored blob.

## Durable HTTPS capability

Newly generated public links use `expires=0` as the versioned no-expiry sentinel. This does **not** mean that a UUID/file ID alone authorizes access. The `sig` parameter is still an HMAC capability derived from Postmaster's persistent file-download secret.

The durable signature is bound to the exact stored-file record incarnation:

- `file_id`;
- immutable file SHA-256;
- immutable `created_at` value;
- a version/domain-separation string for the durable capability format.

As a result:

- a newly generated share/download URL remains valid for more than 30 days and, by design, for as long as that exact stored-file record continues to exist;
- deleting the FileStore record makes the durable URL stop resolving;
- deleting and later re-creating the same `file_id` does not resurrect the old capability, even if the replacement has identical bytes, because its immutable creation identity is different;
- metadata edits such as filename/description/tag changes do not invalidate the capability because they do not change the immutable record identity;
- rotating/replacing the persistent file-download HMAC secret is an emergency global revocation mechanism for previously issued capabilities.

Possession of a valid durable URL is the authorization capability. Treat it as sensitive and distribute it only to the intended recipient. The URL is unguessable without the server-side HMAC secret; the underlying File Store remains non-discoverable through this route.

## Legacy expiring URLs

Postmaster continues to verify the v9.3 expiring URL format so previously generated links remain usable until their original expiry:

```text
GET  /files/{file_id}?expires=<future-unix>&sig=<legacy-hmac>
HEAD /files/{file_id}?expires=<future-unix>&sig=<legacy-hmac>
```

Legacy HMAC validation still binds `file_id` and `expires`, uses constant-time comparison, rejects expired/tampered capabilities and preserves the historical hard maximum of 24 hours for deliberately generated legacy URLs.

New `get_stored_file_resource(..., transport="http"|"auto")` links no longer use the short-lived legacy lifetime. `FILE_STORE_DOWNLOAD_URL_TTL_SECONDS` is retained only for backwards-compatible legacy code paths and should not be used as the normal asynchronous sharing mechanism.

## MCP resources/read

Postmaster registers this resource template:

```text
postmaster://files/{file_id}
```

Reading it returns the original verified FileStore bytes. For binary content the MCP Python SDK serializes the byte return value as protocol `BlobResourceContents`; any Base64 required by the wire protocol is therefore produced by the SDK rather than generated and reinserted manually by the model.

## HTTPS streaming behavior

The `/files/{file_id}` path opens the canonical content-addressed FileStore blob directly. It does not Base64-encode, resize, recompress, transcode or create a temporary transfer copy.

Responses include the stored MIME type, sanitized attachment disposition, `X-Content-Type-Options: nosniff`, private/no-store cache policy and byte-range support. `HEAD` returns metadata without a body. A valid single `Range: bytes=...` request returns `206 Partial Content`; an invalid/unsatisfiable range returns `416` with `Content-Range: bytes */<size>`.

Malformed or forged capabilities return `403`. A durable capability whose stored-file record has been deleted no longer resolves and returns `404`. The same FileStore deletion therefore controls both persistence and durable public availability without introducing a second share database.

## Configuration

The normal single-YAML deployment needs no new required environment variables. Postmaster reuses the existing public MCP hostname when available:

```text
PUBLIC_MCP_HOST
```

Because `/files/*` is served by the same application, `PUBLIC_MCP_HOST=postmaster.example.com` resolves to the HTTPS base `https://postmaster.example.com`. This avoids coupling file delivery to `PUBLIC_EMAIL_BASE_URL`, which remains dedicated to mail/AMP callbacks.

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

`FILE_STORE_DOWNLOAD_SECRET` may be supplied by a private deployment. When it is absent, Postmaster creates a random persistent secret at `/data/file-store-download.secret` with restrictive permissions and reuses it across restarts. Keeping that secret persistent is required for already-issued durable capability URLs to survive application restarts/upgrades.

No `postmaster-mcp.yml`, Cloudflare Worker, dependency or new database schema change is required for durable links. The capability is derived from existing FileStore metadata and the existing persistent file-download secret.

The signed `/files/*` path must be reachable by the client that will consume the URL. If an external access layer protects the whole application, expose this route only according to the deployment's security policy; possession of a valid capability is the file authorization. Do not broadly bypass authentication for `/mcp`, the dashboard, or unrelated routes merely to enable file download.

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
