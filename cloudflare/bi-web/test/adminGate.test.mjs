// adminGate.test.mjs — Worker内 admin gate の回帰テスト（wrangler不要・node実行）。
// 確認事項:
//   - 無効時（BI_ADMIN_GATE_SECRET も BI_GATE_REQUIRED も無い）は従来どおり開いている
//   - 有効時、/health と /internal/*（各自の認可）以外は x-kiraku-admin-gate が
//     一致しない限り、理由を示さない同一の404（全データルート・月別・静的パス・HTTPメソッド違い）
//   - BI_GATE_REQUIRED="true" で秘密が無ければ fail-closed
//   - 静的資産がWorkerのゲートを迂回しない設定（run_worker_first / .assetsignore）
// 秘密の値はすべてテスト用のダミー文字列。実際の秘密はコード・設定・テストに置かない。
import assert from "node:assert";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import worker from "../src/worker.js";
import { makeCoordinatorNamespace } from "./testDoNamespace.js";

const dir = path.dirname(fileURLToPath(import.meta.url));

const GATE_SECRET = "dummy-gate-secret-for-tests-only";
const GATE_HEADER = "x-kiraku-admin-gate";
const WEBHOOK_SECRET = "dummy-webhook-secret-for-tests-only";
const CALLBACK_SECRET = "dummy-callback-secret-for-tests-only";
const OPS_SECRET = "dummy-ops-secret-for-tests-only";

const MONTH = "2026-07";
const ROOT_DATA_PATHS = [
  "/api/manifest",
  "/api/snapshot",
  "/data/manifest.json",
  "/data/bi_snapshot.json",
  "/data/bi_daily_timeseries.csv",
  "/data/bi_monthly_kpi.csv",
  "/data/bi_validation_status.json",
  "/data/bi_exception_summary.json",
];
const MONTH_FILES = [
  "bi_snapshot.json", "bi_daily_timeseries.csv", "bi_monthly_kpi.csv",
  "bi_validation_status.json", "bi_exception_summary.json",
];
const MONTH_DATA_PATHS = MONTH_FILES.map((f) => `/data/months/${MONTH}/${f}`);
const API_PATHS = [
  "/api/months", `/api/snapshot?month=${MONTH}`, "/api/snapshot?month=bad", "/api/snapshot?month=2099-01",
];
const STATIC_PATHS = [
  "/", "/index.html", "/app.js", "/biViewModel.js", "/components.js", "/styles.css",
  "/data/", "/data/secret.json", "/data/months/", "/nope", "/some/spa/route", "/robots.txt",
];
const ALL_GATED_PATHS = [...ROOT_DATA_PATHS, ...API_PATHS, ...MONTH_DATA_PATHS, ...STATIC_PATHS];

function makeEnv(extra = {}) {
  const calls = { assets: 0, r2: 0 };
  const manifest = {
    default_month: MONTH, available_months: [MONTH],
    months_with_any_booking: [MONTH], months_with_active_booking: [MONTH],
  };
  const store = new Map([["latest/manifest.json", JSON.stringify(manifest)]]);
  for (const key of ["bi_snapshot.json", "bi_daily_timeseries.csv", "bi_monthly_kpi.csv",
    "bi_validation_status.json", "bi_exception_summary.json"]) {
    store.set(`latest/${key}`, `root:${key}`);
    store.set(`latest/months/${MONTH}/${key}`, `month:${key}`);
  }
  return {
    calls,
    ASSETS: { fetch: async () => { calls.assets++; return new Response("ASSET", { status: 200 }); } },
    BI_DATA: {
      get: async (key) => {
        calls.r2++;
        return store.has(key) ? { body: store.get(key) } : null;
      },
    },
    BI_REFRESH_COORDINATOR: makeCoordinatorNamespace(),
    BEDS24_WEBHOOK_SECRET: WEBHOOK_SECRET,
    BI_REFRESH_CALLBACK_SECRET: CALLBACK_SECRET,
    BI_REFRESH_OPS_SECRET: OPS_SECRET,
    KIRAKU_PROPERTY_ID: "330695",
    ...extra,
  };
}

const req = (p, { method = "GET", headers = {}, body } = {}) =>
  new Request("https://x" + p, { method, headers, body });
const call = (env, p, opts) => worker.fetch(req(p, opts), env);
const withGate = (value = GATE_SECRET) => ({ [GATE_HEADER]: value });

let passed = 0;
async function check(name, fn) { await fn(); passed++; console.log("ok -", name); }

async function assertGated404(env, p, opts, label) {
  const r = await call(env, p, opts);
  assert.equal(r.status, 404, `${label || p} should be 404`);
  assert.equal(await r.text(), "Not Found", `${label || p} must not reveal anything`);
  assert.equal(r.headers.get("content-type"), "text/plain; charset=utf-8");
  assert.match(r.headers.get("cache-control") || "", /no-store/);
  return r;
}

// ---------------------------------------------------------------- gate inactive
await check("gate inactive (no secret, no REQUIRED): every route stays open without any header", async () => {
  const env = makeEnv();
  for (const p of ROOT_DATA_PATHS) {
    const r = await call(env, p);
    assert.equal(r.status, 200, `${p} should stay open while the gate is inactive`);
  }
  for (const p of MONTH_DATA_PATHS) assert.equal((await call(env, p)).status, 200, p);
  assert.equal((await call(env, "/api/months")).status, 200);
  assert.equal(await (await call(env, "/")).text(), "ASSET");
  assert.equal(await (await call(env, "/app.js")).text(), "ASSET");
});

await check("gate inactive with BI_GATE_REQUIRED=\"false\" or empty secret is still open", async () => {
  for (const extra of [{ BI_GATE_REQUIRED: "false" }, { BI_ADMIN_GATE_SECRET: "" }, { BI_ADMIN_GATE_SECRET: undefined }]) {
    const env = makeEnv(extra);
    assert.equal((await call(env, "/api/snapshot")).status, 200);
    assert.equal(await (await call(env, "/")).text(), "ASSET");
  }
});

// ---------------------------------------------------------------- gate active (secret set)
await check("secret set: every data/api/month/static path is a uniform 404 without the header", async () => {
  const env = makeEnv({ BI_ADMIN_GATE_SECRET: GATE_SECRET });
  for (const p of ALL_GATED_PATHS) await assertGated404(env, p);
  assert.equal(env.calls.assets, 0, "ASSETS must never be reached without the header");
  assert.equal(env.calls.r2, 0, "R2 must never be read without the header");
});

await check("secret set: wrong / empty / near-miss header values are 404", async () => {
  const env = makeEnv({ BI_ADMIN_GATE_SECRET: GATE_SECRET });
  const bad = [
    "wrong", "", GATE_SECRET.slice(0, -1), GATE_SECRET + "x", "x" + GATE_SECRET,
    GATE_SECRET.toUpperCase(), "undefined", "null", "true",
  ];
  for (const value of bad) {
    for (const p of ["/api/snapshot", "/data/bi_snapshot.json", "/", `/data/months/${MONTH}/bi_snapshot.json`]) {
      await assertGated404(env, p, { headers: withGate(value) }, `${p} with header ${JSON.stringify(value)}`);
    }
  }
  assert.equal(env.calls.assets + env.calls.r2, 0);
});

await check("secret set: the secret is only accepted in the dedicated header (not Authorization / query / cookie)", async () => {
  const env = makeEnv({ BI_ADMIN_GATE_SECRET: GATE_SECRET });
  await assertGated404(env, "/api/snapshot", { headers: { Authorization: `Bearer ${GATE_SECRET}` } }, "bearer");
  await assertGated404(env, `/api/snapshot?${GATE_HEADER}=${GATE_SECRET}`, {}, "query");
  await assertGated404(env, "/api/snapshot", { headers: { cookie: `${GATE_HEADER}=${GATE_SECRET}` } }, "cookie");
  await assertGated404(env, "/api/snapshot", { headers: { "x-kiraku-webhook-token": GATE_SECRET } }, "webhook header");
  // The callback / ops secrets are not the gate secret either.
  await assertGated404(env, "/api/snapshot", { headers: withGate(OPS_SECRET) }, "ops secret as gate");
  await assertGated404(env, "/api/snapshot", { headers: withGate(CALLBACK_SECRET) }, "callback secret as gate");
});

await check("secret set: the 404 never echoes the secret or the supplied value", async () => {
  const env = makeEnv({ BI_ADMIN_GATE_SECRET: GATE_SECRET });
  const r = await call(env, "/api/snapshot", { headers: withGate("supplied-value-xyz") });
  const dump = (await r.text()) + JSON.stringify([...r.headers.entries()]);
  assert.ok(!dump.includes(GATE_SECRET));
  assert.ok(!dump.includes("supplied-value-xyz"));
});

await check("secret set: correct header passes every gated path (header name is case-insensitive)", async () => {
  const env = makeEnv({ BI_ADMIN_GATE_SECRET: GATE_SECRET });
  for (const p of ROOT_DATA_PATHS) {
    const r = await call(env, p, { headers: withGate() });
    assert.equal(r.status, 200, p);
  }
  for (const p of MONTH_DATA_PATHS) {
    const r = await call(env, p, { headers: { "X-Kiraku-Admin-Gate": GATE_SECRET } });
    assert.equal(r.status, 200, p);
    assert.match(await r.text(), /^month:/);
  }
  assert.equal((await call(env, "/api/months", { headers: withGate() })).status, 200);
  assert.equal((await call(env, `/api/snapshot?month=${MONTH}`, { headers: withGate() })).status, 200);
  for (const p of ["/", "/app.js", "/styles.css", "/index.html"]) {
    const r = await call(env, p, { headers: withGate() });
    assert.equal(await r.text(), "ASSET", `${p} should reach the static assets after the gate`);
  }
});

await check("secret set: gated requests keep the pre-gate response semantics once they pass", async () => {
  const env = makeEnv({ BI_ADMIN_GATE_SECRET: GATE_SECRET });
  assert.equal((await call(env, "/api/snapshot?month=bad", { headers: withGate() })).status, 400);
  assert.equal((await call(env, "/api/snapshot?month=2099-01", { headers: withGate() })).status, 404);
  const r = await call(env, "/data/bi_monthly_kpi.csv", { headers: withGate() });
  assert.match(r.headers.get("content-type"), /text\/csv/);
  assert.match(r.headers.get("cache-control"), /no-store/);
});

// ---------------------------------------------------------------- fail-closed (REQUIRED, no secret)
await check("BI_GATE_REQUIRED=true without a secret: fail-closed, everything gated is 404 (even with a header)", async () => {
  const env = makeEnv({ BI_GATE_REQUIRED: "true" });
  for (const p of ALL_GATED_PATHS) await assertGated404(env, p);
  for (const value of [GATE_SECRET, "", "undefined", "true"]) {
    await assertGated404(env, "/api/snapshot", { headers: withGate(value) }, `header ${JSON.stringify(value)}`);
    await assertGated404(env, "/", { headers: withGate(value) });
  }
  assert.equal(env.calls.assets + env.calls.r2, 0);
});

await check("BI_GATE_REQUIRED=true with an empty secret is also fail-closed", async () => {
  const env = makeEnv({ BI_GATE_REQUIRED: "true", BI_ADMIN_GATE_SECRET: "" });
  await assertGated404(env, "/api/snapshot", { headers: withGate("") });
  await assertGated404(env, "/", { headers: withGate("") });
});

await check("BI_GATE_REQUIRED=true with the secret: header passes, no header is 404", async () => {
  const env = makeEnv({ BI_GATE_REQUIRED: "true", BI_ADMIN_GATE_SECRET: GATE_SECRET });
  assert.equal((await call(env, "/api/snapshot", { headers: withGate() })).status, 200);
  assert.equal(await (await call(env, "/", { headers: withGate() })).text(), "ASSET");
  await assertGated404(env, "/api/snapshot");
  await assertGated404(env, "/");
});

// ---------------------------------------------------------------- HTTP methods
await check("every HTTP method without the header is 404 on data, month, api and static paths", async () => {
  const env = makeEnv({ BI_ADMIN_GATE_SECRET: GATE_SECRET });
  const targets = ["/api/snapshot", "/api/months", "/data/bi_snapshot.json", `/data/months/${MONTH}/bi_snapshot.json`, "/", "/app.js"];
  for (const method of ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]) {
    for (const p of targets) {
      const opts = { method };
      if (!["GET", "HEAD", "OPTIONS"].includes(method)) opts.body = "{}";
      const r = await call(env, p, opts);
      assert.equal(r.status, 404, `${method} ${p}`);
    }
  }
  assert.equal(env.calls.assets + env.calls.r2, 0);
});

// ---------------------------------------------------------------- unchanged routes
await check("/health is unchanged: open with the gate active, does not read R2 or the assets", async () => {
  for (const extra of [{}, { BI_ADMIN_GATE_SECRET: GATE_SECRET }, { BI_GATE_REQUIRED: "true" },
    { BI_GATE_REQUIRED: "true", BI_ADMIN_GATE_SECRET: GATE_SECRET }]) {
    const env = makeEnv(extra);
    const r = await call(env, "/health");
    assert.equal(r.status, 200);
    const j = await r.json();
    assert.equal(j.ok, true);
    assert.equal(j.service, "kiraku-bi");
    assert.equal(env.calls.r2 + env.calls.assets, 0);
  }
});

await check("/health sub-paths and look-alikes are gated (only the exact /health is public)", async () => {
  const env = makeEnv({ BI_ADMIN_GATE_SECRET: GATE_SECRET });
  for (const p of ["/health/", "/health/x", "/healthz", "/Health"]) await assertGated404(env, p);
});

function webhookRequest(token) {
  return req("/internal/beds24/booking-webhook", {
    method: "POST",
    headers: { "content-type": "application/json", "X-Kiraku-Webhook-Token": token },
    body: JSON.stringify({ booking: { id: 1, propertyId: 330695, status: "confirmed" } }),
  });
}

await check("/internal/* keeps its own authorization and does not depend on the gate header", async () => {
  for (const extra of [{ BI_ADMIN_GATE_SECRET: GATE_SECRET }, { BI_GATE_REQUIRED: "true" },
    { BI_GATE_REQUIRED: "true", BI_ADMIN_GATE_SECRET: GATE_SECRET }]) {
    const env = makeEnv(extra);

    // Beds24 webhook: shared-token auth (401 wrong / 200 right), no gate header.
    assert.equal((await worker.fetch(webhookRequest("wrong"), env)).status, 401);
    assert.equal((await worker.fetch(webhookRequest(WEBHOOK_SECRET), env)).status, 200);
    // GET on the webhook path is 405 from the handler itself, not the gate's 404.
    assert.equal((await call(env, "/internal/beds24/booking-webhook")).status, 405);

    // ops status: Bearer OPS secret.
    assert.equal((await call(env, "/internal/bi-refresh/status")).status, 401);
    const status = await call(env, "/internal/bi-refresh/status", { headers: { Authorization: `Bearer ${OPS_SECRET}` } });
    assert.equal(status.status, 200);

    // callback / shadow-observation / mode / force: their own Bearer checks (401, not the gate's 404).
    for (const p of ["/internal/bi-refresh/complete", "/internal/bi-refresh/shadow-observation",
      "/internal/bi-refresh/mode", "/internal/bi-refresh/force"]) {
      const r = await call(env, p, { method: "POST", headers: { "content-type": "application/json" }, body: "{}" });
      assert.equal(r.status, 401, `${p} should answer with its own 401`);
      assert.equal((await r.json()).error, "unauthorized");
    }
    // Ops POST with the right ops secret is accepted (force) — still no gate header.
    const force = await call(env, "/internal/bi-refresh/force", {
      method: "POST", headers: { Authorization: `Bearer ${OPS_SECRET}`, "content-type": "application/json" }, body: "{}",
    });
    assert.equal(force.status, 200);
  }
});

await check("an unknown /internal/* path is NOT exempt: it is gated like everything else", async () => {
  const env = makeEnv({ BI_ADMIN_GATE_SECRET: GATE_SECRET });
  for (const p of ["/internal/", "/internal/unknown", "/internal/bi-refresh/other", "/internal/beds24/booking-webhook/x"]) {
    await assertGated404(env, p);
  }
  assert.equal(env.calls.assets, 0, "an unknown /internal path must not fall through to the SPA index");
  // With the header it behaves as before (static fallback).
  assert.equal(await (await call(env, "/internal/unknown", { headers: withGate() })).text(), "ASSET");
});

await check("path normalization tricks do not slip past the gate", async () => {
  const env = makeEnv({ BI_ADMIN_GATE_SECRET: GATE_SECRET });
  for (const p of ["/internal/../api/snapshot", "/health/../api/snapshot", "//api/snapshot", "/api//snapshot",
    "/api/snapshot/", "/%61pi/snapshot", "/data/../data/bi_snapshot.json", "/data/months/2026-07/../../bi_snapshot.json"]) {
    const r = await call(env, p);
    assert.equal(r.status, 404, p);
    assert.equal(await r.text(), "Not Found", p);
  }
  assert.equal(env.calls.assets + env.calls.r2, 0);
});

// ---------------------------------------------------------------- static asset bypass guards (config)
function readText(rel) { return readFileSync(path.join(dir, rel), "utf-8"); }

await check("wrangler.toml: [assets] runs the Worker first so static files cannot bypass the gate", async () => {
  const lines = readText("../wrangler.toml").split("\n");
  const start = lines.findIndex((l) => l.trim() === "[assets]");
  assert.ok(start >= 0, "[assets] section must exist");
  const body = [];
  for (const line of lines.slice(start + 1)) {
    if (line.trim().startsWith("[")) break;
    if (!line.trim().startsWith("#")) body.push(line);
  }
  const text = body.join("\n");
  assert.match(text, /^run_worker_first\s*=\s*true\s*$/m);
  assert.match(text, /^binding\s*=\s*"ASSETS"\s*$/m);
});

await check("wrangler.toml: no gate secret value is ever configured in the file", async () => {
  const toml = readText("../wrangler.toml");
  const active = toml.split("\n").filter((l) => !l.trim().startsWith("#")).join("\n");
  assert.ok(!/BI_ADMIN_GATE_SECRET\s*=/.test(active), "BI_ADMIN_GATE_SECRET must never be assigned in wrangler.toml");
  assert.ok(!/x-kiraku-admin-gate\s*=/i.test(active));
});

await check("public/.assetsignore excludes public/data/ so published BI data can never ship as static assets", async () => {
  const lines = readText("../public/.assetsignore").split("\n").map((l) => l.trim()).filter((l) => l && !l.startsWith("#"));
  assert.ok(lines.includes("data/") || lines.includes("data") || lines.includes("/data/") || lines.includes("/data"),
    `.assetsignore must list data/ (got ${JSON.stringify(lines)})`);
});

console.log(`\n${passed} adminGate checks passed`);
