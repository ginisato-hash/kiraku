// biRefreshCron.test.mjs — worker.js scheduled() gating through
// BiRefreshCoordinator: shadow vs active mode, clean/dirty, full
// reconciliation, in-flight, and GitHub dispatch API failure handling.
import assert from "node:assert";
import worker from "../src/worker.js";
import { makeCoordinatorNamespace } from "./testDoNamespace.js";

function withMockFetch(impl, fn) {
  const original = globalThis.fetch;
  globalThis.fetch = impl;
  return fn().finally(() => { globalThis.fetch = original; });
}

function makeCtx() {
  const ctx = { waitUntil: (p) => { ctx.pending = p; } };
  return ctx;
}

async function runCron(env, ctx, scheduledTimeIso) {
  await worker.scheduled(
    { cron: "3,18,33,48 * * * *", scheduledTime: Date.parse(scheduledTimeIso) }, env, ctx);
  await ctx.pending;
}

async function coordinatorStatus(env) {
  const stub = env.BI_REFRESH_COORDINATOR.get("singleton");
  const res = await stub.fetch(new Request("https://do/internal/status"));
  return res.json();
}

let passed = 0;
async function check(name, fn) { await fn(); passed++; console.log("ok -", name); }

// ---------------------------------------------------------------- active, clean
await check("active mode + clean coordinator (freshly reconciled) -> no GitHub API call at all", async () => {
  const namespace = makeCoordinatorNamespace({ BI_REFRESH_DEFAULT_MODE: "active" });
  const env = { GITHUB_ACTIONS_DISPATCH_TOKEN: "x", BI_REFRESH_COORDINATOR: namespace };
  // Prime a fresh successful completion so full-reconciliation/day-rollover don't fire.
  const stub = namespace.get("singleton");
  await stub.fetch(new Request("https://do/internal/complete", {
    method: "POST", body: JSON.stringify({ dispatch_id: "boot", target_seq: 0, status: "success", completed_at: "2026-09-20T00:00:00.000Z" }),
  }));

  let calls = 0;
  await withMockFetch(async () => { calls++; return new Response(null, { status: 204 }); }, async () => {
    await runCron(env, makeCtx(), "2026-09-20T00:03:00Z"); // JST 09:03, same JST day
  });
  assert.equal(calls, 0, "active+clean must not call the GitHub API at all — this is the actual Actions-count savings");
});

// ---------------------------------------------------------------- active, dirty
await check("active mode + a pending webhook -> dispatches exactly once with dispatch_id/target_seq/reason inputs", async () => {
  const namespace = makeCoordinatorNamespace({ BI_REFRESH_DEFAULT_MODE: "active" });
  const env = { GITHUB_ACTIONS_DISPATCH_TOKEN: "x", BI_REFRESH_COORDINATOR: namespace };
  const stub = namespace.get("singleton");
  await stub.fetch(new Request("https://do/internal/complete", {
    method: "POST", body: JSON.stringify({ dispatch_id: "boot", target_seq: 0, status: "success", completed_at: "2026-09-20T00:00:00.000Z" }),
  }));
  await stub.fetch(new Request("https://do/internal/event", { method: "POST" }));

  let captured = null;
  await withMockFetch(async (url, init) => { captured = { url, init }; return new Response(null, { status: 204 }); }, async () => {
    await runCron(env, makeCtx(), "2026-09-20T00:18:00Z");
  });
  assert.ok(captured, "GitHub API must be called exactly once");
  const body = JSON.parse(captured.init.body);
  assert.equal(body.ref, "main");
  assert.equal(body.inputs.reason, "booking_webhook");
  assert.equal(body.inputs.target_seq, "1");
  assert.ok(body.inputs.dispatch_id);

  const status = await coordinatorStatus(env);
  assert.ok(status.in_flight, "the reservation must be recorded as in-flight until the completion callback arrives");
});

// ---------------------------------------------------------------- full reconcile
await check("active mode fires a full-reconciliation dispatch once the max age is exceeded, with zero webhooks", async () => {
  const namespace = makeCoordinatorNamespace({ BI_REFRESH_DEFAULT_MODE: "active", FULL_RECONCILE_MAX_AGE_SECONDS: "3600" });
  const env = { GITHUB_ACTIONS_DISPATCH_TOKEN: "x", BI_REFRESH_COORDINATOR: namespace };
  const stub = namespace.get("singleton");
  await stub.fetch(new Request("https://do/internal/complete", {
    method: "POST", body: JSON.stringify({ dispatch_id: "boot", target_seq: 0, status: "success", completed_at: "2026-09-20T00:00:00.000Z" }),
  }));

  let captured = null;
  await withMockFetch(async (url, init) => { captured = { url, init }; return new Response(null, { status: 204 }); }, async () => {
    await runCron(env, makeCtx(), "2026-09-20T01:05:00Z"); // > 3600s since last reconcile
  });
  assert.ok(captured);
  assert.equal(JSON.parse(captured.init.body).inputs.reason, "full_reconciliation");
});

// ---------------------------------------------------------------- in-flight
await check("active mode skips dispatching again while a previous dispatch is still in-flight (not yet stale)", async () => {
  const namespace = makeCoordinatorNamespace({ BI_REFRESH_DEFAULT_MODE: "active" });
  const env = { GITHUB_ACTIONS_DISPATCH_TOKEN: "x", BI_REFRESH_COORDINATOR: namespace };
  const stub = namespace.get("singleton");
  await stub.fetch(new Request("https://do/internal/complete", {
    method: "POST", body: JSON.stringify({ dispatch_id: "boot", target_seq: 0, status: "success", completed_at: "2026-09-20T00:00:00.000Z" }),
  }));
  await stub.fetch(new Request("https://do/internal/event", { method: "POST" }));

  let calls = 0;
  await withMockFetch(async () => { calls++; return new Response(null, { status: 204 }); }, async () => {
    await runCron(env, makeCtx(), "2026-09-20T00:18:00Z"); // reserves + dispatches
    await stub.fetch(new Request("https://do/internal/event", { method: "POST" })); // event C arrives mid-flight
    await runCron(env, makeCtx(), "2026-09-20T00:33:00Z"); // must skip: still in-flight
  });
  assert.equal(calls, 1, "only the first tick should call the GitHub API; the second must skip while in-flight");
});

// ---------------------------------------------------------------- dispatch API failure
await check("a GitHub dispatch API failure releases the reservation instead of leaving it stuck for the full lease", async () => {
  const namespace = makeCoordinatorNamespace({ BI_REFRESH_DEFAULT_MODE: "active" });
  const env = { GITHUB_ACTIONS_DISPATCH_TOKEN: "x", BI_REFRESH_COORDINATOR: namespace };
  const stub = namespace.get("singleton");
  await stub.fetch(new Request("https://do/internal/complete", {
    method: "POST", body: JSON.stringify({ dispatch_id: "boot", target_seq: 0, status: "success", completed_at: "2026-09-20T00:00:00.000Z" }),
  }));
  await stub.fetch(new Request("https://do/internal/event", { method: "POST" }));

  await withMockFetch(async () => new Response("server error", { status: 500 }), async () => {
    await runCron(env, makeCtx(), "2026-09-20T00:18:00Z");
  });

  const status = await coordinatorStatus(env);
  assert.equal(status.in_flight, null, "a failed GitHub API call must release the in-flight reservation immediately");
  assert.equal(status.consecutive_failures, 1);
  assert.equal(status.dirty_count, 1, "the underlying event must still be pending so the next tick retries it");

  // Next tick, GitHub API now succeeds: must retry and succeed.
  let captured = null;
  await withMockFetch(async (url, init) => { captured = { url, init }; return new Response(null, { status: 204 }); }, async () => {
    await runCron(env, makeCtx(), "2026-09-20T00:33:00Z");
  });
  assert.ok(captured, "the next cron tick must retry the released dispatch");
});

console.log(`\n${passed} BI refresh cron-gating checks passed`);
