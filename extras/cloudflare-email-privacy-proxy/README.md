# Postmaster Email Privacy Proxy (generic Cloudflare Worker)

This directory contains the optional v9.6.3 Privacy Proxy used only after a user explicitly confirms **Full HTML** for one message. Postmaster Safe Email never calls this Worker and never performs DNS/HTTP toward URLs found in email. The first **Visualizza HTML completo** action is inspection/consent only; remote activity is permitted only after the second **Conferma e carica HTML completo** action.

The Worker is intentionally generic: this repository contains no personal Worker URL, account identifier, domain, token, or shared secret.

## Security contract

Postmaster sends a signed JSON request to the configured HTTPS Worker URL. Every request includes an HMAC-SHA256 signature over timestamp, nonce, and request-body SHA-256. The Worker rejects stale timestamps and replayed nonces through a Durable Object. The shared secret must be at least 32 characters, is configured as a Cloudflare secret, and must never be committed.

The Worker accepts one target URL per authenticated `/fetch` request. It only accepts HTTP/HTTPS targets, blocks local/private/link-local/metadata destinations, performs a public-DNS preflight, disables automatic redirects, enforces bounded response sizes/timeouts, and only returns passive rendering content types (images, CSS, fonts). It never forwards Cookie, Authorization, Referer, Origin, or the browser/User-Agent from Postmaster; a canonical proxy User-Agent is used instead.

Postmaster bounds normal Full HTML rendering to 32 unique passive resources and four parallel Worker calls. Hyperlinks (`<a href>`), unsubscribe/one-click unsubscribe URLs, login or magic links, reset/confirmation/approval/action URLs, navigation URLs, one-time-token links, and redirectors used as hyperlinks are never sent to the Worker by Full HTML rendering or high-noise mode. Redirect responses are stored as inert status + `Location` and are not followed.

## Manual deployment

1. Copy `wrangler.toml.example` to `wrangler.toml` outside any workflow that would accidentally commit environment-specific values.
2. Authenticate Wrangler with the Cloudflare account that should own the Worker.
3. Deploy the Worker and Durable Object configuration.
4. Create a strong random shared secret and set it with `wrangler secret put POSTMASTER_PROXY_SECRET` (or the equivalent Cloudflare dashboard secret UI). Do not place the secret in `wrangler.toml`, `postmaster-mcp.yml`, Git, logs, or MCP initialize instructions.
5. Copy the resulting HTTPS Worker URL into Postmaster's Privacy Proxy setup, enter the same secret, independently choose **Tracking obfuscation** On/Off and **High-noise decoy traffic** On/Off, save, then use **Test connection**. High-noise is default Off and requires tracking obfuscation On.
6. Enable the proxy only after the health test succeeds.

This deploys only the optional Worker. It does **not** authorize or perform a Postmaster/Portainer production deploy, restart, Docker change, Cloudflare Access change, Tunnel/DNS change, or reverse-proxy change.

## Optional AI-assisted deployment

A compatible MCP client may be able to add and authorize Cloudflare's official MCP endpoint at `https://mcp.cloudflare.com/mcp`. If the client supports that flow, an assistant can guide an authorized Worker deployment, secret creation, health verification, and saving the resulting Worker URL/policy into Postmaster. Do not assume every MCP client can install or authorize a second MCP server; manual deployment remains supported.

Cloudflare Access/Tunnel/DNS/ingress configuration is deliberately out of scope for this Worker template. Existing ingress topology must be inspected and explained before any separate, explicit hardening change.

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
- Redirects: manual, never automatically followed.
- Returned content: allowlisted passive image/CSS/font types only; SVG and HTML are not returned.
- Cloudflare edge caching is not the privacy durability boundary. Postmaster's local SQLite resource cache remains authoritative for genuine rendering fetches; decoy audit metadata is intentionally separate.

See `THREAT_MODEL.md` for assumptions and residual risks.
