# Postmaster Email Privacy Proxy (generic Cloudflare Worker)

This directory contains the optional Postmaster Privacy Proxy used only after a user explicitly confirms **Full HTML** for one message. Postmaster Safe Email never calls this Worker and never performs DNS/HTTP toward URLs found in email. The first **Visualizza HTML completo** action is inspection/consent only; remote activity is permitted only after the second **Conferma e carica HTML completo** action.

The Worker is intentionally generic: this repository contains no personal Worker URL, account identifier, domain, token, private signing key, or shared HMAC secret.

## v9.6.6 authentication model

Normal `/health` and `/fetch` requests keep the v9.6.3 HMAC-SHA256 contract: Postmaster signs timestamp, nonce, and request-body SHA-256 and the Worker rejects stale timestamps and replayed nonces through the existing Durable Object.

v9.6.6 adds an authenticated `/provision` control path so a compatible MCP client never has to receive or relay the proxy HMAC secret. Postmaster generates an Ed25519 keypair locally, encrypts the private key at rest, and exposes only the public key, key-id, and fingerprint. The Worker operator pins that public material as non-secret configuration. Postmaster then generates the HMAC secret internally and sends it directly to the Worker in a server-to-server request signed with Ed25519.

The provisioning signature binds the HTTP method, request path, Worker origin, timestamp, nonce, request-body SHA-256, monotonic generation, operation, and key-id. The Worker fails closed when the public key/key-id is missing or mismatched, verifies clock skew and nonce replay, requires strict generation ordering, and stores the active secret in the existing Durable Object. Rotation keeps the previous secret only for a bounded grace period (hard maximum 300 seconds) so an interrupted rotation can be reconciled without creating an unbounded dual-secret state.

There is no unauthenticated first-claim/TOFU flow. A Worker that has not been explicitly configured with the Postmaster public key cannot be provisioned.

`POSTMASTER_PROXY_SECRET` remains supported as a legacy fallback when no Durable Object provisioned secret is active. It is not the normal v9.6.6 MCP-native provisioning path.

## Security contract

The Worker accepts exactly one target URL per authenticated `/fetch` request. It only accepts HTTP/HTTPS targets, blocks local/private/link-local/metadata destinations, performs a public-DNS preflight, disables automatic redirects, enforces bounded response sizes/timeouts, and only returns passive rendering content types (images, CSS, fonts). It never forwards Cookie, Authorization, Referer, Origin, or the browser/User-Agent from Postmaster; a canonical proxy User-Agent is used instead.

Postmaster bounds normal Full HTML rendering to 32 unique passive resources and maximum four parallel Worker calls. Hyperlinks (`<a href>`), unsubscribe/one-click unsubscribe URLs, login or magic links, reset/confirmation/approval/action URLs, navigation URLs, one-time-token links, and redirectors used as hyperlinks are never sent to the Worker by Full HTML rendering or high-noise mode. Redirect responses are stored as inert status + `Location` and are not followed.

## MCP-native deployment / provisioning

1. Deploy the Worker and existing `NONCE_GUARD` Durable Object from this directory. The v9.6.6 state vault deliberately reuses that Durable Object binding; no second Durable Object namespace is required.
2. In Postmaster, inspect Privacy Proxy status and run the preview/confirmation flow for `prepare_provisioning`, optionally binding the HTTPS Worker URL during that operation.
3. After explicit confirmation, copy **only** the returned Ed25519 public key and key-id into the Worker configuration (`POSTMASTER_PROVISIONING_PUBLIC_KEY` and `POSTMASTER_PROVISIONING_KEY_ID`). The fingerprint is for human verification. Do not expect or request a private key or proxy HMAC secret from MCP.
4. Deploy/update the Worker configuration so the public key is actually pinned before provisioning.
5. In Postmaster, preview `provision`, show the exact Worker origin, key fingerprint and generation to the user, obtain explicit approval, then submit the one-time confirmation token.
6. Postmaster generates the proxy HMAC secret internally, persists it encrypted as pending, signs a direct `/provision` request with Ed25519, sends the secret server-to-server, and verifies `/health` with the new HMAC secret. Only after verification does local state become active.
7. Enable the Privacy Proxy with the existing enable control only after provisioning reports active/health-verified. Tracking obfuscation and high-noise remain independent policies.

If the client or network is interrupted after a provisioning/rotation request, inspect status first. A `pending` state means the generated secret still exists encrypted in Postmaster. Preview and explicitly confirm `reconcile`; it reuses the same pending secret and generation. Do not start another rotation while pending. The Worker accepts an idempotent same-generation replay only when it carries the same already-installed secret and a fresh valid signed request/nonce.

`rotate` uses the same preview/confirmation contract, increments generation monotonically, verifies the new HMAC secret, and keeps the previous Worker secret only within the bounded grace period. `deprovision` is also preview-first, uses the next generation, clears the provisioned Worker secret state, disables the local proxy state, and does not expose any secret.

The complete provider-neutral client wizard is documented in `../../docs/privacy-proxy-mcp-provisioning.md`.

## Legacy manual deployment

Legacy deployments remain supported:

1. Copy `wrangler.toml.example` to `wrangler.toml` outside any workflow that would accidentally commit environment-specific values.
2. Authenticate Wrangler with the account that should own the Worker.
3. Deploy the Worker and Durable Object configuration.
4. Create a strong random shared secret and set it as the Worker secret `POSTMASTER_PROXY_SECRET`. Do not place the secret in `wrangler.toml`, `postmaster-mcp.yml`, Git, logs, or MCP initialize instructions.
5. Copy the resulting HTTPS Worker URL into Postmaster's Privacy Proxy setup, enter the same secret using the legacy write-only field, independently choose **Tracking obfuscation** On/Off and **High-noise decoy traffic** On/Off, save, then use **Test connection**. High-noise is default Off and requires tracking obfuscation On.
6. Enable the proxy only after the health test succeeds.

The legacy path is compatibility-only. New installations should prefer MCP-native provisioning so the shared secret never traverses the MCP client/chat.

Deploying this optional Worker does **not** authorize or perform a Postmaster/Portainer production deploy, restart, Docker change, Access-policy change, Tunnel/DNS change, or reverse-proxy change. Source release and production activation remain separate operations.

## Optional AI-assisted deployment

A compatible MCP client may be able to configure the Worker through an authorized provider integration. That external authorization is separate from Postmaster. The Postmaster provisioning protocol itself is provider-neutral: it requires only an HTTPS Worker endpoint and the ability to pin the returned public key/key-id as non-secret Worker configuration.

Do not assume every MCP client can install or authorize a second MCP server. Manual Worker deployment remains supported. Access/Tunnel/DNS/ingress configuration is deliberately out of scope for this Worker template and requires its own explicit change approval.

## Tracking obfuscation

Tracking obfuscation is a persisted policy independent from the proxy enable switch and from high-noise mode. When enabled, ordinary passive-resource requests use bounded parameter minimization by removing common tracking parameters such as `utm_*`, `fbclid`, and `gclid`. When disabled, high-noise cannot be enabled.

The policy never authorizes navigation/action/unsubscribe URLs: those URLs remain structurally excluded before Worker invocation.

## High-noise decoy traffic

High-noise is a separate explicit opt-in policy, persisted in Postmaster and defaulting to **Off** for both new and upgraded installations. It is not requested for each message and Full HTML consent never changes the stored policy. The supported policy combinations include tracking Off + high-noise Off, tracking On + high-noise Off, and tracking On + high-noise On.

High-noise runs only after the second Full HTML confirmation and only over URLs already eligible as passive rendering resources. It is deliberately not an internet spray mode and does not simulate clicks. It never visits `<a href>` targets or unsubscribe, login/magic, reset, confirmation, approve/accept, action, navigation, one-time-token, or hyperlink redirector URLs.

Each decoy uses the same authenticated `/fetch` endpoint, HMAC/timestamp/nonce anti-replay checks, SSRF validation, header isolation, content-type allowlist, and manual redirect handling as a genuine passive fetch. The Worker derives a cover variant by removing the original query string and adding a random cache-buster while preserving the exact original scheme, host, and path; it rejects any scope change. This deliberately avoids replaying message-specific query identifiers while staying in the semantic category of passive resource retrieval.

The browser never receives the remote decoy target and never performs the decoy request. Decoy responses are not inserted into the durable Full HTML resource cache and do not prove that a resource was required for rendering. Postmaster stores only separate decoy audit metadata such as URL hash, domain, status/redirect/error, byte count, and timestamp.

### High-noise hard limits

- Maximum 4 decoy requests per single message / confirmed Full HTML load.
- Maximum 2 decoy requests per domain per load.
- Maximum 2 concurrent decoy requests.
- Postmaster client timeout: 3 seconds per decoy.
- Worker hard decoy timeout: 3 seconds.
- Maximum decoy response body: 64,000 bytes, enforced by both Postmaster and Worker.
- Maximum aggregate decoy response bytes per load: 256,000 bytes.
- Maximum Postmaster decoy execution budget: 7 seconds; the request-count/concurrency/timeout bounds keep the scheduled work inside that budget.
- Only absolute HTTP/HTTPS targets that pass the same strict SSRF controls are allowed.
- Redirects remain manual and are never followed.

## Operational limits

- Genuine Full HTML rendering: maximum 32 unique passive URLs per message confirmation.
- Genuine Full HTML rendering: maximum four parallel proxy fetches.
- Worker: exactly one target URL per authenticated `/fetch` call.
- Normal Worker target timeout: 8 seconds; high-noise target timeout: 3 seconds.
- Normal maximum returned body: 2,000,000 bytes; high-noise maximum returned body: 64,000 bytes.
- Provisioning timestamp skew: maximum 300 seconds.
- Provisioning previous-secret grace: default 120 seconds, hard maximum 300 seconds.
- Redirects: manual, never automatically followed.
- Returned content: allowlisted passive image/CSS/font types only; SVG and HTML are not returned.
- Cloudflare edge caching is not the privacy durability boundary. Postmaster's local SQLite resource cache remains authoritative for genuine rendering fetches; decoy audit metadata is intentionally separate.

See `THREAT_MODEL.md` for assumptions and residual risks.
