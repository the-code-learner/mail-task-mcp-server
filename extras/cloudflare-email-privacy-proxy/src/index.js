const MAX_CLOCK_SKEW_SECONDS = 300;
const DEFAULT_MAX_RESPONSE_BYTES = 2_000_000;
const HARD_MAX_RESPONSE_BYTES = 2_000_000;
const HARD_MAX_DECOY_RESPONSE_BYTES = 64_000;
const FETCH_TIMEOUT_MS = 8_000;
const DECOY_FETCH_TIMEOUT_MS = 3_000;
const PROXY_USER_AGENT = "Postmaster-MCP-Privacy-Proxy/9.6.3";
const ALLOWED_CONTENT_TYPES = new Set([
  "image/jpeg", "image/png", "image/gif", "image/webp", "image/avif",
  "text/css", "font/woff", "font/woff2", "application/font-woff",
  "application/font-woff2", "application/vnd.ms-fontobject", "font/ttf", "font/otf",
]);
const TRACKING_PARAMS = new Set([
  "utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term", "gclid", "fbclid",
  "mc_cid", "mc_eid", "mkt_tok", "vero_id", "oly_anon_id", "oly_enc_id",
]);
const BLOCKED_HOSTS = new Set([
  "localhost", "localhost.localdomain", "metadata", "metadata.google.internal",
  "metadata.google.internal.", "instance-data", "instance-data.ec2.internal",
]);
const BLOCKED_CLASSIFICATIONS = new Set([
  "normal link", "analytics link", "unsubscribe", "action url", "redirector",
]);
const UNSUBSCRIBE_PATH_RE = /(?:^|[\/_.-])(?:unsubscribe|opt[-_]?out|list[-_]?unsubscribe)(?:$|[\/_.-])/i;
const ACTION_PATH_RE = /(?:^|[\/_.-])(?:action|reset|magic(?:[-_]?link)?|login|signin|verify|verification|confirm|confirmation|approve|accept|activate)(?:$|[\/_.-])/i;
const ACTION_VALUE_RE = /(?:unsubscribe|opt[-_]?out|reset|magic|login|signin|verify|confirm|approve|accept|activate)/i;
const ACTION_TOKEN_KEY_RE = /^(?:reset|magic|login|verify|verification|confirm|confirmation|approve|accept|activate)[_-]?token$/i;

function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
      "x-content-type-options": "nosniff",
      "referrer-policy": "no-referrer",
    },
  });
}

function bytesToHex(buffer) {
  return [...new Uint8Array(buffer)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

function constantTimeEqual(a, b) {
  if (a.length !== b.length) return false;
  let result = 0;
  for (let i = 0; i < a.length; i += 1) result |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return result === 0;
}

async function sha256Hex(bytes) {
  return bytesToHex(await crypto.subtle.digest("SHA-256", bytes));
}

async function hmacHex(secret, value) {
  const key = await crypto.subtle.importKey(
    "raw", new TextEncoder().encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"],
  );
  return bytesToHex(await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(value)));
}

async function verifyRequest(request, env, bodyBytes) {
  const secret = String(env.POSTMASTER_PROXY_SECRET || "");
  if (secret.length < 32) throw new Error("server_secret_not_configured");
  const timestamp = request.headers.get("x-postmaster-timestamp") || "";
  const nonce = request.headers.get("x-postmaster-nonce") || "";
  const signature = (request.headers.get("x-postmaster-signature") || "").toLowerCase();
  if (!/^\d{10,13}$/.test(timestamp) || nonce.length < 16 || nonce.length > 128 || !/^[a-f0-9]{64}$/.test(signature)) {
    return false;
  }
  const seconds = Number(timestamp.length > 10 ? Math.floor(Number(timestamp) / 1000) : Number(timestamp));
  if (!Number.isFinite(seconds) || Math.abs(Math.floor(Date.now() / 1000) - seconds) > MAX_CLOCK_SKEW_SECONDS) return false;
  const digest = await sha256Hex(bodyBytes);
  const expected = await hmacHex(secret, `${timestamp}\n${nonce}\n${digest}`);
  if (!constantTimeEqual(expected, signature)) return false;
  const guard = env.NONCE_GUARD.get(env.NONCE_GUARD.idFromName("postmaster-email-privacy-proxy"));
  const checked = await guard.fetch("https://nonce-guard/check", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ nonce, timestamp: seconds }),
  });
  return checked.status === 204;
}

function parseIPv4(host) {
  const parts = host.split(".");
  if (parts.length !== 4 || parts.some((part) => !/^\d{1,3}$/.test(part))) return null;
  const numbers = parts.map(Number);
  if (numbers.some((value) => value < 0 || value > 255)) return null;
  return numbers;
}

function blockedIPv4(parts) {
  const [a, b] = parts;
  return a === 0 || a === 10 || a === 127 ||
    (a === 100 && b >= 64 && b <= 127) ||
    (a === 169 && b === 254) ||
    (a === 172 && b >= 16 && b <= 31) ||
    (a === 192 && b === 0) ||
    (a === 192 && b === 168) ||
    (a === 198 && (b === 18 || b === 19)) ||
    a >= 224;
}

function blockedIPv6(value) {
  const host = value.toLowerCase().replace(/^\[|\]$/g, "");
  if (host === "::" || host === "::1") return true;
  if (host.startsWith("fc") || host.startsWith("fd")) return true;
  if (/^fe[89ab]/.test(host)) return true;
  if (host.startsWith("ff")) return true;
  if (host.startsWith("2001:db8:")) return true;
  const mapped = host.match(/^::ffff:(\d+\.\d+\.\d+\.\d+)$/);
  return Boolean(mapped && blockedIPv4(parseIPv4(mapped[1]) || [0, 0, 0, 0]));
}

function hostBlockedSyntactically(hostname) {
  const host = hostname.toLowerCase().replace(/\.$/, "");
  if (!host || BLOCKED_HOSTS.has(host) || host.endsWith(".localhost") || host.endsWith(".local")) return true;
  const ipv4 = parseIPv4(host);
  if (ipv4) return blockedIPv4(ipv4);
  if (host.includes(":")) return blockedIPv6(host);
  return false;
}

function navigationOrActionUrl(url) {
  let path;
  try { path = decodeURIComponent(url.pathname || ""); } catch (_) { path = url.pathname || ""; }
  if (UNSUBSCRIBE_PATH_RE.test(path) || ACTION_PATH_RE.test(path)) return true;
  for (const [rawKey, rawValue] of url.searchParams.entries()) {
    const key = String(rawKey || "").toLowerCase();
    const value = String(rawValue || "").toLowerCase();
    if (["unsubscribe", "optout", "opt_out", "opt-out", "one_click", "one-click", "list_unsubscribe", "list-unsubscribe"].includes(key)) return true;
    if (key === "action" && ACTION_VALUE_RE.test(value)) return true;
    if (["one_time_token", "one-time-token", "magic_token", "magic-token", "otp"].includes(key) || ACTION_TOKEN_KEY_RE.test(key)) return true;
  }
  return false;
}

async function dnsAnswers(hostname, type) {
  const endpoint = new URL("https://cloudflare-dns.com/dns-query");
  endpoint.searchParams.set("name", hostname);
  endpoint.searchParams.set("type", type);
  const response = await fetch(endpoint, { headers: { accept: "application/dns-json" }, redirect: "error" });
  if (!response.ok) throw new Error("dns_preflight_failed");
  const body = await response.json();
  return Array.isArray(body.Answer) ? body.Answer.filter((row) => String(row.type) === (type === "A" ? "1" : "28")).map((row) => String(row.data || "")) : [];
}

async function assertPublicTarget(url) {
  if (!["http:", "https:"].includes(url.protocol)) throw new Error("scheme_not_allowed");
  if (url.username || url.password) throw new Error("embedded_credentials_not_allowed");
  if (hostBlockedSyntactically(url.hostname)) throw new Error("blocked_target_host");
  if (parseIPv4(url.hostname) || url.hostname.includes(":")) return;
  const [v4, v6] = await Promise.all([dnsAnswers(url.hostname, "A"), dnsAnswers(url.hostname, "AAAA")]);
  const answers = [...v4, ...v6];
  if (!answers.length) throw new Error("target_has_no_public_dns_answer");
  for (const answer of answers) {
    const ipv4 = parseIPv4(answer);
    if ((ipv4 && blockedIPv4(ipv4)) || (!ipv4 && answer.includes(":") && blockedIPv6(answer))) {
      throw new Error("dns_resolved_to_blocked_address");
    }
  }
}

function minimizedTrackingUrl(input, enabled) {
  if (!enabled) return new URL(input.toString());
  const url = new URL(input.toString());
  for (const key of [...url.searchParams.keys()]) {
    if (TRACKING_PARAMS.has(key.toLowerCase()) || key.toLowerCase().startsWith("utm_")) url.searchParams.delete(key);
  }
  return url;
}

function decoyVariant(input) {
  const url = new URL(input.toString());
  url.search = "";
  const bytes = crypto.getRandomValues(new Uint8Array(8));
  url.searchParams.set("_", [...bytes].map((value) => value.toString(16).padStart(2, "0")).join(""));
  return url;
}

async function readBounded(response, maxBytes) {
  const declared = Number(response.headers.get("content-length") || "0");
  if (declared && declared > maxBytes) throw new Error("response_too_large");
  if (!response.body) return new Uint8Array();
  const reader = response.body.getReader();
  const chunks = [];
  let total = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      total += value.byteLength;
      if (total > maxBytes) throw new Error("response_too_large");
      chunks.push(value);
    }
  } finally {
    try { await reader.cancel(); } catch (_) { /* no-op */ }
  }
  const merged = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) { merged.set(chunk, offset); offset += chunk.byteLength; }
  return merged;
}

function toBase64(bytes) {
  let binary = "";
  const chunk = 0x8000;
  for (let offset = 0; offset < bytes.length; offset += chunk) {
    binary += String.fromCharCode(...bytes.subarray(offset, Math.min(offset + chunk, bytes.length)));
  }
  return btoa(binary);
}

async function proxyFetch(payload) {
  const original = new URL(String(payload.url || ""));
  const classification = String(payload.classification || "unknown").toLowerCase();
  if (BLOCKED_CLASSIFICATIONS.has(classification) || navigationOrActionUrl(original)) {
    throw new Error("navigation_or_action_url_not_proxyable");
  }
  await assertPublicTarget(original);

  const requestKind = String(payload.request_kind || "render").toLowerCase();
  if (!new Set(["render", "decoy"]).has(requestKind)) throw new Error("request_kind_not_allowed");
  const trackingObfuscation = Boolean(payload.tracking_obfuscation);
  if (requestKind === "decoy" && !trackingObfuscation) throw new Error("decoy_requires_tracking_obfuscation");

  let target = minimizedTrackingUrl(original, trackingObfuscation);
  if (requestKind === "decoy") target = decoyVariant(target);
  if (target.hostname !== original.hostname || target.protocol !== original.protocol || target.pathname !== original.pathname) {
    throw new Error("decoy_target_scope_violation");
  }
  if (navigationOrActionUrl(target)) throw new Error("navigation_or_action_url_not_proxyable");
  await assertPublicTarget(target);

  const hardMaxBytes = requestKind === "decoy" ? HARD_MAX_DECOY_RESPONSE_BYTES : HARD_MAX_RESPONSE_BYTES;
  const maxBytes = Math.max(1, Math.min(Number(payload.max_response_bytes || DEFAULT_MAX_RESPONSE_BYTES), hardMaxBytes));
  const timeoutMs = requestKind === "decoy" ? DECOY_FETCH_TIMEOUT_MS : FETCH_TIMEOUT_MS;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort("timeout"), timeoutMs);
  try {
    const response = await fetch(target, {
      method: "GET",
      redirect: "manual",
      signal: controller.signal,
      headers: {
        "accept": "image/avif,image/webp,image/*,text/css,*/*;q=0.1",
        "user-agent": PROXY_USER_AGENT,
      },
    });
    if (response.status >= 300 && response.status < 400) {
      return {
        status: response.status,
        content_type: "",
        body_base64: "",
        redirect_location: response.headers.get("location") || "",
        error: "",
        request_kind: requestKind,
      };
    }
    const contentType = (response.headers.get("content-type") || "").split(";", 1)[0].trim().toLowerCase();
    if (!ALLOWED_CONTENT_TYPES.has(contentType)) throw new Error("content_type_not_allowed");
    const body = await readBounded(response, maxBytes);
    return {
      status: response.status,
      content_type: contentType,
      body_base64: toBase64(body),
      redirect_location: "",
      error: "",
      request_kind: requestKind,
    };
  } finally {
    clearTimeout(timer);
  }
}

export class NonceGuard {
  constructor(state) { this.state = state; }
  async fetch(request) {
    if (request.method !== "POST") return new Response("", { status: 405 });
    const { nonce, timestamp } = await request.json();
    const now = Math.floor(Date.now() / 1000);
    if (!nonce || Math.abs(now - Number(timestamp)) > MAX_CLOCK_SKEW_SECONDS) return new Response("", { status: 401 });
    const key = `nonce:${nonce}`;
    if (await this.state.storage.get(key)) return new Response("", { status: 409 });
    await this.state.storage.put(key, Number(timestamp), { expiration: now + MAX_CLOCK_SKEW_SECONDS + 30 });
    return new Response("", { status: 204 });
  }
}

export default {
  async fetch(request, env) {
    if (request.method !== "POST") return json({ error: "method_not_allowed" }, 405);
    let bodyBytes;
    try { bodyBytes = new Uint8Array(await request.arrayBuffer()); } catch (_) { return json({ error: "invalid_body" }, 400); }
    try {
      if (!(await verifyRequest(request, env, bodyBytes))) return json({ error: "unauthorized_or_replay" }, 401);
    } catch (error) {
      return json({ error: String(error.message || error) }, 503);
    }
    let payload;
    try { payload = bodyBytes.length ? JSON.parse(new TextDecoder().decode(bodyBytes)) : {}; } catch (_) { return json({ error: "invalid_json" }, 400); }
    const path = new URL(request.url).pathname;
    if (path === "/health") return json({ ok: true, service: "postmaster-email-privacy-proxy", version: "9.6.3" });
    if (path !== "/fetch") return json({ error: "not_found" }, 404);
    try { return json(await proxyFetch(payload)); } catch (error) { return json({ error: String(error.message || error) }, 422); }
  },
};
