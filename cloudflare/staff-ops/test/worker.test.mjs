// 最小Workerテスト（wrangler不要・node実行）。
// 確認: 認証ミドルウェア(login/logout/session cookie/デフォルト拒否)、
// /health、/api/daily-ops、/api/cleaning（override merge込み）、
// POST /api/cleaning/override（401/400/403含む・Origin確認）、その他はASSETSへ。
import assert from "node:assert";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import worker from "../src/worker.js";
import { createSessionToken, SESSION_COOKIE_NAME } from "../src/auth.js";

const dir = path.dirname(fileURLToPath(import.meta.url));
const fixture = JSON.parse(readFileSync(path.join(dir, "fixtures/staff_ops_snapshot.sample.json"), "utf-8"));

const TEST_PASSWORD = "test-shared-staff-password";
const TEST_SESSION_SECRET = "test-session-secret-do-not-use-in-prod";
const TEST_AUTH_VERSION = "1";

function makeEnv({ kvStore, authConfigured = true } = {}) {
  const store = kvStore || {};
  return {
    ASSETS: { fetch: async () => new Response("ASSET", { status: 200 }) },
    OPS_DATA: {
      get: async (key) => (key === "latest/staff_ops_snapshot.json" ? { body: JSON.stringify(fixture) } : null),
    },
    CLEANING_OVERRIDES: {
      get: async (key) => (key in store ? store[key] : null),
      put: async (key, value) => { store[key] = value; },
      delete: async (key) => { delete store[key]; },
    },
    STAFF_OPS_PASSWORD: authConfigured ? TEST_PASSWORD : undefined,
    STAFF_OPS_SESSION_SECRET: authConfigured ? TEST_SESSION_SECRET : undefined,
    AUTH_VERSION: TEST_AUTH_VERSION,
  };
}

async function validCookieHeader(env) {
  const token = await createSessionToken(env.STAFF_OPS_SESSION_SECRET, env.AUTH_VERSION);
  return `${SESSION_COOKIE_NAME}=${token}`;
}

const get = (env, p, headers) => worker.fetch(new Request("https://x" + p, { headers: headers || {} }), env);
const post = (env, p, body, headers) => worker.fetch(new Request("https://x" + p, {
  method: "POST",
  headers: { "content-type": "application/json", ...(headers || {}) },
  body: JSON.stringify(body),
}), env);

let passed = 0;
async function check(name, fn) { await fn(); passed++; console.log("ok -", name); }

// --- Public routes (no auth required) ---

await check("/health returns JSON without reading R2/KV and without any auth", async () => {
  const env = {
    ASSETS: { fetch: async () => new Response("ASSET") },
    OPS_DATA: { get: async () => { throw new Error("should not be called"); } },
    CLEANING_OVERRIDES: { get: async () => { throw new Error("should not be called"); } },
  };
  const r = await get(env, "/health");
  assert.equal(r.status, 200);
  assert.equal((await r.json()).status, "ok");
});

for (const p of ["/login", "/login.js", "/styles.css"]) {
  await check(`${p} is reachable without a session (falls through to ASSETS)`, async () => {
    const env = makeEnv();
    const r = await get(env, p);
    assert.equal(await r.text(), "ASSET");
  });
}

// --- Default-deny: everything else requires a valid session ---

await check("unauthenticated GET / redirects (302) to /login with a next param", async () => {
  const env = makeEnv();
  const r = await get(env, "/", );
  assert.equal(r.status, 302);
  const loc = r.headers.get("location");
  assert.ok(loc.includes("/login"));
  assert.ok(loc.includes("next="));
});

await check("unauthenticated GET /api/daily-ops returns 401 JSON with no PII, not a redirect", async () => {
  const env = makeEnv();
  const r = await get(env, "/api/daily-ops?date=2026-08-30");
  assert.equal(r.status, 401);
  const j = await r.json();
  assert.equal(j.error, "unauthorized");
  assert.equal(JSON.stringify(j).match(/山田|太郎|090-|991-|Direct/), null);
});

await check("unauthenticated GET /api/cleaning returns 401", async () => {
  const env = makeEnv();
  const r = await get(env, "/api/cleaning?date=2026-08-30");
  assert.equal(r.status, 401);
});

await check("unauthenticated GET /ops/print/guest-register redirects to /login (page route, not /api/*)", async () => {
  const env = makeEnv();
  const r = await get(env, "/ops/print/guest-register?date=2026-08-30");
  assert.equal(r.status, 302);
  assert.ok(r.headers.get("location").includes("/login"));
});

await check("a tampered session cookie is treated as unauthenticated", async () => {
  const env = makeEnv();
  const goodCookie = await validCookieHeader(env);
  const tampered = goodCookie.slice(0, -1) + (goodCookie.slice(-1) === "A" ? "B" : "A");
  const r = await get(env, "/api/daily-ops?date=2026-08-30", { Cookie: tampered });
  assert.equal(r.status, 401);
});

await check("when STAFF_OPS_SESSION_SECRET is not configured, EVERY protected route fails closed", async () => {
  const env = makeEnv({ authConfigured: false });
  const r1 = await get(env, "/");
  assert.equal(r1.status, 302);
  const r2 = await get(env, "/api/daily-ops?date=2026-08-30");
  assert.equal(r2.status, 401);
});

// --- Login / logout flow ---

await check("POST /api/auth/login with the wrong password returns 401 and sets no cookie", async () => {
  const env = makeEnv();
  const r = await post(env, "/api/auth/login", { password: "wrong-password" });
  assert.equal(r.status, 401);
  assert.equal((await r.json()).error, "invalid_password");
  assert.equal(r.headers.get("set-cookie"), null);
});

await check("POST /api/auth/login with the correct password returns 200 and a signed session cookie", async () => {
  const env = makeEnv();
  const r = await post(env, "/api/auth/login", { password: TEST_PASSWORD });
  assert.equal(r.status, 200);
  assert.equal((await r.json()).ok, true);
  const setCookie = r.headers.get("set-cookie");
  assert.ok(setCookie.includes(SESSION_COOKIE_NAME + "="));
  assert.ok(setCookie.includes("HttpOnly"));
  assert.ok(setCookie.includes("Secure"));
  assert.ok(setCookie.includes("SameSite=Strict"));
  assert.ok(setCookie.includes("Max-Age=2592000")); // 30日
});

await check("the cookie issued by a successful login is then accepted by a protected route", async () => {
  const env = makeEnv();
  const loginRes = await post(env, "/api/auth/login", { password: TEST_PASSWORD });
  const setCookie = loginRes.headers.get("set-cookie");
  const cookiePair = setCookie.split(";")[0];
  const r = await get(env, "/api/daily-ops?date=2026-08-30", { Cookie: cookiePair });
  assert.equal(r.status, 200);
  const j = await r.json();
  assert.equal(j.date, "2026-08-30");
});

await check("POST /api/auth/login when auth secrets are not configured returns 503, never crashes", async () => {
  const env = makeEnv({ authConfigured: false });
  const r = await post(env, "/api/auth/login", { password: "anything" });
  assert.equal(r.status, 503);
  assert.equal((await r.json()).error, "auth_not_configured");
});

await check("POST /api/auth/login rate-limits repeated wrong passwords from the same IP, other IPs unaffected", async () => {
  const env = makeEnv();
  let last;
  for (let i = 0; i < 12; i++) {
    last = await post(env, "/api/auth/login", { password: "wrong" }, { "CF-Connecting-IP": "9.9.9.9" });
  }
  assert.equal(last.status, 429);
  assert.equal((await last.json()).error, "rate_limited");

  const otherIp = await post(env, "/api/auth/login", { password: "wrong" }, { "CF-Connecting-IP": "1.1.1.1" });
  assert.equal(otherIp.status, 401); // still evaluates the password, not blocked
});

await check("POST /api/auth/logout clears the session cookie (Max-Age=0) and always succeeds", async () => {
  const env = makeEnv();
  const r = await post(env, "/api/auth/logout", {});
  assert.equal(r.status, 200);
  assert.ok(r.headers.get("set-cookie").includes("Max-Age=0"));
});

await check("after logout, the cleared cookie no longer authenticates (client would drop it, but verify server-side handling of an empty value too)", async () => {
  const env = makeEnv();
  const r = await get(env, "/api/daily-ops?date=2026-08-30", { Cookie: `${SESSION_COOKIE_NAME}=` });
  assert.equal(r.status, 401);
});

// --- Cleaning data + override (now behind the auth middleware; also has an Origin check) ---

await check("authenticated GET /api/cleaning?date=2026-08-30 returns merged rooms (no overrides yet)", async () => {
  const env = makeEnv();
  const cookie = await validCookieHeader(env);
  const r = await get(env, "/api/cleaning?date=2026-08-30", { Cookie: cookie });
  assert.equal(r.status, 200);
  const j = await r.json();
  assert.equal(j.date, "2026-08-30");
  assert.equal(j.rooms.length, 4);
  assert.equal(j.rooms[0].room_number, null);
});

await check("authenticated GET /api/cleaning bad date -> 400", async () => {
  const env = makeEnv();
  const cookie = await validCookieHeader(env);
  const r = await get(env, "/api/cleaning?date=20260830", { Cookie: cookie });
  assert.equal(r.status, 400);
});

await check("authenticated POST /api/cleaning/override with a cross-origin Origin header -> 403", async () => {
  const env = makeEnv();
  const cookie = await validCookieHeader(env);
  const r = await post(env, "/api/cleaning/override", {
    date: "2026-08-30", room_type_key: "twin_toilet", field: "room_number", value: "12",
  }, { Cookie: cookie, Origin: "https://evil.example.com" });
  assert.equal(r.status, 403);
  assert.equal((await r.json()).error, "origin_not_allowed");
});

await check("authenticated POST /api/cleaning/override with matching Origin succeeds", async () => {
  const env = makeEnv();
  const cookie = await validCookieHeader(env);
  const r = await post(env, "/api/cleaning/override", {
    date: "2026-08-30", room_type_key: "twin_toilet",
    checkout_booking_id: "89381500", checkin_booking_id: "89381508",
    field: "room_number", value: "12",
  }, { Cookie: cookie, Origin: "https://x" });
  assert.equal(r.status, 200);
  assert.equal((await r.json()).ok, true);
});

await check("authenticated POST /api/cleaning/override with NO Origin header (tolerated) succeeds", async () => {
  const env = makeEnv();
  const cookie = await validCookieHeader(env);
  const r = await post(env, "/api/cleaning/override", {
    date: "2026-08-30", room_type_key: "twin_toilet", field: "notes", value: "確認済み",
  }, { Cookie: cookie });
  assert.equal(r.status, 200);
});

await check("authenticated POST /api/cleaning/override rejects a field outside the allowlist (e.g. state)", async () => {
  const env = makeEnv();
  const cookie = await validCookieHeader(env);
  const r = await post(env, "/api/cleaning/override", {
    date: "2026-08-30", room_type_key: "twin_toilet", field: "state", value: "VACANT",
  }, { Cookie: cookie });
  assert.equal(r.status, 400);
  assert.equal((await r.json()).error, "invalid_field");
});

await check("authenticated POST /api/cleaning/override rejects an over-length value", async () => {
  const env = makeEnv();
  const cookie = await validCookieHeader(env);
  const r = await post(env, "/api/cleaning/override", {
    date: "2026-08-30", room_type_key: "twin_toilet", field: "notes", value: "x".repeat(201),
  }, { Cookie: cookie });
  assert.equal(r.status, 400);
  assert.equal((await r.json()).error, "invalid_value");
});

await check("unauthenticated POST /api/cleaning/override is rejected by the auth middleware BEFORE the origin/field checks (401, not 403/400)", async () => {
  const env = makeEnv();
  const r = await post(env, "/api/cleaning/override", {
    date: "2026-08-30", room_type_key: "twin_toilet", field: "room_number", value: "12",
  });
  assert.equal(r.status, 401);
});

await check("override succeeds then GET /api/cleaning reflects it, with no updated_by real-name attribution (shared login)", async () => {
  const env = makeEnv();
  const cookie = await validCookieHeader(env);
  await post(env, "/api/cleaning/override", {
    date: "2026-08-30", room_type_key: "twin_toilet",
    checkout_booking_id: "89381500", checkin_booking_id: "89381508",
    field: "room_number", value: "12",
  }, { Cookie: cookie });

  const getRes = await get(env, "/api/cleaning?date=2026-08-30", { Cookie: cookie });
  const j = await getRes.json();
  const room = j.rooms.find((r2) => r2.room_type_key === "twin_toilet");
  assert.equal(room.room_number, "12");
  assert.equal(room.updated_by, undefined); // bookkeeping field, stripped before exposure
});

await check("override value:null clears a previously-set field", async () => {
  const env = makeEnv();
  const cookie = await validCookieHeader(env);
  await post(env, "/api/cleaning/override", {
    date: "2026-08-30", room_type_key: "single_no_toilet",
    checkout_booking_id: null, checkin_booking_id: "89381999",
    field: "notes", value: "要確認",
  }, { Cookie: cookie });
  await post(env, "/api/cleaning/override", {
    date: "2026-08-30", room_type_key: "single_no_toilet",
    checkout_booking_id: null, checkin_booking_id: "89381999",
    field: "notes", value: null,
  }, { Cookie: cookie });

  const getRes = await get(env, "/api/cleaning?date=2026-08-30", { Cookie: cookie });
  const j = await getRes.json();
  const room = j.rooms.find((r2) => r2.room_type_key === "single_no_toilet");
  assert.equal(room.notes, null);
});

// --- Misc ---

await check("authenticated request to an unknown path falls through to ASSETS", async () => {
  const env = makeEnv();
  const cookie = await validCookieHeader(env);
  const r = await get(env, "/some-other-page", { Cookie: cookie });
  assert.equal(await r.text(), "ASSET");
});

await check("no-store cache headers on API responses", async () => {
  const env = makeEnv();
  const cookie = await validCookieHeader(env);
  const r = await get(env, "/api/daily-ops?date=2026-08-30", { Cookie: cookie });
  const cc = r.headers.get("cache-control") || "";
  assert.ok(cc.includes("no-store"));
});

console.log(`\n${passed} worker checks passed`);
