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

console.log(`\n${passed} worker checks passed`);
