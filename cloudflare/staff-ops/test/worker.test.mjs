// 最小Workerテスト（wrangler不要・node実行）。
// 確認: 認証ミドルウェア(login/logout/session cookie/デフォルト拒否)、
// /health、/api/daily-ops、/src/roomMaster.js、/api/cleaning（override merge込み、
// NEWキー体系 `${date}:${roomNumber}`）、POST/DELETE /api/cleaning/override
// （401/400/403含む・Origin確認・同一ロジックの共有バリデーション）、その他はASSETSへ。
import assert from "node:assert";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import worker from "../src/worker.js";
import { createSessionToken, SESSION_COOKIE_NAME } from "../src/auth.js";
import { KIRAKU_ROOM_ORDER } from "../src/roomMaster.js";

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
const del = (env, p, body, headers) => worker.fetch(new Request("https://x" + p, {
  method: "DELETE",
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

await check("unauthenticated GET /src/roomMaster.js returns 302 (page-like route, not /api/*)", async () => {
  const env = makeEnv();
  const r = await get(env, "/src/roomMaster.js");
  assert.equal(r.status, 302);
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

// --- /src/roomMaster.js (dynamically served from src/roomMaster.js, authenticated) ---

await check("authenticated GET /src/roomMaster.js serves a JS module mirroring KIRAKU_ROOM_ORDER exactly", async () => {
  const env = makeEnv();
  const cookie = await validCookieHeader(env);
  const r = await get(env, "/src/roomMaster.js", { Cookie: cookie });
  assert.equal(r.status, 200);
  assert.ok((r.headers.get("content-type") || "").includes("javascript"));
  const text = await r.text();
  assert.ok(text.includes(JSON.stringify(KIRAKU_ROOM_ORDER)));
});

// --- GET /api/cleaning (now behind the auth middleware; merges overrides) ---

await check("authenticated GET /api/cleaning?date=2026-08-30 returns 18 canonical rooms + 1 UNASSIGNED, no overrides yet", async () => {
  const env = makeEnv();
  const cookie = await validCookieHeader(env);
  const r = await get(env, "/api/cleaning?date=2026-08-30", { Cookie: cookie });
  assert.equal(r.status, 200);
  const j = await r.json();
  assert.equal(j.date, "2026-08-30");
  assert.equal(j.rooms.length, 19);
  assert.equal(j.rooms[0].room_number, "401");
  assert.equal(j.rooms[0].hasOverride, false);
  // 2026-09撤回: TURNOVERの自動instruction「入替」は生成しない(fixture更新済み)。
  assert.equal(j.rooms[0].effectiveInstruction, "");
  const unassigned = j.rooms.find((r2) => r2.room_number === null);
  assert.equal(unassigned.status, "UNASSIGNED");
});

await check("authenticated GET /api/cleaning bad date -> 400", async () => {
  const env = makeEnv();
  const cookie = await validCookieHeader(env);
  const r = await get(env, "/api/cleaning?date=20260830", { Cookie: cookie });
  assert.equal(r.status, 400);
  assert.equal((await r.json()).error, "invalid_date");
});

await check("authenticated GET /api/cleaning for a date not in the snapshot -> 404", async () => {
  const env = makeEnv();
  const cookie = await validCookieHeader(env);
  const r = await get(env, "/api/cleaning?date=2099-01-01", { Cookie: cookie });
  assert.equal(r.status, 404);
});

// --- POST /api/cleaning/override (NEW body shape: {date, roomNumber, instruction}) ---

await check("authenticated POST /api/cleaning/override with a cross-origin Origin header -> 403", async () => {
  const env = makeEnv();
  const cookie = await validCookieHeader(env);
  const r = await post(env, "/api/cleaning/override", {
    date: "2026-08-30", roomNumber: "405", instruction: "タオル追加",
  }, { Cookie: cookie, Origin: "https://evil.example.com" });
  assert.equal(r.status, 403);
  assert.equal((await r.json()).error, "origin_not_allowed");
});

await check("authenticated POST /api/cleaning/override with matching Origin succeeds", async () => {
  const env = makeEnv();
  const cookie = await validCookieHeader(env);
  const r = await post(env, "/api/cleaning/override", {
    date: "2026-08-30", roomNumber: "405", instruction: "タオル追加",
  }, { Cookie: cookie, Origin: "https://x" });
  assert.equal(r.status, 200);
  assert.equal((await r.json()).ok, true);
});

await check("authenticated POST /api/cleaning/override with NO Origin header (tolerated) succeeds", async () => {
  const env = makeEnv();
  const cookie = await validCookieHeader(env);
  const r = await post(env, "/api/cleaning/override", {
    date: "2026-08-30", roomNumber: "406", instruction: "確認済み",
  }, { Cookie: cookie });
  assert.equal(r.status, 200);
});

await check("authenticated POST /api/cleaning/override rejects an invalid date", async () => {
  const env = makeEnv();
  const cookie = await validCookieHeader(env);
  const r = await post(env, "/api/cleaning/override", {
    date: "20260830", roomNumber: "405", instruction: "x",
  }, { Cookie: cookie });
  assert.equal(r.status, 400);
  assert.equal((await r.json()).error, "invalid_date");
});

await check("authenticated POST /api/cleaning/override rejects a room number not in KIRAKU_ROOM_ORDER", async () => {
  const env = makeEnv();
  const cookie = await validCookieHeader(env);
  const r = await post(env, "/api/cleaning/override", {
    date: "2026-08-30", roomNumber: "999", instruction: "x",
  }, { Cookie: cookie });
  assert.equal(r.status, 400);
  assert.equal((await r.json()).error, "invalid_room");
});

await check("authenticated POST /api/cleaning/override rejects an empty instruction (must not silently store '' as a permanent override)", async () => {
  const env = makeEnv();
  const cookie = await validCookieHeader(env);
  const r = await post(env, "/api/cleaning/override", {
    date: "2026-08-30", roomNumber: "405", instruction: "",
  }, { Cookie: cookie });
  assert.equal(r.status, 400);
  assert.equal((await r.json()).error, "invalid_instruction");
});

await check("authenticated POST /api/cleaning/override rejects a whitespace-only instruction", async () => {
  const env = makeEnv();
  const cookie = await validCookieHeader(env);
  const r = await post(env, "/api/cleaning/override", {
    date: "2026-08-30", roomNumber: "405", instruction: "   ",
  }, { Cookie: cookie });
  assert.equal(r.status, 400);
  assert.equal((await r.json()).error, "invalid_instruction");
});

await check("authenticated POST /api/cleaning/override rejects an over-length instruction (>200 chars)", async () => {
  const env = makeEnv();
  const cookie = await validCookieHeader(env);
  const r = await post(env, "/api/cleaning/override", {
    date: "2026-08-30", roomNumber: "405", instruction: "x".repeat(201),
  }, { Cookie: cookie });
  assert.equal(r.status, 400);
  assert.equal((await r.json()).error, "invalid_instruction");
});

await check("authenticated POST /api/cleaning/override rejects a body with extra unexpected properties", async () => {
  const env = makeEnv();
  const cookie = await validCookieHeader(env);
  const r = await post(env, "/api/cleaning/override", {
    date: "2026-08-30", roomNumber: "405", instruction: "x", price: 9800,
  }, { Cookie: cookie });
  assert.equal(r.status, 400);
  assert.equal((await r.json()).error, "invalid_payload");
});

await check("unauthenticated POST /api/cleaning/override is rejected by the auth middleware BEFORE any body validation (401, not 403/400)", async () => {
  const env = makeEnv();
  const r = await post(env, "/api/cleaning/override", {
    date: "2026-08-30", roomNumber: "405", instruction: "x",
  });
  assert.equal(r.status, 401);
});

await check("override succeeds then GET /api/cleaning reflects it (effectiveInstruction/hasOverride/updatedAt)", async () => {
  const env = makeEnv();
  const cookie = await validCookieHeader(env);
  await post(env, "/api/cleaning/override", {
    date: "2026-08-30", roomNumber: "402", instruction: "追加タオル希望",
  }, { Cookie: cookie });

  const getRes = await get(env, "/api/cleaning?date=2026-08-30", { Cookie: cookie });
  const j = await getRes.json();
  const room = j.rooms.find((r2) => r2.room_number === "402");
  assert.equal(room.effectiveInstruction, "追加タオル希望");
  assert.equal(room.hasOverride, true);
  assert.ok(room.updatedAt);
});

await check("override instruction is trimmed before storage", async () => {
  const env = makeEnv();
  const cookie = await validCookieHeader(env);
  await post(env, "/api/cleaning/override", {
    date: "2026-08-30", roomNumber: "403", instruction: "  水回り重点清掃  ",
  }, { Cookie: cookie });

  const getRes = await get(env, "/api/cleaning?date=2026-08-30", { Cookie: cookie });
  const j = await getRes.json();
  const room = j.rooms.find((r2) => r2.room_number === "403");
  assert.equal(room.effectiveInstruction, "水回り重点清掃");
});

// --- DELETE /api/cleaning/override (NEW route) ---

await check("authenticated DELETE /api/cleaning/override removes an existing override; subsequent GET shows source_instruction again", async () => {
  const env = makeEnv();
  const cookie = await validCookieHeader(env);
  await post(env, "/api/cleaning/override", {
    date: "2026-08-30", roomNumber: "404", instruction: "臨時対応済み",
  }, { Cookie: cookie });

  const delRes = await del(env, "/api/cleaning/override", {
    date: "2026-08-30", roomNumber: "404",
  }, { Cookie: cookie });
  assert.equal(delRes.status, 200);
  assert.equal((await delRes.json()).ok, true);

  const getRes = await get(env, "/api/cleaning?date=2026-08-30", { Cookie: cookie });
  const j = await getRes.json();
  const room = j.rooms.find((r2) => r2.room_number === "404");
  assert.equal(room.hasOverride, false);
  assert.equal(room.effectiveInstruction, room.source_instruction);
});

await check("DELETE /api/cleaning/override is idempotent when nothing exists yet", async () => {
  const env = makeEnv();
  const cookie = await validCookieHeader(env);
  const r = await del(env, "/api/cleaning/override", {
    date: "2026-08-30", roomNumber: "501",
  }, { Cookie: cookie });
  assert.equal(r.status, 200);
  assert.equal((await r.json()).ok, true);
});

await check("DELETE /api/cleaning/override rejects invalid date / invalid room the same way POST does", async () => {
  const env = makeEnv();
  const cookie = await validCookieHeader(env);
  const r1 = await del(env, "/api/cleaning/override", { date: "bad", roomNumber: "401" }, { Cookie: cookie });
  assert.equal(r1.status, 400);
  assert.equal((await r1.json()).error, "invalid_date");

  const r2 = await del(env, "/api/cleaning/override", { date: "2026-08-30", roomNumber: "999" }, { Cookie: cookie });
  assert.equal(r2.status, 400);
  assert.equal((await r2.json()).error, "invalid_room");
});

await check("DELETE /api/cleaning/override rejects extra unexpected body properties", async () => {
  const env = makeEnv();
  const cookie = await validCookieHeader(env);
  const r = await del(env, "/api/cleaning/override", {
    date: "2026-08-30", roomNumber: "401", instruction: "should not be here",
  }, { Cookie: cookie });
  assert.equal(r.status, 400);
  assert.equal((await r.json()).error, "invalid_payload");
});

await check("DELETE /api/cleaning/override rejects a cross-origin Origin header", async () => {
  const env = makeEnv();
  const cookie = await validCookieHeader(env);
  const r = await del(env, "/api/cleaning/override", {
    date: "2026-08-30", roomNumber: "401",
  }, { Cookie: cookie, Origin: "https://evil.example.com" });
  assert.equal(r.status, 403);
  assert.equal((await r.json()).error, "origin_not_allowed");
});

await check("unauthenticated DELETE /api/cleaning/override is rejected by the auth middleware (401)", async () => {
  const env = makeEnv();
  const r = await del(env, "/api/cleaning/override", { date: "2026-08-30", roomNumber: "401" });
  assert.equal(r.status, 401);
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
