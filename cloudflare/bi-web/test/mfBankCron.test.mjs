import assert from "node:assert/strict";
import worker from "../src/worker.js";
import { MF_BANK_CRON, mfBankDispatchInputs, dispatchMfBankSync } from "../src/mfBankCron.js";
import { makeCoordinatorNamespace } from "./testDoNamespace.js";

let passed = 0;
async function check(name, fn) { await fn(); passed++; console.log("ok -", name); }
const event = iso => ({ cron: MF_BANK_CRON, scheduledTime: Date.parse(iso) });
async function mockFetch(impl, fn) {
  const original = globalThis.fetch;
  globalThis.fetch = impl;
  try { await fn(); } finally { globalThis.fetch = original; }
}

await check("daily full follows the intended JST03:17 slot even when handled hours later", async () => {
  assert.deepEqual(mfBankDispatchInputs(event("2026-11-16T18:17:00Z")), {
    full: "true", scheduled_at: "2026-11-16T18:17:00.000Z", dispatch_id: "mf-2026111618",
    full_planned_at: "2026-11-16T18:17:00.000Z",
  });
  for (const hour of [0, 17, 19, 23]) {
    assert.equal(mfBankDispatchInputs(event(`2026-11-16T${String(hour).padStart(2, "0")}:17:00Z`)).full, "false");
  }
});

await check("night maintenance defers JST01-05 without network calls or BI access", async () => {
  const env = {MF_NIGHTLY_MAINTENANCE: "true",
    get BI_REFRESH_COORDINATOR() { throw new Error("must not access BI"); }};
  await mockFetch(async () => { throw new Error("must not fetch during maintenance"); }, async () => {
    for (const hour of [16, 17, 18, 19, 20]) {
      let pending;
      await worker.scheduled(event(`2026-11-16T${hour}:17:39.696Z`), env, {waitUntil(p) { pending = p; }});
      assert.equal((await pending).deferred, true);
    }
  });
});

await check("JST06:17 executes the deferred JST03:17 full and JST07:17 returns to normal", async () => {
  const env = {MF_NIGHTLY_MAINTENANCE: "true", GITHUB_ACTIONS_DISPATCH_TOKEN: "synthetic-token"};
  const requests = [];
  await mockFetch(async (url, init) => {
    if (url.includes("/jobs?")) return Response.json({jobs: [{name: "sync-mf-banks", conclusion: "success", steps:
      ["Restore private ledger", "Fetch bank records from MF", "Commit private ledger generation"].map(name => ({name, conclusion: "success"}))}]});
    if (!init.method) return Response.json({workflow_runs: requests.length ? [{id: 123, head_branch: "main", conclusion: "success", display_title: "MF banks | full@2026-11-16T18:17:00.000Z"}] : []});
    requests.push(JSON.parse(init.body)); return new Response(null, {status: 204});
  }, async () => {
    await dispatchMfBankSync(event("2026-11-16T21:17:39.696Z"), env);
    await dispatchMfBankSync(event("2026-11-16T22:17:39.696Z"), env);
  });
  assert.deepEqual(requests[0].inputs, {full: "true", scheduled_at: "2026-11-16T21:17:39.696Z",
    dispatch_id: "mf-2026111621", full_planned_at: "2026-11-16T18:17:00.000Z"});
  assert.equal(requests[1].inputs.full, "false");
  assert.equal(requests[1].inputs.full_planned_at, "");
});

await check("a failed/skip full or unavailable metadata keeps the original daily full pending", async () => {
  const env = {MF_NIGHTLY_MAINTENANCE: "true", GITHUB_ACTIONS_DISPATCH_TOKEN: "synthetic-token"};
  for (const mode of ["no_success", "green_skip", "metadata_unavailable"]) {
    let body;
    await mockFetch(async (url, init) => {
      if (init.method === "POST") { body = JSON.parse(init.body); return new Response(null, {status: 204}); }
      if (mode === "metadata_unavailable") return new Response("sensitive text must not be logged", {status: 503});
      if (url.includes("/jobs?")) return Response.json({jobs: [{name: "sync-mf-banks", conclusion: "success", steps: []}]});
      return Response.json({workflow_runs: mode === "no_success" ? [] : [{id: 123, head_branch: "main", conclusion: "success", display_title: "full@2026-11-16T18:17:00.000Z"}]});
    }, async () => { await dispatchMfBankSync(event("2026-11-16T22:17:39.696Z"), env); });
    assert.equal(body.inputs.full, "true");
    assert.equal(body.inputs.full_planned_at, "2026-11-16T18:17:00.000Z");
  }
});

await check("turning off temporary maintenance restores the original JST03:17 full", async () => {
  assert.equal(mfBankDispatchInputs(event("2026-11-16T18:17:39.696Z"), {MF_NIGHTLY_MAINTENANCE: "false"}).full, "true");
  assert.equal(mfBankDispatchInputs(event("2026-11-16T21:17:39.696Z"), {MF_NIGHTLY_MAINTENANCE: "false"}).full, "false");
});

await check("the MF maintenance flag never pauses the existing BI cron", async () => {
  let target, pending;
  await mockFetch(async url => { target = url; return new Response(null, {status: 204}); }, async () => {
    await worker.scheduled({cron: "3,18,33,48 * * * *", scheduledTime: Date.parse("2026-11-16T16:18:00Z")}, {
      MF_NIGHTLY_MAINTENANCE: "true", GITHUB_ACTIONS_DISPATCH_TOKEN: "synthetic-token",
      BI_REFRESH_COORDINATOR: makeCoordinatorNamespace(),
    }, {waitUntil(p) { pending = p; }});
    await pending;
  });
  assert.equal(target, "https://api.github.com/repos/ginisato-hash/kiraku/actions/workflows/refresh-bi-r2.yml/dispatches");
});

await check("invalid or unrelated scheduled events cannot dispatch", async () => {
  for (const input of [{}, {cron: MF_BANK_CRON, scheduledTime: NaN},
    {...event("2026-11-16T18:17:00Z"), cron: "3,18,33,48 * * * *"},
    event("2026-11-16T18:18:00Z")]) assert.throws(() => mfBankDispatchInputs(input));
});

await check("the hourly tick targets only MF main, never invokes BI, and is awaited", async () => {
  let captured, pending;
  const env = {
    GITHUB_ACTIONS_DISPATCH_TOKEN: "synthetic-token",
    get BI_REFRESH_COORDINATOR() { throw new Error("MF must not access BI coordinator"); },
    get BI_DATA() { throw new Error("MF must not access public BI data"); },
  };
  await mockFetch(async (url, init) => { captured = {url, init}; return new Response(null, {status: 204}); }, async () => {
    await worker.scheduled(event("2026-11-16T18:17:00Z"), env, {waitUntil(p) { pending = p; }});
    assert(pending); await pending;
  });
  assert.equal(captured.url, "https://api.github.com/repos/ginisato-hash/kiraku/actions/workflows/sync-mf-banks.yml/dispatches");
  assert.equal(captured.init.method, "POST");
  assert.equal(captured.init.headers.Authorization, "Bearer synthetic-token");
  assert.deepEqual(JSON.parse(captured.init.body), {ref: "main", inputs: mfBankDispatchInputs(event("2026-11-16T18:17:00Z"))});
  assert(captured.init.signal instanceof AbortSignal);
});

await check("dispatch failures fail the scheduled task without returning secrets or response bodies", async () => {
  const token = "synthetic-sensitive-token";
  await mockFetch(async () => new Response(token, {status: 401}), async () => {
    await assert.rejects(dispatchMfBankSync(event("2026-11-16T10:17:00Z"), {GITHUB_ACTIONS_DISPATCH_TOKEN: token}), {message: "mf_dispatch_rejected_401"});
  });
  await mockFetch(async () => { throw new Error(token); }, async () => {
    await assert.rejects(dispatchMfBankSync(event("2026-11-16T10:17:00Z"), {GITHUB_ACTIONS_DISPATCH_TOKEN: token}), {message: "mf_dispatch_network_failed"});
  });
  await mockFetch(async () => { throw new Error("must not fetch"); }, async () => {
    await assert.rejects(dispatchMfBankSync(event("2026-11-16T10:17:00Z"), {}), {message: "mf_dispatch_token_missing"});
  });
});

await check("public HTTP cannot invoke MF or select an arbitrary workflow", async () => {
  await mockFetch(async () => { throw new Error("HTTP must not dispatch"); }, async () => {
    const result = await worker.fetch(new Request("https://example.test/internal/mf-bank-sync?workflow=evil.yml", {method: "POST"}), {
      GITHUB_ACTIONS_DISPATCH_TOKEN: "synthetic-token", ASSETS: {fetch: async () => new Response("not found", {status: 404})},
    });
    assert.equal(result.status, 404);
  });
});

console.log(`\n${passed} MF cron checks passed`);
