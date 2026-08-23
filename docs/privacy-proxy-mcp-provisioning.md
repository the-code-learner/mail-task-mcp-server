# Privacy Proxy provisioning through an MCP client

Postmaster v9.6.6 adds a provider-neutral, MCP-native provisioning flow for the optional email Privacy Proxy. The goal is to let an MCP client guide the complete setup without ever receiving the generated proxy HMAC secret or the Ed25519 private signing key.

The flow reuses the existing MCP command name `set_amp_account_state`. It does not add a new MCP command name. Normal `/health` and `/fetch` traffic remains HMAC-SHA256 authenticated with timestamp and nonce replay protection. The new `/provision` path is authenticated separately with a pinned Ed25519 public key.

## Security properties

- Postmaster generates the Ed25519 keypair locally.
- The private key is encrypted at rest and never appears in MCP responses, logs, audit output, documentation examples, or error strings.
- MCP exposes only the base64url Ed25519 public key, key-id, and SHA-256 fingerprint.
- The Worker public key and key-id are explicit non-secret configuration. Missing/unpinned material fails closed; there is no unauthenticated TOFU/first-claim path.
- Postmaster generates the proxy HMAC secret locally, stores it encrypted as `pending`, and sends it directly to the Worker server-to-server.
- The provisioning signature binds method, path, Worker origin, timestamp, nonce, body digest, monotonic generation, operation, and key-id.
- Worker provisioning nonces are one-time and timestamp-bounded.
- The Worker stores active/previous HMAC secret state in the existing Durable Object. Rotation has a 120-second default grace and a 300-second hard maximum.
- Local promotion is `pending -> verify -> active`: Postmaster does not replace its active secret until `/health` succeeds with the pending secret.
- `POSTMASTER_PROXY_SECRET` remains a legacy fallback when no Durable Object provisioned secret is active.

## End-to-end wizard

The examples below show argument names, not private values. Never ask the user to paste a generated shared secret or private signing key; those values are intentionally unavailable to the MCP client.

### 1. Inspect status

Call the existing tool with the read-only action:

```text
set_amp_account_state(
  privacy_proxy_action="status"
)
```

Inspect both `privacy_proxy` and `privacy_proxy_provisioning`. The provisioning phase is one of `unprepared`, `prepared`, `pending`, or `active`. Record the configured Worker URL, current generation, pending generation (if any), key-id, and fingerprint. Status never includes private signing material or plaintext HMAC secrets.

If the state is `pending`, do not start a new provision/rotate operation. Go directly to the interruption/reconcile section.

### 2. Prepare provisioning — preview first

Start a preview. The HTTPS Worker URL may be bound during this operation:

```text
set_amp_account_state(
  privacy_proxy_action="prepare_provisioning",
  privacy_proxy_worker_url="https://worker.example.invalid"
)
```

This first call is preview-only. Show the exact action/Worker origin/generation returned in `action_preview` and obtain explicit user approval in the active chat. The returned `confirmation_token` is short-lived, one-time, and bound to that exact preview.

After explicit approval, repeat the same action and Worker URL with the exact token:

```text
set_amp_account_state(
  privacy_proxy_action="prepare_provisioning",
  privacy_proxy_worker_url="https://worker.example.invalid",
  privacy_proxy_confirm="ONE_TIME_TOKEN_FROM_PREVIEW"
)
```

Only now does Postmaster generate/persist the Ed25519 keypair. The response must contain public material only:

- `public_key`
- `key_id`
- `fingerprint`

A compliant client must never display or request a private key or generated proxy HMAC secret because Postmaster never returns them.

### 3. Pin the public key in the Worker

Configure the Worker with the returned public material as non-secret settings:

```text
POSTMASTER_PROVISIONING_PUBLIC_KEY = <public_key>
POSTMASTER_PROVISIONING_KEY_ID = <key_id>
```

Use the fingerprint to let the user verify that the pinned public key is the one prepared by Postmaster. Apply/deploy this Worker configuration before attempting `provision`.

This configuration step is intentionally outside the secret-provisioning request. The trust anchor exists before `/provision` is accepted, so there is no first-claim bootstrap.

### 4. Preview provision

Call:

```text
set_amp_account_state(
  privacy_proxy_action="provision"
)
```

The first call must not generate/send a secret. Show the preview to the user, including Worker origin, key fingerprint/key-id, operation, and proposed generation. Obtain explicit approval.

### 5. Explicitly confirm provision

Retry with the one-time token from the preview:

```text
set_amp_account_state(
  privacy_proxy_action="provision",
  privacy_proxy_confirm="ONE_TIME_TOKEN_FROM_PREVIEW"
)
```

Postmaster then performs the sensitive work internally:

1. generate a random HMAC secret;
2. encrypt and persist it as `pending` with the proposed generation;
3. serialize the provisioning body;
4. sign method/path/origin/timestamp/nonce/body digest/generation/operation/key-id with the encrypted-at-rest Ed25519 private key;
5. POST directly to the Worker `/provision` endpoint with redirects disabled;
6. never include the plaintext secret/private key in the MCP response or error message.

The Worker rejects missing/unpinned public keys, bad signatures, stale timestamps, replayed nonces, wrong key-id/origin/generation, rollback/out-of-order generations, and conflicting same-generation requests.

### 6. Health verification and promotion

After the Worker accepts the pending secret, Postmaster immediately calls `/health` with the same HMAC/timestamp/nonce contract used by normal proxy requests. If health verification succeeds, Postmaster promotes the pending secret to local active state and clears the pending record.

Expected result:

```text
phase = active
health_verified = true
provisioned = true
```

If `/provision` or `/health` fails, the response contains a bounded error code/status only. It does not contain the generated secret. The pending encrypted state is retained so the operation can be reconciled.

### 7. Enable the proxy

Provisioning and enabling are intentionally separate. After the MCP-native state is active and health-verified, use the existing setting to enable the proxy:

```text
set_amp_account_state(
  privacy_proxy_enabled=true
)
```

Tracking obfuscation and high-noise decoy traffic remain independent persisted policies. High-noise remains explicit opt-in and requires tracking obfuscation.

## Rotation

Rotation is preview-first and uses a monotonic generation.

Preview:

```text
set_amp_account_state(
  privacy_proxy_action="rotate"
)
```

After showing the exact preview and obtaining explicit approval, confirm with the returned one-time token:

```text
set_amp_account_state(
  privacy_proxy_action="rotate",
  privacy_proxy_confirm="ONE_TIME_TOKEN_FROM_PREVIEW"
)
```

Postmaster generates a new pending HMAC secret and sends it directly to the Worker. The Worker installs the new generation and keeps the immediately previous secret only for the bounded grace period. Postmaster promotes the new local secret only after `/health` verifies it.

Generation rollback, skipped/out-of-order generations, and same-generation requests containing a different secret are rejected.

## Reconcile after an interruption

Always inspect status first. If provisioning shows `pending`, the encrypted pending secret and generation are still recoverable locally.

Preview:

```text
set_amp_account_state(
  privacy_proxy_action="reconcile"
)
```

Show the preview and obtain explicit approval, then confirm:

```text
set_amp_account_state(
  privacy_proxy_action="reconcile",
  privacy_proxy_confirm="ONE_TIME_TOKEN_FROM_PREVIEW"
)
```

`reconcile` does not generate another secret. It reuses the same encrypted pending secret/generation, signs a fresh request with a fresh timestamp/nonce, and verifies health again. If the Worker had already installed that exact generation/secret before the interruption, the same-generation operation is idempotently accepted. If the Worker had not installed it, the normal next-generation rule applies.

Do not run `rotate` while a pending operation exists. Reconcile the pending state first.

## Deprovision

Deprovision is also preview-first and consumes the next monotonic generation.

Preview:

```text
set_amp_account_state(
  privacy_proxy_action="deprovision"
)
```

After explicit approval:

```text
set_amp_account_state(
  privacy_proxy_action="deprovision",
  privacy_proxy_confirm="ONE_TIME_TOKEN_FROM_PREVIEW"
)
```

The signed Worker request clears the Durable Object provisioned active/previous secret state. Postmaster clears its active/pending MCP-native secret state and disables the proxy locally. Public Ed25519 material remains available for a future explicitly approved provision unless a separate future release adds key replacement semantics.

A legacy `POSTMASTER_PROXY_SECRET` configured independently on the Worker remains a legacy fallback capability; MCP-native deprovision invalidates the MCP-native provisioned state, not unrelated external configuration.

## What to do when the flow stops midway

- **Before prepare confirmation:** nothing has changed; request a new preview if the token expires.
- **After prepare but before Worker pinning:** public key material is safe to re-read from status. Pin it before provisioning.
- **After Worker pinning but before provision confirmation:** no HMAC secret has been generated/sent yet. Start a new provision preview if needed.
- **After provision/rotate confirmation with `pending`:** do not create another secret. Inspect status, then preview and explicitly confirm `reconcile`.
- **After active health verification but before enable:** provisioning succeeded; enabling remains a separate explicit setting change.
- **After an expired/used/mismatched confirmation token:** request a new preview. Tokens are never reusable and approval does not carry across a changed action/target/generation.

## Legacy manual secret compatibility

The pre-v9.6.6 `privacy_proxy_secret` MCP argument remains write-only for backwards-compatible deployments, and the Worker still supports `POSTMASTER_PROXY_SECRET`. These compatibility paths are not the recommended MCP-native provisioning mechanism because a manually supplied shared secret necessarily exists outside the new server-to-server flow.

Do not remove legacy support when upgrading an existing deployment unless that is a separately planned migration.

## Release versus production activation

Publishing a Postmaster source release that contains this functionality does not deploy/update the Worker, pin a public key, create a production secret, restart Postmaster, or enable the proxy. Source release and production activation are intentionally separate security boundaries.
