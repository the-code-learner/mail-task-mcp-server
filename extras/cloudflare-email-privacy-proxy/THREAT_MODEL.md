# Threat model — Postmaster Email Privacy Proxy

## Assets and goals

The primary privacy goal is that opening **Safe Email** causes zero network activity toward URLs embedded in a message. Full HTML is a separate, per-message consent action. After that consent, only passive rendering resources may be requested, through the authenticated Worker, and the browser must render only Postmaster-local cached resources.

The Worker must not become an open proxy, must not expose Postmaster credentials, and must resist SSRF toward private infrastructure and metadata services.

## Trust boundaries

1. **Email content is hostile input.** URL strings, HTML, CSS, redirects and response content are untrusted.
2. **Postmaster is the policy decision point.** It inventories all message URLs statically before network access, separates passive resources from navigation/action URLs, requires the second Full HTML confirmation, bounds concurrency/URL count, and persists accepted resources locally.
3. **The Worker is a constrained fetcher.** It accepts only authenticated, recent, non-replayed requests from a Postmaster instance holding the shared secret.
4. **The browser is not trusted to contact message targets.** Full HTML is rewritten to local resource URLs and rendered under a restrictive sandbox/CSP. Cached CSS is neutralized for nested external `url()`/`@import` references before browser delivery.

## Authentication and replay resistance

Requests carry a Unix timestamp, random nonce and HMAC-SHA256 over `timestamp + nonce + SHA256(body)`. A Durable Object serializes nonce acceptance. Requests outside the five-minute clock window or with a previously used nonce are rejected.

The secret is a Cloudflare secret and a locally encrypted Postmaster credential. Read APIs expose only configured/masked state. Logging code must never log the HMAC secret or decrypted secret.

## Header privacy

Target requests are constructed from scratch. Cookie, Authorization, Referer, Origin and the real client User-Agent are never copied. A constant `Postmaster-MCP-Privacy-Proxy/9.6.3` User-Agent is used.

## SSRF controls

The Worker allows only absolute HTTP/HTTPS URLs without embedded credentials. It rejects localhost/local suffixes, loopback, RFC1918 private IPv4, link-local, CGNAT, multicast/reserved ranges, IPv6 loopback/ULA/link-local/multicast/documentation ranges, and common cloud metadata hostnames. Hostnames receive A/AAAA DNS preflight checks and are rejected if any returned address is blocked.

Redirects use `redirect: manual`; a redirect target is returned as inert status + `Location` and is never followed automatically.

### DNS rebinding residual risk

Cloudflare Workers do not expose a portable API that pins the later HTTPS fetch to the exact DNS answers inspected during preflight while preserving normal TLS hostname verification. Therefore a race between DNS preflight and the platform's target resolution remains a residual risk. The template mitigates it with short-lived per-request execution, syntactic IP blocking, public-DNS checks, no redirects, strict authentication, one URL per request, response limits, and by limiting use to passive email resources. Deployments with stronger egress controls should apply them as defense in depth.

## Resource and content limits

Postmaster submits at most 32 unique passive resources with at most four concurrent Worker calls. Each Worker call handles exactly one URL, times out after eight seconds, rejects declared/streamed bodies over 2 MB, and accepts only a small image/CSS/font content-type allowlist. HTML/SVG are not returned as renderable remote resources.

Remote CSS is not trusted merely because its MIME type is `text/css`: Postmaster strips `@import` and neutralizes external CSS `url()` references before serving it to the browser.

## Navigation invariant

`<a href>` targets, unsubscribe URLs, one-click unsubscribe, magic-login/reset/verification/approval/action URLs and other navigation URLs remain silent during Full HTML. They may be inventoried and classified, but Full HTML consent does not authorize navigation or action requests.

## Tracking obfuscation

The optional policy removes a bounded set of common tracking query parameters from passive-resource requests. It does not generate synthetic traffic, decoy hits, or high-noise requests. This avoids increasing third-party traffic and cannot accidentally execute action URLs because navigation URLs are structurally excluded before Worker invocation.

## Non-goals

This Worker does not secure Postmaster's inbound network/ingress, configure Cloudflare Access/Tunnel/DNS, inspect malware in attachments, prove whether tracking is present, or make remote HTML intrinsically trustworthy. Tracking scores are heuristics and are presented as estimates with observed evidence separated from inference.
