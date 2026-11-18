// 最小Workerテスト（wrangler不要・node実行）。
// 確認: /health がR2なしで返る / 旧公開BIデータ経路は閉じ、R2もASSETSも読まない（kiraku-guest-os #72）。
import assert from "node:assert";
import worker from "../src/worker.js";

const reads = [];
const env = {
  ASSETS: { fetch: async (req) => { reads.push("ASSETS " + new URL(req.url).pathname); return new Response("ASSET", { status: 200 }); } },
  BI_DATA: {
    get: async (key) => { reads.push("R2 " + key); return { body: '{"guest_name":"SYNTHETIC","bank_actual_latest_balance":1}' }; },
  },
};
const get = (p) => worker.fetch(new Request("https://x" + p), env);

let passed = 0;
async function check(name, fn) { await fn(); passed++; console.log("ok -", name); }

function assertNoStore(r) {
  const cc = r.headers.get("cache-control") || "";
  assert.ok(cc.includes("no-store"), `expected no-store, got: ${cc}`);
}

await check("/health returns JSON without reading R2", async () => {
  const r = await get("/health");
  assert.equal(r.status, 200);
  const j = await r.json();
  assert.equal(j.ok, true);
  assert.equal(j.service, "kiraku-bi");
  assert.equal(j.r2_binding, "BI_DATA");
});

const CLOSED = [
  "/api/months", "/api/manifest", "/api/snapshot", "/api/snapshot?month=2026-10",
  "/data/manifest.json", "/data/bi_snapshot.json", "/data/bi_daily_timeseries.csv", "/data/bi_monthly_kpi.csv",
  "/data/bi_validation_status.json", "/data/bi_exception_summary.json", "/data/bank_cashflow_summary.json",
  "/data/months/2026-10/bi_snapshot.json", "/data/months/2026-10/bi_daily_timeseries.csv",
  "/app.js", "/biViewModel.js", "/anything-else",
];

for (const path of CLOSED) {
  await check(`${path} is closed and reads neither R2 nor static files`, async () => {
    reads.length = 0;
    const r = await get(path);
    assert.equal(r.status, 404);
    assert.deepEqual(await r.json(), { ok: false, error: "public_bi_closed" });
    assertNoStore(r);
    assert.deepEqual(reads, []);
  });
}

await check("/ shows a notice pointing to the staff workspace, with no data", async () => {
  reads.length = 0;
  for (const path of ["/", "/index.html"]) {
    const r = await get(path);
    assert.equal(r.status, 200);
    assert.match(r.headers.get("content-type"), /text\/html/);
    const html = await r.text();
    assert.ok(html.includes("https://kiraku-checkin.giniworks.com/staff"));
    assert.ok(!html.includes("guest_name") && !html.includes("bank_"));
    assertNoStore(r);
  }
  assert.deepEqual(reads, []);
});

console.log(`worker.test.mjs: ${passed} passed`);
