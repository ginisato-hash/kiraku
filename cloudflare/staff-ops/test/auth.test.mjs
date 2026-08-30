// auth.js の純粋関数テスト（session署名/検証・パスワード比較・cookie組み立て・
// rate limit・Origin確認）。Cloudflare Workersランタイム外(plain Node)でも
// globalThis.crypto.subtle(Web Crypto)が使えるため、実際のHMAC/SHA-256で検証する。
import assert from "node:assert";
import {
  SESSION_COOKIE_NAME,
  createSessionToken,
  verifySessionToken,
  passwordsMatch,
  parseCookieHeader,
  buildSessionCookieHeader,
  buildLogoutCookieHeader,
  isSameOriginRequest,
  checkLoginRateLimit,
  recordLoginFailure,
  clearLoginRateLimit,
  loginRateLimitConfig,
} from "../src/auth.js";

let passed = 0;
async function check(name, fn) { await fn(); passed++; console.log("ok -", name); }

const SECRET = "test-session-secret-do-not-use-in-prod";

await check("createSessionToken + verifySessionToken round-trips within the validity window", async () => {
  const token = await createSessionToken(SECRET, "1", { nowSeconds: 1000, maxAgeSeconds: 100 });
  const result = await verifySessionToken(SECRET, token, "1", { nowSeconds: 1050 });
  assert.strictEqual(result.valid, true);
  assert.strictEqual(result.payload.v, "1");
});

await check("verifySessionToken rejects an expired token", async () => {
  const token = await createSessionToken(SECRET, "1", { nowSeconds: 1000, maxAgeSeconds: 100 });
  const result = await verifySessionToken(SECRET, token, "1", { nowSeconds: 1200 });
  assert.strictEqual(result.valid, false);
  assert.strictEqual(result.reason, "expired");
});

await check("verifySessionToken rejects a version mismatch (AUTH_VERSION bump invalidates old sessions)", async () => {
  const token = await createSessionToken(SECRET, "1", { nowSeconds: 1000, maxAgeSeconds: 100 });
  const result = await verifySessionToken(SECRET, token, "2", { nowSeconds: 1050 });
  assert.strictEqual(result.valid, false);
  assert.strictEqual(result.reason, "version_mismatch");
});

await check("verifySessionToken rejects a tampered signature", async () => {
  const token = await createSessionToken(SECRET, "1", { nowSeconds: 1000, maxAgeSeconds: 100 });
  const lastChar = token.slice(-1);
  const flipped = lastChar === "A" ? "B" : "A";
  const tampered = token.slice(0, -1) + flipped;
  const result = await verifySessionToken(SECRET, tampered, "1", { nowSeconds: 1050 });
  assert.strictEqual(result.valid, false);
  assert.strictEqual(result.reason, "bad_signature");
});

await check("verifySessionToken rejects a tampered payload (e.g. exp extended) even if format looks valid", async () => {
  const token = await createSessionToken(SECRET, "1", { nowSeconds: 1000, maxAgeSeconds: 100 });
  const [payloadB64, sigB64] = token.split(".");
  // Flip a character inside the payload segment itself (not just the signature).
  const forged = (payloadB64.slice(0, -1) + (payloadB64.slice(-1) === "A" ? "B" : "A")) + "." + sigB64;
  const result = await verifySessionToken(SECRET, forged, "1", { nowSeconds: 1050 });
  assert.strictEqual(result.valid, false);
});

await check("verifySessionToken rejects a session signed with a different secret", async () => {
  const token = await createSessionToken("some-other-secret", "1", { nowSeconds: 1000, maxAgeSeconds: 100 });
  const result = await verifySessionToken(SECRET, token, "1", { nowSeconds: 1050 });
  assert.strictEqual(result.valid, false);
});

await check("verifySessionToken fails closed on missing/malformed input", async () => {
  assert.strictEqual((await verifySessionToken(SECRET, null, "1")).valid, false);
  assert.strictEqual((await verifySessionToken(SECRET, "", "1")).valid, false);
  assert.strictEqual((await verifySessionToken(SECRET, "not-a-valid-token", "1")).valid, false);
  assert.strictEqual((await verifySessionToken(null, "whatever.whatever", "1")).valid, false);
});

await check("passwordsMatch: correct password matches, wrong password does not", async () => {
  assert.strictEqual(await passwordsMatch("kiraku-staff-2026", "kiraku-staff-2026"), true);
  assert.strictEqual(await passwordsMatch("kiraku-staff-2026", "wrong-password"), false);
  assert.strictEqual(await passwordsMatch("", "kiraku-staff-2026"), false);
});

await check("passwordsMatch: different-length inputs still resolve correctly (digest-based compare)", async () => {
  assert.strictEqual(await passwordsMatch("short", "a-much-longer-password-value"), false);
});

await check("parseCookieHeader extracts the named cookie among several", async () => {
  const header = `other=1; ${SESSION_COOKIE_NAME}=abc.def; another=2`;
  assert.strictEqual(parseCookieHeader(header, SESSION_COOKIE_NAME), "abc.def");
  assert.strictEqual(parseCookieHeader(header, "missing"), null);
  assert.strictEqual(parseCookieHeader(null, SESSION_COOKIE_NAME), null);
});

await check("buildSessionCookieHeader sets HttpOnly, Secure, SameSite=Strict, Path=/, and a 30-day Max-Age", async () => {
  const header = buildSessionCookieHeader("sometoken", 2592000);
  assert.ok(header.includes("HttpOnly"));
  assert.ok(header.includes("Secure"));
  assert.ok(header.includes("SameSite=Strict"));
  assert.ok(header.includes("Path=/"));
  assert.ok(header.includes("Max-Age=2592000"));
  assert.ok(header.startsWith(`${SESSION_COOKIE_NAME}=`));
});

await check("buildLogoutCookieHeader clears the cookie via Max-Age=0", async () => {
  const header = buildLogoutCookieHeader();
  assert.ok(header.includes("Max-Age=0"));
  assert.ok(header.includes("HttpOnly"));
  assert.ok(header.includes("Secure"));
  assert.ok(header.includes("SameSite=Strict"));
});

await check("isSameOriginRequest: matching Origin allowed, mismatched Origin rejected, absent Origin tolerated", async () => {
  const sameOriginReq = new Request("https://kiraku-staff-ops.example.workers.dev/api/cleaning/override", {
    method: "POST", headers: { Origin: "https://kiraku-staff-ops.example.workers.dev" },
  });
  assert.strictEqual(isSameOriginRequest(sameOriginReq), true);

  const crossOriginReq = new Request("https://kiraku-staff-ops.example.workers.dev/api/cleaning/override", {
    method: "POST", headers: { Origin: "https://evil.example.com" },
  });
  assert.strictEqual(isSameOriginRequest(crossOriginReq), false);

  const noOriginReq = new Request("https://kiraku-staff-ops.example.workers.dev/api/cleaning/override", { method: "POST" });
  assert.strictEqual(isSameOriginRequest(noOriginReq), true);
});

await check("login rate limiting: allows attempts under the threshold, blocks at/above it, fails open with no KV", async () => {
  const store = {};
  const kv = {
    get: async (k) => (k in store ? store[k] : null),
    put: async (k, v) => { store[k] = v; },
    delete: async (k) => { delete store[k]; },
  };
  const { maxAttempts } = loginRateLimitConfig();

  for (let i = 0; i < maxAttempts; i++) {
    const before = await checkLoginRateLimit(kv, "1.2.3.4");
    assert.strictEqual(before.allowed, true, `attempt ${i} should still be allowed`);
    await recordLoginFailure(kv, "1.2.3.4", before.currentCount);
  }
  const blocked = await checkLoginRateLimit(kv, "1.2.3.4");
  assert.strictEqual(blocked.allowed, false);

  // A different IP is tracked independently.
  const otherIp = await checkLoginRateLimit(kv, "5.6.7.8");
  assert.strictEqual(otherIp.allowed, true);

  await clearLoginRateLimit(kv, "1.2.3.4");
  const afterClear = await checkLoginRateLimit(kv, "1.2.3.4");
  assert.strictEqual(afterClear.allowed, true);

  // No KV binding at all -> fail open (never blocks a legitimate login on infra hiccups).
  const noKv = await checkLoginRateLimit(null, "1.2.3.4");
  assert.strictEqual(noKv.allowed, true);
});

console.log(`\n${passed} auth checks passed`);
