import assert from "node:assert/strict";
import worker from "../src/worker.js";
import { MF_BANK_CRON, mfBankDispatchInputs, dispatchMfBankSync } from "../src/mfBankCron.js";

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
  });
  for (const hour of [0, 17, 19, 23]) {
    assert.equal(mfBankDispatchInputs(event(`2026-11-16T${String(hour).padStart(2, "0")}:17:00Z`)).full, "false");
  }
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
