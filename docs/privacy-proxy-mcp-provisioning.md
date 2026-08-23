# Privacy Proxy provisioning through an MCP client

Postmaster v9.6.7 keeps the v9.6.6 Ed25519/server-to-server provisioning model and moves the lifecycle-sensitive MCP flow onto stable, dedicated commands:

- `privacy_proxy_status`
- `privacy_proxy_provisioning_preview`
- `privacy_proxy_provisioning_execute`

The v9.6.6 `set_amp_account_state` provisioning arguments remain available as a legacy compatibility surface, but new MCP clients must use the split commands above. The v9.6.7 layer does not dynamically remove/re-add a command name between preview and execute.

See `docs/MCP_LIFECYCLE_V967.md` for token persistence, annotations, reconnect/restart behavior and the legacy compatibility policy.

## Security properties

- Postmaster generates the Ed25519 keypair locally.
- The private signing key is encrypted at rest and never returned through MCP/chat/log/audit/error output.
- MCP exposes only the base64url Ed25519 public key, key-id and SHA-256 fingerprint.
- The Worker public key/key-id must be explicitly pinned. Missing or wrong trust material fails closed; there is no unauthenticated TOFU/first-claim path.
- Postmaster generates the proxy HMAC secret internally, stores it encrypted as pending and sends it directly to the Worker server-to-server.
- Provisioning signatures bind method, path, Worker origin, timestamp, nonce, body digest, monotonic generation, operation and key-id.
- Local promotion remains `pending -> verify -> active`; Postmaster replaces the local active secret only after `/health` verifies the pending secret.
- Worker provisioning nonces remain timestamp-bounded and one-time.
- Rotation retains the immediately previous secret only for the bounded grace period (120 seconds requested, 300 seconds maximum).
- `POSTMASTER_PROXY_SECRET` remains a legacy Worker fallback when no MCP-native Durable Object secret is active.
- MCP approval tokens have a 300-second TTL, survive reconnect/restart and are one-time with persistent replay rejection. They contain no HMAC shared secret or Ed25519 private key.

## Required client flow

A confirmation token is not permission by itself. Every mutating action requires this sequence:

1. inspect status;
2. request the exact preview;
3. display the exact public action/target/state to the user;
4. obtain explicit approval in the active chat;
5. call execute with the exact action/Worker target and token;
6. inspect status again.

Do not infer approval from a generic client permission mode or from the fact that a preview was requested.

## 1. Inspect status

```text
privacy_proxy_status()
```

Inspect `privacy_proxy` and `privacy_proxy_provisioning`. The provisioning phase is `unprepared`, `prepared`, `pending`, or `active`. Status includes the Worker URL, generation, pending generation/operation, key-id and fingerprint, but never private signing material or plaintext HMAC secrets.

If state is `pending`, do not start a new provision or rotate action. Reconcile the pending operation.

## 2. Prepare provisioning

Preview the exact HTTPS Worker origin:

```text
privacy_proxy_provisioning_preview(
  action="prepare_provisioning",
  worker_url="https://worker.example.invalid"
)
```

This call is read-only: it does not generate a keypair, persist a Worker URL, write a confirmation nonce, or contact the Worker. Show `action_preview` and obtain explicit approval.

After approval:

```text
privacy_proxy_provisioning_execute(
  action="prepare_provisioning",
  worker_url="https://worker.example.invalid",
  confirmation_token="ONE_TIME_TOKEN_FROM_PREVIEW"
)
```

Only execute generates/persists the Ed25519 keypair and Worker URL. The response returns public material only:

- `public_key`
- `key_id`
- `fingerprint`

A compliant client must never request or display an Ed25519 private key or generated proxy HMAC secret; Postmaster does not expose them.

## 3. Pin the Worker trust anchor

Configure the Worker using the public material returned by prepare:

```text
POSTMASTER_PROVISIONING_PUBLIC_KEY = <public_key>
POSTMASTER_PROVISIONING_KEY_ID = <key_id>
```

Let the user verify the fingerprint and deploy/apply the Worker configuration before provisioning. This trust anchor exists before `/provision` is accepted, preventing a first-claim bootstrap.

## 4. Provision

Preview:

```text
privacy_proxy_provisioning_preview(
  action="provision"
)
```

The preview does not generate or send a shared secret. Show the exact Worker origin, key-id/fingerprint, generation and current state, then obtain explicit approval.

Execute after approval:

```text
privacy_proxy_provisioning_execute(
  action="provision",
  confirmation_token="ONE_TIME_TOKEN_FROM_PREVIEW"
)
```

Postmaster then performs the sensitive work internally:

1. generate a random HMAC shared secret;
2. encrypt it locally as pending with the proposed generation;
3. sign the provisioning request with the encrypted-at-rest Ed25519 private key;
4. POST directly to the pinned Worker `/provision` endpoint with redirects disabled;
5. verify `/health` using the pending HMAC secret;
6. promote pending to active only after successful health verification.

The Worker rejects missing/unpinned public keys, invalid signatures, stale timestamps, replayed nonces, wrong origin/key-id/generation and rollback/out-of-order generation changes.

If `/provision` or `/health` fails, Postmaster returns bounded public failure state and keeps the encrypted pending material for reconcile. The secret is not returned.

## 5. Enable the proxy

Provisioning and enablement remain separate. After the MCP-native state is active and health-verified, the existing administrative setting can enable the proxy:

```text
set_amp_account_state(
  privacy_proxy_enabled=true
)
```

This setting remains a legacy administrative write, not part of the provisioning approval token. Tracking obfuscation and high-noise decoy traffic remain independent persisted policies; high-noise is explicit opt-in and requires tracking obfuscation.

## Rotation

Preview:

```text
privacy_proxy_provisioning_preview(
  action="rotate"
)
```

After explicit approval:

```text
privacy_proxy_provisioning_execute(
  action="rotate",
  confirmation_token="ONE_TIME_TOKEN_FROM_PREVIEW"
)
```

Rotation uses the next monotonic generation. The Worker retains the previous secret only for bounded grace, and Postmaster promotes the new local secret only after health verification.

## Reconcile after interruption

If status reports a pending operation, preview reconcile:

```text
privacy_proxy_provisioning_preview(
  action="reconcile"
)
```

After explicit approval:

```text
privacy_proxy_provisioning_execute(
  action="reconcile",
  confirmation_token="ONE_TIME_TOKEN_FROM_PREVIEW"
)
```

Reconcile reuses the same encrypted pending secret and generation with a fresh signed request/timestamp/nonce. It does not generate another secret. Do not rotate while pending state exists.

## Deprovision

Deprovision is the destructive provisioning operation and remains preview-first.

```text
privacy_proxy_provisioning_preview(
  action="deprovision"
)
```

After explicit approval:

```text
privacy_proxy_provisioning_execute(
  action="deprovision",
  confirmation_token="ONE_TIME_TOKEN_FROM_PREVIEW"
)
```

The signed Worker request clears MCP-native active/previous secret state. Postmaster clears its active/pending MCP-native secret state and disables the proxy locally. Public Ed25519 identity remains available for a later explicitly approved provision.

A separately configured legacy `POSTMASTER_PROXY_SECRET` is not silently removed by this operation.

## Interruption guide

- Before prepare execute: no provisioning state has changed; request a new preview if the token expires.
- After prepare but before Worker pinning: public key material can be re-read from status; pin it before provision.
- Before provision execute: no HMAC secret has been generated/sent.
- After provision/rotate execute with `pending`: inspect status, then preview and explicitly approve `reconcile`.
- After active verification but before enable: provisioning succeeded; enablement is a separate administrative write.
- After an expired, used or mismatched confirmation token: request a fresh preview and a fresh explicit approval. Approval never carries across changed action/origin/key/generation/state.

## Legacy compatibility

`set_amp_account_state` keeps its v9.6.6-compatible arguments, including the historical `privacy_proxy_action`, `privacy_proxy_confirm` and write-only `privacy_proxy_secret` paths. This prevents another legacy schema mutation, but those arguments are deprecated for the preview/approval/execute provisioning lifecycle.

The Worker `POSTMASTER_PROXY_SECRET` fallback also remains supported for existing/manual deployments. Neither legacy path is the recommended MCP-native provisioning flow in v9.6.7.

## Release versus production activation

Publishing the v9.6.7 source release does not deploy/update a production Worker, pin a production key, create a production secret, restart Postmaster, enable the proxy or switch the production Postmaster runtime. Source release and production activation remain separate security boundaries.
