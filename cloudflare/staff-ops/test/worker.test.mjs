// 最小Workerテスト（wrangler不要・node実行）。
// 確認: /health、/api/daily-ops、/api/cleaning（override merge込み）、
// POST /api/cleaning/override（403/400含む）、その他はASSETSへ。
import assert from "node:assert";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import worker from "../src/worker.js";

const dir = path.dirname(fileURLToPath(import.meta.url));
const fixture = JSON.parse(readFileSync(path.join(dir, "fixtures/staff_ops_snapshot.sample.json"), "utf-8"));

function makeEnv({ kvStore } = {}) {
  const store = kvStore || {};
  return {
    ASSETS: { fetch: async () => new Response("ASSET", { status: 200 }) },
    OPS_DATA: {
      get: async (key) => (key === "latest/staff_ops_snapshot.json" ? { body: JSON.stringify(fixture) } : null),
    },
    CLEANING_OVERRIDES: {
      get: async (key) => (key in store ? store[key] : null),
      put: async (key, value) => { store[key] = value; },
    },
  };
}

const get = (env, p) => worker.fetch(new Request("https://x" + p), env);
const post = (env, p, body, headers) => worker.fetch(new Request("https://x" + p, {
  method: "POST",
  headers: { "content-type": "application/json", ...(headers || {}) },
  body: JSON.stringify(body),
}), env);

let passed = 0;
async function check(name, fn) { await fn(); passed++; console.log("ok -", name); }

await check("/health returns JSON without reading R2/KV", async () => {
  const env = {
    ASSETS: { fetch: async () => new Response("ASSET") },
    OPS_DATA: { get: async () => { throw new Error("should not be called"); } },
    CLEANING_OVERRIDES: { get: async () => { throw new Error("should not be called"); } },
  };
  const r = await get(env, "/health");
  assert.equal(r.status, 200);
  const j = await r.json();
  assert.equal(j.status, "ok");
});

await check("/api/daily-ops?date=2026-08-30 returns the day's data", async () => {
  const env = makeEnv();
  const r = await get(env, "/api/daily-ops?date=2026-08-30");
  assert.equal(r.status, 200);
  const j = await r.json();
  assert.equal(j.date, "2026-08-30");
  assert.equal(j.arrivals.length, 2);
});

await check("/api/daily-ops missing date -> 400", async () => {
  const env = makeEnv();
  const r = await get(env, "/api/daily-ops");
  assert.equal(r.status, 400);
  assert.equal((await r.json()).error, "invalid_date");
});

await check("/api/daily-ops malformed date -> 400", async () => {
  const env = makeEnv();
  const r = await get(env, "/api/daily-ops?date=2026-8-30");
  assert.equal(r.status, 400);
});

await check("/api/daily-ops date not in snapshot -> 404", async () => {
  const env = makeEnv();
  const r = await get(env, "/api/daily-ops?date=2099-01-01");
  assert.equal(r.status, 404);
  assert.equal((await r.json()).error, "not_found");
});

await check("/api/cleaning?date=2026-08-30 returns merged rooms (no overrides yet)", async () => {
  const env = makeEnv();
  const r = await get(env, "/api/cleaning?date=2026-08-30");
  assert.equal(r.status, 200);
  const j = await r.json();
  assert.equal(j.date, "2026-08-30");
  assert.equal(j.rooms.length, 4);
  assert.equal(j.rooms[0].room_number, null);
});

await check("/api/cleaning bad date -> 400", async () => {
  const env = makeEnv();
  const r = await get(env, "/api/cleaning?date=20260830");
  assert.equal(r.status, 400);
});

await check("POST /api/cleaning/override without access header -> 403", async () => {
  const env = makeEnv();
  const r = await post(env, "/api/cleaning/override", {
    date: "2026-08-30", room_type_key: "twin_toilet",
    checkout_booking_id: "89381500", checkin_booking_id: "89381508",
    field: "room_number", value: "12",
  });
  assert.equal(r.status, 403);
  assert.equal((await r.json()).error, "access_required");
});

await check("POST /api/cleaning/override with bad date -> 400", async () => {
  const env = makeEnv();
  const r = await post(env, "/api/cleaning/override", {
    date: "not-a-date", room_type_key: "twin_toilet", field: "room_number", value: "12",
  }, { "Cf-Access-Authenticated-User-Email": "s_sato@yuge-zao.com" });
  assert.equal(r.status, 400);
  assert.equal((await r.json()).error, "invalid_date");
});

await check("POST /api/cleaning/override rejects a field outside the allowlist (e.g. state)", async () => {
  const env = makeEnv();
  const r = await post(env, "/api/cleaning/override", {
    date: "2026-08-30", room_type_key: "twin_toilet", field: "state", value: "VACANT",
  }, { "Cf-Access-Authenticated-User-Email": "s_sato@yuge-zao.com" });
  assert.equal(r.status, 400);
  assert.equal((await r.json()).error, "invalid_field");
});

await check("POST /api/cleaning/override rejects an over-length value", async () => {
  const env = makeEnv();
  const r = await post(env, "/api/cleaning/override", {
    date: "2026-08-30", room_type_key: "twin_toilet", field: "notes", value: "x".repeat(201),
  }, { "Cf-Access-Authenticated-User-Email": "s_sato@yuge-zao.com" });
  assert.equal(r.status, 400);
  assert.equal((await r.json()).error, "invalid_value");
});

await check("POST /api/cleaning/override succeeds with access header and allowed field, then GET /api/cleaning reflects it", async () => {
  const env = makeEnv();
  const postRes = await post(env, "/api/cleaning/override", {
    date: "2026-08-30", room_type_key: "twin_toilet",
    checkout_booking_id: "89381500", checkin_booking_id: "89381508",
    field: "room_number", value: "12",
  }, { "Cf-Access-Authenticated-User-Email": "s_sato@yuge-zao.com" });
  assert.equal(postRes.status, 200);
  assert.equal((await postRes.json()).ok, true);

  const getRes = await get(env, "/api/cleaning?date=2026-08-30");
  const j = await getRes.json();
  const room = j.rooms.find((r2) => r2.room_type_key === "twin_toilet");
  assert.equal(room.room_number, "12");
});

await check("POST /api/cleaning/override with value:null clears a previously-set field", async () => {
  const env = makeEnv();
  await post(env, "/api/cleaning/override", {
    date: "2026-08-30", room_type_key: "single_no_toilet",
    checkout_booking_id: null, checkin_booking_id: "89381999",
    field: "notes", value: "要確認",
  }, { "Cf-Access-Authenticated-User-Email": "s_sato@yuge-zao.com" });

  await post(env, "/api/cleaning/override", {
    date: "2026-08-30", room_type_key: "single_no_toilet",
    checkout_booking_id: null, checkin_booking_id: "89381999",
    field: "notes", value: null,
  }, { "Cf-Access-Authenticated-User-Email": "s_sato@yuge-zao.com" });

  const getRes = await get(env, "/api/cleaning?date=2026-08-30");
  const j = await getRes.json();
  const room = j.rooms.find((r2) => r2.room_type_key === "single_no_toilet");
  assert.equal(room.notes, null);
});

await check("unknown path -> ASSETS fallback", async () => {
  const env = makeEnv();
  const r = await get(env, "/");
  assert.equal(await r.text(), "ASSET");
});

await check("no-store cache headers on API responses", async () => {
  const env = makeEnv();
  const r = await get(env, "/api/daily-ops?date=2026-08-30");
  const cc = r.headers.get("cache-control") || "";
  assert.ok(cc.includes("no-store"));
});

console.log(`\n${passed} worker checks passed`);
