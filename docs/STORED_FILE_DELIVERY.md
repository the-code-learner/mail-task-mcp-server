# Persistent File Store email delivery (v9.4.6)

Postmaster v9.4.6 adds two distinct ways to use a file already stored in the canonical Persistent File Store. Both keep file bytes server-side and preserve the existing single-YAML deployment contract.

## 1. MIME attachment from `file_id`

All existing email/draft APIs keep the same command names and parameters. An item in the existing `attachments` list may now use a third mutually exclusive source:

```json
{
  "file_id": "15a5b2f3-e9f6-49b1-bbd9-a25af8215350"
}
```

Optional MIME-only overrides are accepted:

```json
{
  "file_id": "15a5b2f3-e9f6-49b1-bbd9-a25af8215350",
  "filename": "customer-copy.jpg",
  "media_type": "image/jpeg"
}
```

The override never mutates Persistent File Store metadata. The runtime resolves the stored-file record, validates its owner/project scope, verifies the canonical blob, applies attachment size limits and passes the bytes directly to the common MIME builder. It does not call `get_file_base64`, `get_stored_file_resource`, or the signed `/files/*` handoff path.

The existing `content_base64` and `source_mailbox` + `source_uid` attachment modes are unchanged and may be mixed with `file_id` attachments.

## 2. Tracked public stored-file download

A stored file can be linked instead of physically attached by using an internal-only HTML href in an existing email body:

```html
<a href="postmaster-file:15a5b2f3-e9f6-49b1-bbd9-a25af8215350">Download image</a>
```

Before SMTP delivery Postmaster resolves and authorizes that file, then creates a normal per-delivery tracking-link occurrence. The recipient sees only the existing public tracking path:

```text
https://postmaster.example/t/c/<opaque-random-token>
```

The public URL never contains `file_id`, directly or reversibly. The token maps in the existing analytics database to the delivery/campaign/link occurrence and, only internally, to the stored-file record. There is no public `?file_id=` API and no generic public File Store lookup.

The same `/t/c/*` callback handles normal tracked redirects and stored-file downloads. Stored-file targets add backward-compatible columns to `tracking_links` (`target_type`, internal stored-file reference, download filename/media type, status, expiry and revocation timestamps). Migration is automatic and idempotent on existing persistent analytics databases.

Downloads use the existing `record_click` pipeline, so recipient/delivery/campaign/link association, client fingerprinting, raw event retention, unique-click semantics, and query-time provider/scanner classification remain unchanged. Stored-file targets return the canonical bytes with validated MIME type and attachment `Content-Disposition`. Unknown, revoked, expired and unavailable targets use the same non-descriptive public 404 response.

## Security and deployment

- File bytes never need to traverse MCP/model context for File Store → MIME attachment or public download.
- Public tracked URLs expose only cryptographically random opaque tokens.
- The existing `/t/c/*` public Cloudflare exposure is reused; no new anonymous path is required.
- The pre-existing signed `/files/*` handoff remains separate and is not used internally by these features.
- No new port, volume, mandatory environment variable or domain is introduced.
- `postmaster-mcp.yml` remains unchanged.

## MCP compatibility

No MCP command is added, removed or renamed. The only structured input extension is the backward-compatible `attachments[].file_id` source (with optional `filename` and `media_type` MIME-only overrides). The `postmaster-file:` href is content interpreted inside the already-existing `body_html` parameter and does not change any command signature.
