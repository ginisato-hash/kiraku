// 最小Workerテスト（wrangler不要・node実行）。
// 確認: /health がR2なしで返る / data routeがR2 keyに対応 / unknown data pathが404 / その他はASSETSへ。
import assert from "node:assert";
import worker from "../src/worker.js";

const env = {
  ASSETS: { fetch: async () => new Response("ASSET", { status: 200 }) },
  BI_DATA: {
    get: async (key) =>
      key === "latest/bi_snapshot.json" ? { body: '{"month":"2026-06"}' } : null,
  },
};
const get = (p) => worker.fetch(new Request("https://x" + p), env);

let passed = 0;
async function check(name, fn) { await fn(); passed++; console.log("ok -", name); }

await check("/health returns JSON without reading R2", async () => {
  const r = await get("/health");
  assert.equal(r.status, 200);
  const j = await r.json();
  assert.equal(j.ok, true);
  assert.equal(j.service, "kiraku-bi");
  assert.equal(j.r2_binding, "BI_DATA");
});

await check("/api/snapshot maps to latest/bi_snapshot.json", async () => {
  const r = await get("/api/snapshot");
  assert.equal(r.status, 200);
  assert.match(r.headers.get("content-type"), /application\/json/);
  assert.equal((await r.json()).month, "2026-06");
});

await check("/data/bi_snapshot.json maps to same R2 key", async () => {
  const r = await get("/data/bi_snapshot.json");
  assert.equal(r.status, 200);
});

await check("missing R2 object -> 404 JSON", async () => {
  const r = await get("/data/manifest.json"); // env returns null
  assert.equal(r.status, 404);
  assert.equal((await r.json()).ok, false);
});

await check("csv route content-type", async () => {
  env.BI_DATA.get = async () => ({ body: "month\n2026-06\n" });
  const r = await get("/data/bi_monthly_kpi.csv");
  assert.match(r.headers.get("content-type"), /text\/csv/);
});

await check("unknown path -> ASSETS fallback", async () => {
  const r = await get("/");
  assert.equal(await r.text(), "ASSET");
});

// ---------------- 月別対応（/api/months, /api/snapshot?month=, /data/months/...） ----------------
function makeMonthEnv(manifestExtra) {
  const manifest = {
    default_month: "2026-07",
    available_months: ["2026-07", "2026-08"],
    months_with_any_booking: ["2026-07", "2026-08"],
    months_with_active_booking: ["2026-07", "2026-08"],
    ...manifestExtra,
  };
  const store = {
    "latest/manifest.json": JSON.stringify(manifest),
    "latest/bi_snapshot.json": JSON.stringify({ month: "2026-07", target_month: "2026-07" }),
    "latest/months/2026-07/bi_snapshot.json": JSON.stringify({ target_month: "2026-07" }),
    "latest/months/2026-08/bi_snapshot.json": JSON.stringify({ target_month: "2026-08" }),
    "latest/months/2026-07/bi_daily_timeseries.csv": "date\n2026-07-01\n",
  };
  return {
    ASSETS: { fetch: async () => new Response("ASSET", { status: 200 }) },
    BI_DATA: { get: async (key) => (key in store ? { body: store[key] } : null) },
  };
}

await check("/api/months returns month lists from manifest", async () => {
  const env = makeMonthEnv();
  const r = await worker.fetch(new Request("https://x/api/months"), env);
  assert.equal(r.status, 200);
  const j = await r.json();
  assert.deepEqual(j, {
    default_month: "2026-07",
    available_months: ["2026-07", "2026-08"],
    months_with_any_booking: ["2026-07", "2026-08"],
    months_with_active_booking: ["2026-07", "2026-08"],
    today_global_summary: null,
  });
});

await check("/api/months 404s when manifest missing", async () => {
  const env = { ASSETS: { fetch: async () => new Response("ASSET") },
    BI_DATA: { get: async () => null } };
  const r = await worker.fetch(new Request("https://x/api/months"), env);
  assert.equal(r.status, 404);
});

await check("/api/months includes today_global_summary when present in manifest", async () => {
  const env = makeMonthEnv({
    today_global_summary: { calculated_at_jst: "x", date_jst: "2026-07-08",
      new_booking_count: 1, new_booking_revenue: 12000, checkin_count: 2, checkin_revenue: 30000 },
  });
  const r = await worker.fetch(new Request("https://x/api/months"), env);
  const j = await r.json();
  assert.deepEqual(j.today_global_summary, { calculated_at_jst: "x", date_jst: "2026-07-08",
    new_booking_count: 1, new_booking_revenue: 12000, checkin_count: 2, checkin_revenue: 30000 });
});

await check("/api/snapshot?month=2026-07 reads latest/months/2026-07/bi_snapshot.json", async () => {
  const env = makeMonthEnv();
  const r = await worker.fetch(new Request("https://x/api/snapshot?month=2026-07"), env);
  assert.equal(r.status, 200);
  assert.equal((await r.json()).target_month, "2026-07");
});

await check("/api/snapshot?month=2026-08 reads the other month", async () => {
  const env = makeMonthEnv();
  const r = await worker.fetch(new Request("https://x/api/snapshot?month=2026-08"), env);
  assert.equal(r.status, 200);
  assert.equal((await r.json()).target_month, "2026-08");
});

await check("/api/snapshot?month=bad-format -> 400", async () => {
  const env = makeMonthEnv();
  const r = await worker.fetch(new Request("https://x/api/snapshot?month=2026-7"), env);
  assert.equal(r.status, 400);
});

await check("/api/snapshot?month=2099-01 not in available_months -> 404", async () => {
  const env = makeMonthEnv();
  const r = await worker.fetch(new Request("https://x/api/snapshot?month=2099-01"), env);
  assert.equal(r.status, 404);
});

await check("/api/snapshot without month uses manifest.default_month", async () => {
  const env = makeMonthEnv();
  const r = await worker.fetch(new Request("https://x/api/snapshot"), env);
  assert.equal(r.status, 200);
  assert.equal((await r.json()).target_month, "2026-07");
});

await check("/api/snapshot falls back to latest/bi_snapshot.json without manifest", async () => {
  const env = { ASSETS: { fetch: async () => new Response("ASSET") },
    BI_DATA: { get: async (key) => key === "latest/bi_snapshot.json"
      ? { body: '{"month":"2026-06"}' } : null } };
  const r = await worker.fetch(new Request("https://x/api/snapshot"), env);
  assert.equal(r.status, 200);
  assert.equal((await r.json()).month, "2026-06");
});

await check("/data/months/2026-07/bi_snapshot.json returns month file", async () => {
  const env = makeMonthEnv();
  const r = await worker.fetch(new Request("https://x/data/months/2026-07/bi_snapshot.json"), env);
  assert.equal(r.status, 200);
  assert.match(r.headers.get("content-type"), /application\/json/);
});

await check("/data/months/2026-07/bi_daily_timeseries.csv returns csv content-type", async () => {
  const env = makeMonthEnv();
  const r = await worker.fetch(
    new Request("https://x/data/months/2026-07/bi_daily_timeseries.csv"), env);
  assert.equal(r.status, 200);
  assert.match(r.headers.get("content-type"), /text\/csv/);
});

await check("/data/months/bad-format/bi_snapshot.json -> 400", async () => {
  const env = makeMonthEnv();
  const r = await worker.fetch(new Request("https://x/data/months/2026-7/bi_snapshot.json"), env);
  assert.equal(r.status, 400);
});

await check("/data/months/2026-09/bi_snapshot.json missing in R2 -> 404", async () => {
  const env = makeMonthEnv();
  const r = await worker.fetch(new Request("https://x/data/months/2026-09/bi_snapshot.json"), env);
  assert.equal(r.status, 404);
});

// ---------------- no-store cache headers（日付跨ぎ更新不具合対応） ----------------
function assertNoStore(r) {
  const cc = r.headers.get("cache-control") || "";
  assert.ok(cc.includes("no-store"), `expected no-store, got: ${cc}`);
  assert.ok(cc.includes("no-cache"), `expected no-cache, got: ${cc}`);
  assert.ok(cc.includes("must-revalidate"), `expected must-revalidate, got: ${cc}`);
}

await check("/api/manifest has no-store cache headers", async () => {
  const env = makeMonthEnv();
  const r = await worker.fetch(new Request("https://x/api/manifest"), env);
  assertNoStore(r);
});

await check("/api/snapshot has no-store cache headers", async () => {
  const env = makeMonthEnv();
  const r = await worker.fetch(new Request("https://x/api/snapshot"), env);
  assertNoStore(r);
});

await check("/api/snapshot?month=YYYY-MM has no-store cache headers", async () => {
  const env = makeMonthEnv();
  const r = await worker.fetch(new Request("https://x/api/snapshot?month=2026-07"), env);
  assertNoStore(r);
});

await check("/api/months has no-store cache headers", async () => {
  const env = makeMonthEnv();
  const r = await worker.fetch(new Request("https://x/api/months"), env);
  assertNoStore(r);
});

await check("/data/months/2026-07/bi_snapshot.json has no-store cache headers", async () => {
  const env = makeMonthEnv();
  const r = await worker.fetch(new Request("https://x/data/months/2026-07/bi_snapshot.json"), env);
  assertNoStore(r);
});

// ---------------- 手動更新ボタンのcache-busting query(`_`)を無視して正常応答する ----------------
await check("/api/snapshot?month=2026-08&_=123 (manual refresh cache-busting) still resolves correctly", async () => {
  const env = makeMonthEnv();
  const r = await worker.fetch(new Request("https://x/api/snapshot?month=2026-08&_=123"), env);
  assert.equal(r.status, 200);
  assert.equal((await r.json()).target_month, "2026-08");
  assertNoStore(r);
});

await check("/api/manifest?_=123 (manual refresh cache-busting) still resolves correctly", async () => {
  const env = makeMonthEnv();
  const r = await worker.fetch(new Request("https://x/api/manifest?_=123"), env);
  assert.equal(r.status, 200);
  assert.equal((await r.json()).default_month, "2026-07");
  assertNoStore(r);
});

console.log(`\n${passed} worker checks passed`);
