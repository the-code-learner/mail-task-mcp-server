# MCP lifecycle hardening in v9.6.7

Postmaster v9.6.7 separates lifecycle-sensitive MCP operations into stable command names whose input schemas and classifications do not change between preview and execute. The design specifically avoids relying on a client rediscovering or reclassifying the same command name after an approval turn.

## Stable command surface

| Command | Behavior | MCP annotations |
| --- | --- | --- |
| `runtime_status` | Local runtime identity/state only | read-only, non-destructive, idempotent, closed-world |
| `runtime_version_change_preview` | Resolve and preview one exact stable runtime target | read-only, non-destructive, idempotent, open-world because stable release discovery uses GitHub |
| `runtime_version_change_execute` | Apply one approved runtime-control intent and schedule restart | write, non-idempotent, open-world; conservatively destructive because rollback is supported |
| `privacy_proxy_status` | Local Privacy Proxy/provisioning state only | read-only, non-destructive, idempotent, closed-world |
| `privacy_proxy_provisioning_preview` | Preview one exact provisioning action | read-only, non-destructive, idempotent, closed-world |
| `privacy_proxy_provisioning_execute` | Apply one approved provisioning action | write, non-idempotent, open-world; conservatively destructive because deprovision is supported |

The two execute commands use conservative static `destructiveHint=true` because a command name has one immutable annotation set and each command includes a destructive operation. Individual responses additionally identify the requested operation: runtime rollback and Privacy Proxy deprovision are destructive; runtime update/pin/switch and Privacy Proxy prepare/provision/rotate/reconcile are not classified as destructive operations by Postmaster.

No v9.6.7 lifecycle command is dynamically removed and re-added under the same name. `tools/list` therefore exposes the same name, schema and annotations before preview, after reconnect and before execute.

## Required approval sequence

The client flow is always:

1. call the relevant status command;
2. call the relevant preview command;
3. show the exact public preview to the user;
4. obtain explicit approval from the user in the active conversation;
5. call the execute command with the exact target/action and the preview token;
6. reconnect if a runtime restart occurred and verify status.

Receiving a confirmation token is not itself approval. The caller must not infer approval from the original request, a generic permission setting, the presence of a target argument, or any wording convention.

## Confirmation-token architecture

v9.6.7 uses a stateless authenticated token plus persistent one-time nonce consumption.

The token payload contains only protocol metadata: version, scope, random nonce, issued/expiry timestamps and a SHA-256 digest of the exact binding. It is authenticated with HMAC-SHA256 using a persistent random 32-byte server key. The token contains neither the HMAC signing key nor any Privacy Proxy shared secret or Ed25519 private key.

Issuing a preview token does not write a nonce record. The persistent key and SQLite replay table are initialized when the composed runtime is installed at application startup. Execute authenticates the token and atomically records a digest of its nonce before accepting the exact binding. A valid-but-mismatched attempt therefore consumes the nonce as well, preventing a token from becoming a reusable bearer credential after a failed attempt.

The target TTL is exactly 300 seconds. Expired, malformed, wrong-scope, mismatched and already-consumed tokens are rejected. Because both the signing key and consumed-nonce database are persistent, a token can survive an MCP disconnect/reconnect and a controlled Postmaster process restart while replay rejection also survives restart.

Default persistent files are:

- `/data/mcp_confirmation_v967.key`
- `/data/mcp_confirmation_v967.db`

The SQLite database contains the additive `mcp_confirmation_consumed` table. Environment overrides exist for tests/advanced deployments, but no new setting is required in `postmaster-mcp.yml`.

## Runtime binding

A runtime preview token is bound to:

- operation (`update-latest`, `pin-version`, `switch-version`, or `rollback-version`);
- exact verified stable `vX.Y.Z` target;
- current persisted selector;
- current running build;
- current local `VERSION` release identity.

`update-latest` is not approved as an unbounded future selector. Preview resolves the current latest stable release to an exact `vX.Y.Z`. Execute requires that same concrete release and rejects the token if stable latest changed meanwhile. The persisted selector may remain `latest`, but the existing bootstrap receives `restart_ref_once=<exact-approved-release>` so the first approved restart cannot silently jump to a newer release discovered after approval.

Source release publication and production activation remain separate boundaries. Publishing/tagging v9.6.7 does not itself call the runtime execute command, write production runtime control, or restart production.

## Privacy Proxy binding

The existing provisioning service already binds the action to Worker origin, monotonic generation, Ed25519 key-id/fingerprint and pending generation/operation. v9.6.7 additionally binds the token to a live public state snapshot including Worker URL, configured/enabled state, provisioning phase, prepared/provisioned/pending state and current/pending generation.

This preserves the existing security model:

- explicit Ed25519 public-key pinning, with no unauthenticated TOFU/first-claim;
- server-to-server secret provisioning;
- `pending -> verify -> active` promotion;
- monotonic generations;
- bounded previous-secret grace;
- reconcile for interrupted provisioning/rotation;
- deprovision support;
- legacy Worker `POSTMASTER_PROXY_SECRET` fallback.

The generated proxy HMAC secret and Ed25519 private signing key remain unavailable through MCP/chat/log/audit/error output.

## Legacy compatibility

`build_status` and `set_amp_account_state` remain registered with their v9.6.6-compatible schemas and behavior so existing clients are not broken by another schema mutation. v9.6.7 does not remove/re-register either legacy command name.

They are deprecated specifically for the lifecycle-sensitive version-change and MCP-native Privacy Proxy provisioning flows. New clients should use the six commands above. Historical compatibility fields exposed by the legacy wrappers can still describe the v9.6.6/90-command contract; `runtime_status` is the authoritative v9.6.7 runtime surface and reports 96 command names (`+6` from 90).

The legacy write-only `privacy_proxy_secret` path remains supported for existing/manual deployments, but it is not the recommended MCP-native provisioning flow.

## Lifecycle regression boundary

v9.6.7 tests exercise actual MCP client lifecycle with separate connections:

`connect -> tools/list -> preview -> disconnect -> reconnect -> tools/list -> execute -> status`

Coverage verifies stable schemas/annotations, read-only previews, token survival across reconnect and controlled backend restart, successful execute after approval, one-time consumption/replay rejection, current-state binding and absence of v9.6.7 dynamic re-registration. Equivalent lifecycle coverage exists for runtime version change and Privacy Proxy provisioning, while the v9.6.6 Ed25519/replay/timestamp/origin/key/generation/SSRF/redirect/grace/reconcile/deprovision/non-exposure regressions remain in place.

## Deployment boundary

v9.6.7 does not require a dependency change and does not require editing `postmaster-mcp.yml`. The single-YAML bootstrap remains the release installer, while source release and any later production runtime activation stay explicitly separate.
