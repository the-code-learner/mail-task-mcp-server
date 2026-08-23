# Threat model — Postmaster Email Privacy Proxy

## Assets and goals

The primary privacy goal is that opening **Safe Email** causes zero network activity toward URLs embedded in a message. Full HTML is a separate, two-step per-message consent flow. The first **Visualizza HTML completo** action performs static inspection only. Only after the second explicit **Conferma e carica HTML completo** action may passive rendering resources be requested through the authenticated Worker. The browser renders only Postmaster-local cached resources.

An optional persisted high-noise policy may add bounded cover traffic after that second confirmation. Its purpose is to add noise around passive-resource tracking signals, not to simulate user clicks or activate message actions.

The Worker must not become an open proxy, must not expose Postmaster credentials, and must resist SSRF toward private infrastructure and metadata services.

## Trust boundaries

1. **Email content is hostile input.** URL strings, HTML, CSS, redirects and response content are untrusted.
2. **Postmaster is the policy decision point.** It inventories all message URLs statically before network access, separates passive resources from navigation/action URLs, requires the second Full HTML confirmation, applies the persisted privacy policy, bounds genuine and decoy work, and persists accepted genuine rendering resources locally.
3. **The Worker is a constrained fetcher.** It accepts only authenticated, recent, non-replayed requests from a Postmaster instance holding the shared secret. Render and decoy requests use the same authenticated `/fetch` endpoint and the same safety checks.
4. **The browser is not trusted to contact message targets.** Full HTML is rewritten to local resource URLs and rendered under a restrictive sandbox/CSP. Cached CSS is neutralized for nested external `url()`/`@import` references before browser delivery. Decoy targets are never exposed to the browser.

## Authentication and replay resistance

Requests carry a Unix timestamp, random nonce and HMAC-SHA256 over `timestamp + nonce + SHA256(body)`. A Durable Object serializes nonce acceptance. Requests outside the five-minute clock window or with a previously used nonce are rejected.

The secret is a Cloudflare secret and a locally encrypted Postmaster credential. Read APIs expose only configured/masked state. Logging code must never log the HMAC secret or decrypted secret. High-noise does not introduce a separate unauthenticated or less-protected endpoint.

## Header privacy

Target requests are constructed from scratch. Cookie, Authorization, Referer, Origin and the real client User-Agent are never copied. A constant `Postmaster-MCP-Privacy-Proxy/9.6.3` User-Agent is used for both genuine and decoy passive-resource requests.

## SSRF controls

The Worker allows only absolute HTTP/HTTPS URLs without embedded credentials. It rejects localhost/local suffixes, loopback, RFC1918 private IPv4, link-local, CGNAT, multicast/reserved ranges, IPv6 loopback/ULA/link-local/multicast/documentation ranges, and common cloud metadata hostnames. Hostnames receive A/AAAA DNS preflight checks and are rejected if any returned address is blocked.

The same SSRF validation is applied to the original target and to any high-noise derived variant. A decoy variant must preserve the original scheme, hostname and path exactly; any scope change is rejected.

Redirects use `redirect: manual`; a redirect target is returned as inert status + `Location` and is never followed automatically, including for high-noise requests.

### DNS rebinding residual risk

Cloudflare Workers do not expose a portable API that pins the later HTTPS fetch to the exact DNS answers inspected during preflight while preserving normal TLS hostname verification. Therefore a race between DNS preflight and the platform's target resolution remains a residual risk. The template mitigates it with short-lived per-request execution, syntactic IP blocking, public-DNS checks, no redirects, strict authentication, one URL per request, response limits, and by limiting use to passive email resources. High-noise does not relax any of these controls. Deployments with stronger egress controls should apply them as defense in depth.

## Resource and content limits

For genuine Full HTML rendering, Postmaster submits at most 32 unique passive resources with at most four concurrent Worker calls. A normal Worker call handles exactly one URL, times out after eight seconds, rejects declared/streamed bodies over 2 MB, and accepts only a small image/CSS/font content-type allowlist. HTML/SVG are not returned as renderable remote resources.

For high-noise, Postmaster schedules at most four decoy calls for a confirmed message load, at most two per domain, with at most two concurrent calls. It applies a three-second per-request timeout, a 64,000-byte response limit, a 256,000-byte aggregate response budget and a seven-second execution budget. The Worker independently hard-caps decoy target time at three seconds and decoy bodies at 64,000 bytes. These controls are deliberately much smaller than the normal passive-rendering ceilings.

Remote CSS is not trusted merely because its MIME type is `text/css`: Postmaster strips `@import` and neutralizes external CSS `url()` references before serving it to the browser.

## Navigation invariant

`<a href>` targets, unsubscribe and one-click unsubscribe URLs, login/magic links, reset/verification/confirmation/approval/accept/action URLs, one-time-token links, hyperlink redirectors and other navigation URLs remain silent during Safe Email, the first Full HTML action, genuine Full HTML resource fetching and high-noise traffic. They may be inventoried and classified, but Full HTML consent does not authorize navigation or action requests.

The high-noise implementation is not a click bot. It performs GET only for resources that are structurally eligible as passive rendering inputs and deliberately does not issue side-effect-oriented requests.

## Tracking obfuscation

Tracking obfuscation is a persisted policy separate from high-noise. When enabled, genuine passive-resource requests remove a bounded set of common tracking query parameters such as `utm_*`, `fbclid`, and `gclid`. It never changes which URL classes are authorized.

High-noise requires tracking obfuscation to be enabled, but enabling tracking obfuscation does not enable high-noise. Upgraded installations implicitly receive high-noise **Off**.

## High-noise derivation and residual limitations

For a high-noise call, the Worker removes the complete original query string from an already-authorized passive URL and adds a random cache-buster while preserving the exact scheme, host and path. This prevents the cover request from deliberately replaying message-specific query tokens while still remaining a retrieval of the same passive resource path.

This is intentionally a bounded cover-traffic design rather than an extreme traffic generator. It cannot guarantee anonymity against a tracker that fingerprints path, timing, IP/edge behavior, or correlates the genuine request with other signals. Increasing traffic without bound would create denial-of-service, abuse, cost and side-effect risk, so the architecture stops at the strongest variant compatible with the passive-resource and no-navigation invariants.

## Cache and audit semantics

Successful genuine passive resources needed for rendering are stored in Postmaster's durable mailbox resource cache and reused to avoid automatic refetches. Redirects and errors are represented explicitly.

Decoy results are never inserted into that render cache and are never treated as evidence that a resource was necessary to display the message. They are recorded separately as bounded audit metadata containing a URL hash, domain, status/redirect/error state, response-byte count and timestamp. The raw decoy target is not persisted in that audit table.

## Non-goals

This Worker does not secure Postmaster's inbound network/ingress, configure Cloudflare Access/Tunnel/DNS, inspect malware in attachments, prove whether tracking is present, make remote HTML intrinsically trustworthy, simulate human clicks, automatically navigate message links, or activate email actions. Tracking scores are heuristics and are presented as estimates with observed evidence separated from inference.
