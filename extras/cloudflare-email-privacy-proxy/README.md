# Postmaster Email Privacy Proxy (generic Cloudflare Worker)

This directory contains the optional v9.6.3 Privacy Proxy used only after a user explicitly confirms **Full HTML** for one message. Postmaster Safe Email never calls this Worker and never performs DNS/HTTP toward URLs found in email.

The Worker is intentionally generic: this repository contains no personal Worker URL, account identifier, domain, token, or shared secret.

## Security contract

Postmaster sends a signed JSON request to the configured HTTPS Worker URL. Every request includes an HMAC-SHA256 signature over timestamp, nonce, and request-body SHA-256. The Worker rejects stale timestamps and replayed nonces through a Durable Object. The shared secret must be at least 32 characters, is configured as a Cloudflare secret, and must never be committed.

The Worker accepts one target URL per authenticated `/fetch` request. It only accepts HTTP/HTTPS targets, blocks local/private/link-local/metadata destinations, performs a public-DNS preflight, disables automatic redirects, enforces an 8-second timeout and a 2 MB response ceiling, and only returns passive rendering content types (images, CSS, fonts). It never forwards Cookie, Authorization, Referer, Origin, or the browser/User-Agent from Postmaster; a canonical proxy User-Agent is used instead.

Postmaster additionally bounds Full HTML to 32 unique passive resources and four parallel Worker calls. Hyperlinks (`<a href>`), unsubscribe URLs, magic/login/reset/confirmation/action URLs are not sent to the Worker. Redirect responses are stored as status + `Location` and are not followed.

## Manual deployment

1. Copy `wrangler.toml.example` to `wrangler.toml` outside any workflow that would accidentally commit environment-specific values.
2. Authenticate Wrangler with the Cloudflare account that should own the Worker.
3. Deploy the Worker and Durable Object configuration.
4. Create a strong random shared secret and set it with `wrangler secret put POSTMASTER_PROXY_SECRET` (or the equivalent Cloudflare dashboard secret UI). Do not place the secret in `wrangler.toml`, `postmaster-mcp.yml`, Git, logs, or MCP initialize instructions.
5. Copy the resulting HTTPS Worker URL into Postmaster's Privacy Proxy setup, enter the same secret, choose tracking-obfuscation On/Off, save, then use **Test connection**.
6. Enable the proxy only after the health test succeeds.

This deploys only the optional Worker. It does **not** authorize or perform a Postmaster/Portainer production deploy, restart, Docker change, Cloudflare Access change, Tunnel/DNS change, or reverse-proxy change.

## Optional AI-assisted deployment

A compatible MCP client may be able to add and authorize Cloudflare's official MCP endpoint at `https://mcp.cloudflare.com/mcp`. If the client supports that flow, an assistant can guide an authorized Worker deployment, secret creation, health verification, and saving the resulting Worker URL/policy into Postmaster. Do not assume every MCP client can install or authorize a second MCP server; manual deployment remains supported.

Cloudflare Access/Tunnel/DNS/ingress configuration is deliberately out of scope for this Worker template. Existing ingress topology must be inspected and explained before any separate, explicit hardening change.

## Tracking obfuscation

When enabled in Postmaster configuration, the Worker performs bounded **parameter minimization** for passive-resource requests by removing common tracking parameters such as `utm_*`, `fbclid`, and `gclid`. It does not generate decoy traffic or synthetic high-noise requests. That avoids creating additional third-party network activity while still reducing common per-message identifiers where possible. The policy never applies to navigation/action/unsubscribe URLs because those URLs are never automatically fetched.

## Operational limits

- Postmaster: maximum 32 unique passive URLs per message confirmation.
- Postmaster: maximum four parallel proxy fetches.
- Worker: exactly one target URL per `/fetch` call.
- Worker target timeout: 8 seconds.
- Maximum returned body: 2,000,000 bytes.
- Redirects: manual, never automatically followed.
- Returned content: allowlisted passive image/CSS/font types only; SVG and HTML are not returned.
- Cloudflare edge caching is not the privacy durability boundary. Postmaster's local SQLite resource cache is authoritative for avoiding repeated target fetches.

See `THREAT_MODEL.md` for assumptions and residual risks.
