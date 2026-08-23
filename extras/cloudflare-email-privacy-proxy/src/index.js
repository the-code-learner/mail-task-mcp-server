const MAX_CLOCK_SKEW_SECONDS = 300;
const DEFAULT_MAX_RESPONSE_BYTES = 2_000_000;
const HARD_MAX_RESPONSE_BYTES = 2_000_000;
const HARD_MAX_DECOY_RESPONSE_BYTES = 64_000;
const FETCH_TIMEOUT_MS = 8_000;
const DECOY_FETCH_TIMEOUT_MS = 3_000;
const MAX_PREVIOUS_SECRET_GRACE_SECONDS = 300;
const PROXY_USER_AGENT = "Postmaster-MCP-Privacy-Proxy/9.6.6";
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

function base64UrlToBytes(value) {
  const normalized = String(value || "").replace(/-/g, "+").replace(/_/g, "/");
  const padded = normalized + "=".repeat((4 - (normalized.length % 4)) % 4);
  const binary = atob(padded);
  return Uint8Array.from(binary, (char) => char.charCodeAt(0));
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

function nonceGuard(env) {
  return env.NONCE_GUARD.get(env.NONCE_GUARD.idFromName("postmaster-email-privacy-proxy"));
}

async function checkNonce(env, nonce, timestamp, scope) {
  const guard = nonceGuard(env);
  const checked = await guard.fetch("https://nonce-guard/check", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ nonce, timestamp, scope }),
  });
  return checked.status === 204;
}

async function readSecretState(env) {
  const guard = nonceGuard(env);
  const response = await guard.fetch("https://nonce-guard/secret-state", { method: "GET" });
  if (!response.ok) throw new Error("secret_state_unavailable");
  return response.json();
}

async function verifyRequest(request, env, bodyBytes) {
  const timestamp = request.headers.get("x-postmaster-timestamp") || "";
  const nonce = request.headers.get("x-postmaster-nonce") || "";
  const signature = (request.headers.get("x-postmaster-signature") || "").toLowerCase();
  if (!/^\d{10,13}$/.test(timestamp) || nonce.length < 16 || nonce.length > 128 || !/^[a-f0-9]{64}$/.test(signature)) {
    return false;
  }
  const seconds = Number(timestamp.length > 10 ? Math.floor(Number(timestamp) / 1000) : Number(timestamp));
  if (!Number.isFinite(seconds) || Math.abs(Math.floor(Date.now() / 1000) - seconds) > MAX_CLOCK_SKEW_SECONDS) return false;
  const digest = await sha256Hex(bodyBytes);
  const canonical = `${timestamp}\n${nonce}\n${digest}`;

  const state = await readSecretState(env);
  const candidates = [];
  if (state.active_secret) {
    candidates.push(String(state.active_secret));
    if (state.previous_secret && Number(state.previous_valid_until || 0) >= Math.floor(Date.now() / 1000)) {
      candidates.push(String(state.previous_secret));
    }
  } else {
    const legacy = String(env.POSTMASTER_PROXY_SECRET || "");
    if (legacy.length >= 32) candidates.push(legacy);
  }
  if (!candidates.length) throw new Error("server_secret_not_configured");

  let verified = false;
  for (const secret of candidates) {
    const expected = await hmacHex(secret, canonical);
    if (constantTimeEqual(expected, signature)) {
      verified = true;
      break;
    }
  }
  if (!verified) return false;
  return checkNonce(env, nonce, seconds, "hmac");
}

export function provisioningCanonical({
  method, path, origin, timestamp, nonce, bodyDigest, generation, operation, keyId,
}) {
  return [
    String(method || "").toUpperCase(),
    String(path || ""),
    String(origin || ""),
    String(timestamp || ""),
    String(nonce || ""),
    String(Number(generation)),
    String(operation || ""),
    String(keyId || ""),
    String(bodyDigest || ""),
  ].join("\n");
}

export async function verifyProvisioningRequest(request, env, bodyBytes, payload) {
  const publicKey = String(env.POSTMASTER_PROVISIONING_PUBLIC_KEY || "");
  const pinnedKeyId = String(env.POSTMASTER_PROVISIONING_KEY_ID || "");
  if (!publicKey || !pinnedKeyId) throw new Error("provisioning_key_not_configured");

  const timestamp = request.headers.get("x-postmaster-provisioning-timestamp") || "";
  const nonce = request.headers.get("x-postmaster-provisioning-nonce") || "";
  const keyId = request.headers.get("x-postmaster-provisioning-key-id") || "";
  const generation = request.headers.get("x-postmaster-provisioning-generation") || "";
  const operation = request.headers.get("x-postmaster-provisioning-operation") || "";
  const claimedDigest = (request.headers.get("x-postmaster-provisioning-body-sha256") || "").toLowerCase();
  const signature = request.headers.get("x-postmaster-provisioning-signature") || "";

  if (!/^\d{10,13}$/.test(timestamp) || nonce.length < 16 || nonce.length > 128) return false;
  if (!/^\d+$/.test(generation) || !/^[a-f0-9]{64}$/.test(claimedDigest)) return false;
  if (!new Set(["provision", "rotate", "deprovision"]).has(operation)) return false;
  if (keyId !== pinnedKeyId || String(payload.key_id || keyId) !== keyId) return false;
  if (Number(payload.generation) !== Number(generation) || String(payload.operation || "") !== operation) return false;

  const seconds = Number(timestamp.length > 10 ? Math.floor(Number(timestamp) / 1000) : Number(timestamp));
  if (!Number.isFinite(seconds) || Math.abs(Math.floor(Date.now() / 1000) - seconds) > MAX_CLOCK_SKEW_SECONDS) return false;

  const digest = await sha256Hex(bodyBytes);
  if (!constantTimeEqual(digest, claimedDigest)) return false;
  const url = new URL(request.url);
  const canonical = provisioningCanonical({
    method: request.method,
    path: url.pathname,
    origin: url.origin,
    timestamp,
    nonce,
    bodyDigest: digest,
    generation: Number(generation),
    operation,
    keyId,
  });

  let keyBytes;
  let signatureBytes;
  try {
    keyBytes = base64UrlToBytes(publicKey);
    signatureBytes = base64UrlToBytes(signature);
  } catch (_) {
    return false;
  }
  if (keyBytes.length !== 32 || signatureBytes.length !== 64) return false;
  const key = await crypto.subtle.importKey("raw", keyBytes, { name: "Ed25519" }, false, ["verify"]);
  const verified = await crypto.subtle.verify(
    { name: "Ed25519" }, key, signatureBytes, new TextEncoder().encode(canonical),
  );
  if (!verified) return false;
  return checkNonce(env, nonce, seconds, `provision:${keyId}`);
}

async function applyProvisioning(env, payload) {
  const guard = nonceGuard(env);
  const response = await guard.fetch("https://nonce-guard/secret-state", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload),
  });
  let result = {};
  try { result = await response.json(); } catch (_) { /* status is authoritative */ }
  return { status: response.status, result };
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
  return Array.isArray(body.Answer)
    ? body.Answer.filter((row) => String(row.type) === (type === "A" ? "1" : "28")).map((row) => String(row.data || ""))
    : [];
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
    // Exactly one target is contacted for every render/decoy request and no caller Cookie or
    // Authorization header is forwarded.
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

  async _checkNonce(request) {
    const { nonce, timestamp, scope = "hmac" } = await request.json();
    const now = Math.floor(Date.now() / 1000);
    if (!nonce || Math.abs(now - Number(timestamp)) > MAX_CLOCK_SKEW_SECONDS) return new Response("", { status: 401 });
    const key = `nonce:${scope}:${nonce}`;
    if (await this.state.storage.get(key)) return new Response("", { status: 409 });
    await this.state.storage.put(key, Number(timestamp), { expiration: now + MAX_CLOCK_SKEW_SECONDS + 30 });
    return new Response("", { status: 204 });
  }

  async _getSecretState() {
    const active = await this.state.storage.get("proxy:active");
    const previous = await this.state.storage.get("proxy:previous");
    const generation = Number(await this.state.storage.get("proxy:generation") || 0);
    const now = Math.floor(Date.now() / 1000);
    let previousSecret = "";
    let previousValidUntil = 0;
    if (previous && Number(previous.valid_until || 0) >= now) {
      previousSecret = String(previous.secret || "");
      previousValidUntil = Number(previous.valid_until || 0);
    } else if (previous) {
      await this.state.storage.delete("proxy:previous");
    }
    return json({
      generation,
      active_secret: active ? String(active.secret || "") : "",
      previous_secret: previousSecret,
      previous_valid_until: previousValidUntil,
    });
  }

  async _applySecretState(request) {
    const payload = await request.json();
    const generation = Number(payload.generation);
    const operation = String(payload.operation || "");
    const currentGeneration = Number(await this.state.storage.get("proxy:generation") || 0);
    const active = await this.state.storage.get("proxy:active");
    if (!Number.isInteger(generation) || generation <= 0) return json({ error: "invalid_generation" }, 400);

    if (operation === "deprovision") {
      if (generation !== currentGeneration + 1) return json({ error: "generation_out_of_order" }, 409);
      await this.state.storage.delete("proxy:active");
      await this.state.storage.delete("proxy:previous");
      await this.state.storage.put("proxy:generation", generation);
      return json({ ok: true, generation, provisioned: false }, 204);
    }

    if (!new Set(["provision", "rotate"]).has(operation)) return json({ error: "invalid_operation" }, 400);
    const secret = String(payload.secret || "");
    if (secret.length < 32) return json({ error: "invalid_secret" }, 400);

    if (generation === currentGeneration) {
      if (active && Number(active.generation) === generation && String(active.secret || "") === secret) {
        return json({ ok: true, generation, idempotent: true }, 204);
      }
      return json({ error: "generation_conflict" }, 409);
    }
    if (generation !== currentGeneration + 1) return json({ error: "generation_out_of_order" }, 409);
    if (operation === "provision" && active) return json({ error: "already_provisioned" }, 409);
    if (operation === "rotate" && !active) return json({ error: "not_provisioned" }, 409);

    if (active) {
      const requestedGrace = Number(payload.previous_secret_grace_seconds || 0);
      const grace = Math.max(0, Math.min(requestedGrace, MAX_PREVIOUS_SECRET_GRACE_SECONDS));
      if (grace > 0) {
        await this.state.storage.put("proxy:previous", {
          secret: String(active.secret || ""),
          generation: Number(active.generation || currentGeneration),
          valid_until: Math.floor(Date.now() / 1000) + grace,
        });
      } else {
        await this.state.storage.delete("proxy:previous");
      }
    }
    await this.state.storage.put("proxy:active", { secret, generation });
    await this.state.storage.put("proxy:generation", generation);
    return json({ ok: true, generation, provisioned: true }, 204);
  }

  async fetch(request) {
    const path = new URL(request.url).pathname;
    if (path === "/check") {
      if (request.method !== "POST") return new Response("", { status: 405 });
      return this._checkNonce(request);
    }
    if (path === "/secret-state") {
      if (request.method === "GET") return this._getSecretState();
      if (request.method === "POST") return this._applySecretState(request);
      return new Response("", { status: 405 });
    }
    return new Response("", { status: 404 });
  }
}

async function handleProvisioning(request, env, bodyBytes) {
  let payload;
  try { payload = bodyBytes.length ? JSON.parse(new TextDecoder().decode(bodyBytes)) : {}; } catch (_) {
    return json({ error: "invalid_json" }, 400);
  }
  try {
    if (!(await verifyProvisioningRequest(request, env, bodyBytes, payload))) {
      return json({ error: "unauthorized_or_replay" }, 401);
    }
  } catch (error) {
    const code = String(error.message || error);
    if (code === "provisioning_key_not_configured") return json({ error: code }, 503);
    return json({ error: "provisioning_verification_failed" }, 401);
  }
  const applied = await applyProvisioning(env, payload);
  if (applied.status === 204 || applied.status === 200) {
    return json({
      ok: true,
      generation: Number(payload.generation),
      provisioned: String(payload.operation) !== "deprovision",
    }, 200);
  }
  return json({ error: String(applied.result.error || "provisioning_state_rejected") }, applied.status);
}

export default {
  async fetch(request, env) {
    if (request.method !== "POST") return json({ error: "method_not_allowed" }, 405);
    let bodyBytes;
    try { bodyBytes = new Uint8Array(await request.arrayBuffer()); } catch (_) { return json({ error: "invalid_body" }, 400); }
    const path = new URL(request.url).pathname;
    if (path === "/provision") return handleProvisioning(request, env, bodyBytes);

    try {
      if (!(await verifyRequest(request, env, bodyBytes))) return json({ error: "unauthorized_or_replay" }, 401);
    } catch (error) {
      return json({ error: String(error.message || error) }, 503);
    }
    let payload;
    try { payload = bodyBytes.length ? JSON.parse(new TextDecoder().decode(bodyBytes)) : {}; } catch (_) { return json({ error: "invalid_json" }, 400); }
    if (path === "/health") return json({ ok: true, service: "postmaster-email-privacy-proxy", version: "9.6.6" });
    if (path !== "/fetch") return json({ error: "not_found" }, 404);
    try { return json(await proxyFetch(payload)); } catch (error) { return json({ error: String(error.message || error) }, 422); }
  },
};
