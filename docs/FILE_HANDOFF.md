# File handoff

Postmaster keeps `file_id` as the canonical identifier for every FileStore record. File handoff must reuse the original SHA-256 content-addressed blob; transfer mechanisms do not create a second cache or a converted copy.

## Preferred file handoff hierarchy

1. native ResourceLink/file reference
2. HTTPS streaming
3. MCP `resources/read`
4. chunked/Base64 fallback
5. inline Base64 only as last resort

**Never resize, recompress or transcode an asset merely to make it transferable to the client.**

The v9.2 ChatGPT-to-Postmaster input path is unchanged: `save_uploaded_file`, `save_uploaded_files`, OpenAI `_meta["openai/fileParams"]`, safe server-side HTTPS download, portable `save_file(content_base64)` and WebGUI multipart upload continue to use the same FileStore.

## Native MCP ResourceLink

`get_stored_file_resource(file_id, transport="auto")` returns a real MCP `ResourceLink` content block rather than JSON serialized into text. The link includes the stored filename as `name`, MIME type, size, description when available, and one of these URIs:

```text
postmaster://files/{file_id}
https://configured-file-base/files/{file_id}?expires=...&sig=...
```

Transport behavior:

- `auto`: use signed HTTPS only when `FILE_STORE_PUBLIC_BASE_URL` is explicitly configured; otherwise use the MCP resource URI.
- `http`: require the dedicated HTTPS public base and return a temporary signed URL.
- `mcp`: always return `postmaster://files/{file_id}`.

Constructing the link reads metadata only. It does not read the stored blob.

## MCP resources/read

Postmaster registers this resource template:

```text
postmaster://files/{file_id}
```

Reading it returns the original verified FileStore bytes. For binary content the MCP Python SDK serializes the byte return value as protocol `BlobResourceContents`; any Base64 required by the wire protocol is therefore produced by the SDK rather than generated and reinserted manually by the model.

## Signed HTTPS streaming

The dedicated endpoint is:

```text
GET  /files/{file_id}?expires=<unix>&sig=<hmac>
HEAD /files/{file_id}?expires=<unix>&sig=<hmac>
```

A UUID/file_id alone is not authorization. The HMAC signature binds at least `file_id` and `expires`, expiry is mandatory, and verification uses constant-time comparison. Invalid or expired capabilities return `403`; a valid capability for a missing file returns `404`.

The HTTP path opens the canonical content-addressed FileStore blob directly. It does not Base64-encode, resize, recompress, transcode or create a temporary transfer copy.

Responses include the stored MIME type, sanitized attachment disposition, `X-Content-Type-Options: nosniff`, private/no-store cache policy and byte-range support. `HEAD` returns metadata without a body. A valid single `Range: bytes=...` request returns `206 Partial Content`; an invalid/unsatisfiable range returns `416` with `Content-Range: bytes */<size>`.

## Configuration

```text
FILE_STORE_PUBLIC_BASE_URL
FILE_STORE_DOWNLOAD_SECRET
FILE_STORE_DOWNLOAD_URL_TTL_SECONDS
```

`FILE_STORE_PUBLIC_BASE_URL` is intentionally separate from mail/AMP callback settings and from the general MCP host. It must be an externally reachable HTTPS base URL and is opt-in. Leaving it blank keeps `transport=auto` on the MCP resource path.

`FILE_STORE_DOWNLOAD_SECRET` may be supplied by the private deployment. When it is blank, Postmaster creates a random persistent secret at `/data/file-store-download.secret` with restrictive permissions and reuses it across restarts.

`FILE_STORE_DOWNLOAD_URL_TTL_SECONDS` defaults to 900 seconds. Runtime validation bounds generated capability lifetime and refuses URLs beyond the hard maximum of 24 hours.

The signed `/files/*` path must be reachable by the client that will consume the URL. If an external access layer protects the whole application, expose this route only according to the deployment's security policy; possession of a valid short-lived signature is the file capability. Do not broadly bypass authentication for `/mcp`, the dashboard, or unrelated routes merely to enable file download.

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
