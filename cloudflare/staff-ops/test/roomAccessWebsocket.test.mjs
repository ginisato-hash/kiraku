// roomAccessWebsocket.test.mjs — worker.js側のGET /api/cleaning/live routing:
// 認証ゲート・date検証・Upgradeヘッダ必須・DO bindingへの委譲。
//
// 実際のWebSocketハンドシェイク自体(101 Switching Protocols)はNode環境では
// 検証できない(cleaningLiveState.test.mjsのコメント参照 — Node fetchの
// Response実装はstatus 101を拒否する)。ここでは「認証済み・正しいUpgrade
// ヘッダを持つリクエストが、正しい日付のCleaningLiveState DOインスタンスまで
// ちゃんとルーティングされる」ところまでをNodeで検証し、そこから先の実際の
// 101アップグレードはlocal wrangler dev + 本番2ブラウザacceptanceで確認する。
import assert from "node:assert";
import worker from "../src/worker.js";
import { createSessionToken, SESSION_COOKIE_NAME } from "../src/auth.js";

const TEST_PASSWORD = "test-shared-staff-password";
const TEST_SESSION_SECRET = "test-session-secret-do-not-use-in-prod";
const TEST_AUTH_VERSION = "1";

function makeDoStorage() {
  const map = new Map();
  return {
    async get(key) { return map.has(key) ? map.get(key) : undefined; },
    async put(key, value) { map.set(key, value); },
    async list() { return [...map.entries()]; },
  };
}

// Fakeのfetch()実装(実DOクラスは呼ばない) — このファイルはWorker側のroutingだけを
// 見たいので、DOに何が渡ったか(URL/method/headers)を記録するだけの二重(spy)。
function makeSpyCleaningLiveNamespace() {
  const calls = [];
  return {
    calls,
    idFromName: (name) => name,
    get: (name) => ({
      fetch: (request) => {
        calls.push({ name, url: request.url, upgrade: request.headers.get("Upgrade") });
        return new Response(null, { status: 200 }); // stand-in; real DO would return 101
      },
    }),
  };
}

function makeEnv({ withLiveDO = true } = {}) {
  return {
    ASSETS: { fetch: async () => new Response("ASSET", { status: 200 }) },
    OPS_DATA: { get: async () => null },
    CLEANING_OVERRIDES: {
      get: async () => null, put: async () => {}, delete: async () => {},
    },
    CLEANING_LIVE: withLiveDO ? makeSpyCleaningLiveNamespace() : undefined,
    STAFF_OPS_PASSWORD: TEST_PASSWORD,
    STAFF_OPS_SESSION_SECRET: TEST_SESSION_SECRET,
    AUTH_VERSION: TEST_AUTH_VERSION,
  };
}

async function validCookieHeader(env) {
  const token = await createSessionToken(env.STAFF_OPS_SESSION_SECRET, env.AUTH_VERSION);
  return `${SESSION_COOKIE_NAME}=${token}`;
}

const get = (env, p, headers) => worker.fetch(new Request("https://x" + p, { headers: headers || {} }), env);

let passed = 0;
async function check(name, fn) { await fn(); passed++; console.log("ok -", name); }

await check("unauthenticated GET /api/cleaning/live is rejected (401), never reaches the Durable Object", async () => {
  const env = makeEnv();
  const r = await get(env, "/api/cleaning/live?date=2026-09-08", { Upgrade: "websocket" });
  assert.equal(r.status, 401);
  assert.equal(env.CLEANING_LIVE.calls.length, 0);
});

await check("authenticated GET /api/cleaning/live rejects a missing/invalid date before touching the DO", async () => {
  const env = makeEnv();
  const cookie = await validCookieHeader(env);
  const r1 = await get(env, "/api/cleaning/live", { Cookie: cookie, Upgrade: "websocket" });
  assert.equal(r1.status, 400);
  assert.equal((await r1.json()).error, "invalid_date");

  const r2 = await get(env, "/api/cleaning/live?date=20260908", { Cookie: cookie, Upgrade: "websocket" });
  assert.equal(r2.status, 400);
  assert.equal(env.CLEANING_LIVE.calls.length, 0);
});

await check("authenticated GET /api/cleaning/live without an Upgrade: websocket header is rejected (426), not silently accepted", async () => {
  const env = makeEnv();
  const cookie = await validCookieHeader(env);
  const r = await get(env, "/api/cleaning/live?date=2026-09-08", { Cookie: cookie });
  assert.equal(r.status, 426);
  assert.equal((await r.json()).error, "expected_websocket");
  assert.equal(env.CLEANING_LIVE.calls.length, 0);
});

await check("authenticated GET /api/cleaning/live returns 500 do_unavailable when the CLEANING_LIVE binding is missing", async () => {
  const env = makeEnv({ withLiveDO: false });
  const cookie = await validCookieHeader(env);
  const r = await get(env, "/api/cleaning/live?date=2026-09-08", { Cookie: cookie, Upgrade: "websocket" });
  assert.equal(r.status, 500);
  assert.equal((await r.json()).error, "do_unavailable");
});

await check("a valid authenticated WS-upgrade request is forwarded to the correct date's Durable Object instance", async () => {
  const env = makeEnv();
  const cookie = await validCookieHeader(env);
  await get(env, "/api/cleaning/live?date=2026-09-08", { Cookie: cookie, Upgrade: "websocket" });
  assert.equal(env.CLEANING_LIVE.calls.length, 1);
  assert.equal(env.CLEANING_LIVE.calls[0].name, "2026-09-08");
  assert.equal(env.CLEANING_LIVE.calls[0].upgrade, "websocket");
});

await check("two different dates route to two different Durable Object instances (idFromName uses the date)", async () => {
  const env = makeEnv();
  const cookie = await validCookieHeader(env);
  await get(env, "/api/cleaning/live?date=2026-09-08", { Cookie: cookie, Upgrade: "websocket" });
  await get(env, "/api/cleaning/live?date=2026-09-09", { Cookie: cookie, Upgrade: "websocket" });
  const names = env.CLEANING_LIVE.calls.map((c) => c.name);
  assert.deepEqual(names, ["2026-09-08", "2026-09-09"]);
});

console.log(`\n${passed} roomAccessWebsocket checks passed`);
