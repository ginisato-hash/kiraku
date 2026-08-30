// auth.js — shared staff-password authentication for kiraku-staff-ops.
//
// Cloudflare Access (Zero Trust) is NOT used for this app — replaced by a
// single shared staff password (Worker Secret STAFF_OPS_PASSWORD) plus a
// signed, HttpOnly session cookie. There is no per-user identity: everyone
// who knows the shared password gets the same session. Do not read this
// module as an individual-account auth system.
//
// Session token shape: `${base64url(JSON payload)}.${base64url(HMAC-SHA256 signature)}`
// Payload: { exp: <epoch seconds>, v: <AUTH_VERSION string> }. Verifying a
// token means: signature is valid AND exp is in the future AND v matches the
// CURRENT env.AUTH_VERSION — so bumping AUTH_VERSION (a plain, non-secret
// wrangler.toml var) instantly invalidates every existing session, which is
// the intended way to force a mass logout after rotating STAFF_OPS_PASSWORD.

export const SESSION_COOKIE_NAME = "staff_ops_session";
export const DEFAULT_SESSION_MAX_AGE_SECONDS = 30 * 24 * 60 * 60; // 30日
const LOGIN_RATE_LIMIT_MAX_ATTEMPTS = 10;
const LOGIN_RATE_LIMIT_WINDOW_SECONDS = 5 * 60;

const encoder = new TextEncoder();
const decoder = new TextDecoder();

function bytesToBase64url(bytes) {
  let bin = "";
  const arr = new Uint8Array(bytes);
  for (let i = 0; i < arr.length; i++) bin += String.fromCharCode(arr[i]);
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function base64urlToBytes(str) {
  const padded = str.replace(/-/g, "+").replace(/_/g, "/");
  const withPad = padded + "===".slice((padded.length + 3) % 4);
  const bin = atob(withPad);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

async function hmacKey(secret) {
  return crypto.subtle.importKey(
    "raw",
    encoder.encode(String(secret)),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign", "verify"],
  );
}

export async function createSessionToken(sessionSecret, authVersion, opts = {}) {
  const now = opts.nowSeconds ?? Math.floor(Date.now() / 1000);
  const maxAge = opts.maxAgeSeconds ?? DEFAULT_SESSION_MAX_AGE_SECONDS;
  const payload = { exp: now + maxAge, v: String(authVersion) };
  const payloadB64 = bytesToBase64url(encoder.encode(JSON.stringify(payload)));
  const key = await hmacKey(sessionSecret);
  const sig = await crypto.subtle.sign("HMAC", key, encoder.encode(payloadB64));
  return `${payloadB64}.${bytesToBase64url(sig)}`;
}

// Never throws — always resolves to { valid: false, reason } or { valid: true, payload }.
export async function verifySessionToken(sessionSecret, token, authVersion, opts = {}) {
  if (!sessionSecret) return { valid: false, reason: "not_configured" };
  if (!token || typeof token !== "string") return { valid: false, reason: "missing" };
  const parts = token.split(".");
  if (parts.length !== 2 || !parts[0] || !parts[1]) return { valid: false, reason: "malformed" };
  const [payloadB64, sigB64] = parts;

  let sigBytes;
  try {
    sigBytes = base64urlToBytes(sigB64);
  } catch (e) {
    return { valid: false, reason: "malformed_signature" };
  }

  let key;
  try {
    key = await hmacKey(sessionSecret);
  } catch (e) {
    return { valid: false, reason: "key_error" };
  }

  const sigOk = await crypto.subtle.verify("HMAC", key, sigBytes, encoder.encode(payloadB64));
  if (!sigOk) return { valid: false, reason: "bad_signature" };

  let payload;
  try {
    payload = JSON.parse(decoder.decode(base64urlToBytes(payloadB64)));
  } catch (e) {
    return { valid: false, reason: "bad_payload" };
  }

  const now = opts.nowSeconds ?? Math.floor(Date.now() / 1000);
  if (typeof payload.exp !== "number" || payload.exp < now) {
    return { valid: false, reason: "expired" };
  }
  if (String(payload.v) !== String(authVersion)) {
    return { valid: false, reason: "version_mismatch" };
  }
  return { valid: true, payload };
}

// Constant-time-ish password comparison: both inputs are reduced to a fixed
// 32-byte SHA-256 digest first (so no early return is possible based on
// input length), then every byte is compared regardless of earlier
// mismatches. This is a reasonable defensive measure for a small internal
// staff tool — not a claim of cryptographic timing-attack immunity.
export async function passwordsMatch(submitted, expected) {
  if (typeof submitted !== "string" || typeof expected !== "string") return false;
  const [a, b] = await Promise.all([
    crypto.subtle.digest("SHA-256", encoder.encode(submitted)),
    crypto.subtle.digest("SHA-256", encoder.encode(expected)),
  ]);
  const va = new Uint8Array(a);
  const vb = new Uint8Array(b);
  let diff = 0;
  for (let i = 0; i < va.length; i++) diff |= va[i] ^ vb[i];
  return diff === 0;
}

export function parseCookieHeader(cookieHeader, name) {
  if (!cookieHeader) return null;
  const parts = cookieHeader.split(";");
  for (const part of parts) {
    const idx = part.indexOf("=");
    if (idx === -1) continue;
    const key = part.slice(0, idx).trim();
    if (key === name) {
      try {
        return decodeURIComponent(part.slice(idx + 1).trim());
      } catch (e) {
        return part.slice(idx + 1).trim();
      }
    }
  }
  return null;
}

export function buildSessionCookieHeader(token, maxAgeSeconds = DEFAULT_SESSION_MAX_AGE_SECONDS) {
  return `${SESSION_COOKIE_NAME}=${encodeURIComponent(token)}; HttpOnly; Secure; SameSite=Strict; Path=/; Max-Age=${maxAgeSeconds}`;
}

export function buildLogoutCookieHeader() {
  return `${SESSION_COOKIE_NAME}=; HttpOnly; Secure; SameSite=Strict; Path=/; Max-Age=0`;
}

// Same-origin check for mutation endpoints (POST /api/cleaning/override).
// SameSite=Strict already prevents the session cookie from being sent on a
// true cross-site request, but this is defense-in-depth: if an Origin header
// is present it MUST match this Worker's own origin. Absent Origin is
// tolerated (some same-origin fetches omit it depending on browser/context).
export function isSameOriginRequest(request) {
  const origin = request.headers.get("Origin");
  if (!origin) return true;
  try {
    const originUrl = new URL(origin);
    const requestUrl = new URL(request.url);
    return originUrl.protocol === requestUrl.protocol && originUrl.host === requestUrl.host;
  } catch (e) {
    return false;
  }
}

// --- Simple KV-backed login rate limiting (per client IP) ---
// Deliberately NOT aggressive (no CAPTCHA, no long lockouts) — this is an
// internal staff tool, the goal is just to slow down brute-forcing the
// shared password, not to add friction for legitimate staff.
export function loginRateLimitConfig() {
  return { maxAttempts: LOGIN_RATE_LIMIT_MAX_ATTEMPTS, windowSeconds: LOGIN_RATE_LIMIT_WINDOW_SECONDS };
}

// Fails OPEN (allows the login attempt through) if the KV binding itself is
// unavailable — rate limiting is a secondary protection, not the primary
// auth gate, and must never be the reason a legitimate login is blocked by
// an infra hiccup.
export async function checkLoginRateLimit(kv, clientIp) {
  if (!kv || !clientIp) return { allowed: true, currentCount: 0 };
  const key = `login_fail:${clientIp}`;
  let raw = null;
  try {
    raw = await kv.get(key);
  } catch (e) {
    return { allowed: true, currentCount: 0 };
  }
  let count = 0;
  if (raw) {
    try {
      count = JSON.parse(raw).count || 0;
    } catch (e) {
      count = 0;
    }
  }
  const { maxAttempts } = loginRateLimitConfig();
  return { allowed: count < maxAttempts, currentCount: count };
}

export async function recordLoginFailure(kv, clientIp, currentCount) {
  if (!kv || !clientIp) return;
  const { windowSeconds } = loginRateLimitConfig();
  const key = `login_fail:${clientIp}`;
  try {
    await kv.put(key, JSON.stringify({ count: (currentCount || 0) + 1 }), { expirationTtl: windowSeconds });
  } catch (e) {
    // best-effort only
  }
}

export async function clearLoginRateLimit(kv, clientIp) {
  if (!kv || !clientIp) return;
  try {
    await kv.delete(`login_fail:${clientIp}`);
  } catch (e) {
    // best-effort only
  }
}
