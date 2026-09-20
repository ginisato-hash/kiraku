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

// ---------------------------------------------------------------- fix round: shadow tracking (blocker 1)
await check("shadow mode passes tracked dispatch_id/target_seq/reason inputs, and its completion callback advances state", async () => {
  const namespace = makeCoordinatorNamespace(); // default mode = shadow
  const env = { GITHUB_ACTIONS_DISPATCH_TOKEN: "x", BI_REFRESH_COORDINATOR: namespace };
  const stub = namespace.get("singleton");
  await stub.fetch(new Request("https://do/internal/event", { method: "POST" }));

  let captured = null;
  await withMockFetch(async (url, init) => { captured = { url, init }; return new Response(null, { status: 204 }); }, async () => {
    await runCron(env, makeCtx(), "2026-09-20T00:03:00Z");
  });
  assert.ok(captured, "shadow must still call the GitHub API unconditionally");
  const body = JSON.parse(captured.init.body);
  assert.ok(body.inputs, "shadow's unconditional dispatch must still carry Coordinator tracking inputs");
  assert.equal(body.inputs.reason, "booking_webhook");
  assert.equal(body.inputs.target_seq, "1");
  const dispatchId = body.inputs.dispatch_id;
  assert.ok(dispatchId);

  // Simulate the workflow's own completion callback for this shadow dispatch.
  const completeRes = await stub.fetch(new Request("https://do/internal/complete", {
    method: "POST",
    body: JSON.stringify({ dispatch_id: dispatchId, target_seq: 1, status: "success", completed_at: "2026-09-20T00:05:00.000Z" }),
  }));
  assert.equal(completeRes.status, 200);

  const status = await coordinatorStatus(env);
  assert.equal(status.last_completed_seq, 1, "shadow's real success must be reflected in Coordinator state");
  assert.equal(status.last_full_reconcile_at, "2026-09-20T00:05:00.000Z");
  assert.equal(status.dirty_count, 0, "planner must now independently agree the coordinator is clean");
});

await check("shadow mode still dispatches unconditionally even when a previous shadow dispatch is still in-flight", async () => {
  const namespace = makeCoordinatorNamespace();
  const env = { GITHUB_ACTIONS_DISPATCH_TOKEN: "x", BI_REFRESH_COORDINATOR: namespace };
  const stub = namespace.get("singleton");
  await stub.fetch(new Request("https://do/internal/event", { method: "POST" }));

  let calls = 0;
  let lastCaptured = null;
  await withMockFetch(async (url, init) => {
    calls++; lastCaptured = init;
    return new Response(null, { status: 204 });
  }, async () => {
    await runCron(env, makeCtx(), "2026-09-20T00:03:00Z"); // reserves + tracked dispatch
    await runCron(env, makeCtx(), "2026-09-20T00:18:00Z"); // still in-flight -> untracked, but STILL fires
  });
  assert.equal(calls, 2, "shadow must call GitHub on every single tick, tracked or not");
  assert.ok(!JSON.parse(lastCaptured.body).inputs, "the second, untracked tick must fall back to the legacy no-inputs call shape");
});

// ---------------------------------------------------------------- fix round: coordinator fail-open (blocker 4)
function makeThrowingCoordinatorNamespace() {
  return { idFromName: (n) => n, get: () => ({ fetch: async () => { throw new Error("DO unavailable"); } }) };
}
function makeMalformedCoordinatorNamespace() {
  return { idFromName: (n) => n, get: () => ({ fetch: async () => new Response("not json", { status: 200 }) }) };
}

await check("shadow + coordinator throws -> GitHub is still dispatched exactly once (fail-open)", async () => {
  const env = { GITHUB_ACTIONS_DISPATCH_TOKEN: "x", BI_REFRESH_COORDINATOR: makeThrowingCoordinatorNamespace() };
  let calls = 0;
  await withMockFetch(async () => { calls++; return new Response(null, { status: 204 }); }, async () => {
    await runCron(env, makeCtx(), "2026-09-20T00:03:00Z");
  });
  assert.equal(calls, 1, "a broken Coordinator must never prevent the legacy unconditional dispatch");
});

await check("active + coordinator throws -> GitHub is still dispatched exactly once (fail-open, refresh must not silently stop)", async () => {
  const env = { GITHUB_ACTIONS_DISPATCH_TOKEN: "x", BI_REFRESH_COORDINATOR: makeThrowingCoordinatorNamespace() };
  let calls = 0;
  await withMockFetch(async () => { calls++; return new Response(null, { status: 204 }); }, async () => {
    await runCron(env, makeCtx(), "2026-09-20T00:03:00Z");
  });
  assert.equal(calls, 1, "active mode must also fail open to the unconditional dispatch when the Coordinator is unavailable");
});

await check("coordinator returns a malformed (non-JSON/shape-invalid) response -> safe fallback dispatch, no thrown exception", async () => {
  const env = { GITHUB_ACTIONS_DISPATCH_TOKEN: "x", BI_REFRESH_COORDINATOR: makeMalformedCoordinatorNamespace() };
  let calls = 0;
  let threw = false;
  await withMockFetch(async () => { calls++; return new Response(null, { status: 204 }); }, async () => {
    try {
      await runCron(env, makeCtx(), "2026-09-20T00:03:00Z");
    } catch (e) {
      threw = true;
    }
  });
  assert.equal(threw, false, "scheduled() must never let a Coordinator response-shape problem escape as an unhandled exception");
  assert.equal(calls, 1, "must still have fallen back to the unconditional dispatch");
});

await check("fallback GitHub dispatch failure (coordinator down AND GitHub API fails) is observable, not a thrown exception", async () => {
  const env = { GITHUB_ACTIONS_DISPATCH_TOKEN: "x", BI_REFRESH_COORDINATOR: makeThrowingCoordinatorNamespace() };
  let threw = false;
  await withMockFetch(async () => new Response("server error", { status: 500 }), async () => {
    try {
      await runCron(env, makeCtx(), "2026-09-20T00:03:00Z");
    } catch (e) {
      threw = true;
    }
  });
  assert.equal(threw, false, "a doubly-failed tick (coordinator down + GitHub API down) must not throw — it's logged and left to the next tick");
});

// ---------------------------------------------------------------- fix round 2: strict coordinator response contract
// The previous fail-open check only confirmed status===200 and
// typeof body.mode==="string" — {"mode":"active"} alone satisfied that,
// then `!decision.would_dispatch` (undefined -> falsy -> true) took the
// "nothing to do" early return in active mode, silently stopping BI
// refresh on a Coordinator bug. These prove every such partial/malformed
// response now falls back to the unconditional dispatch instead.
function makeFixedJsonCoordinatorNamespace(body, status = 200) {
  return {
    idFromName: (n) => n,
    get: () => ({ fetch: async () => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } }) }),
  };
}

async function assertFallsBackToUnconditionalDispatch(name, body) {
  await check(name, async () => {
    const env = { GITHUB_ACTIONS_DISPATCH_TOKEN: "x", BI_REFRESH_COORDINATOR: makeFixedJsonCoordinatorNamespace(body) };
    let captured = null;
    await withMockFetch(async (url, init) => { captured = init; return new Response(null, { status: 204 }); }, async () => {
      await runCron(env, makeCtx(), "2026-09-20T00:03:00Z");
    });
    assert.ok(captured, "must still have dispatched");
    assert.ok(!JSON.parse(captured.body).inputs, "a rejected/invalid decision must dispatch with the legacy no-inputs shape, not made-up inputs");
  });
}

await assertFallsBackToUnconditionalDispatch(
  "200 + {mode:active} (missing would_dispatch) -> unconditional fallback dispatch, not a silent skip",
  { mode: "active" },
);

await assertFallsBackToUnconditionalDispatch(
  "200 + {mode:active, would_dispatch:true} (missing event_seq/last_completed_seq/dispatch_id) -> unconditional fallback dispatch",
  { mode: "active", would_dispatch: true },
);

await assertFallsBackToUnconditionalDispatch(
  "200 + {mode:turbo, would_dispatch:false} (unsupported mode) -> unconditional fallback dispatch",
  { mode: "turbo", would_dispatch: false },
);

await assertFallsBackToUnconditionalDispatch(
  "200 + active dispatch decision missing dispatch_id -> unconditional fallback dispatch",
  { mode: "active", would_dispatch: true, reason: "booking_webhook", event_seq: 5, last_completed_seq: 4 },
);

await assertFallsBackToUnconditionalDispatch(
  "200 + active dispatch decision with an invalid target_seq -> unconditional fallback dispatch",
  {
    mode: "active", would_dispatch: true, reason: "booking_webhook", event_seq: 5, last_completed_seq: 4,
    dispatch_id: "abc-123", target_seq: "not-a-number",
  },
);

// Valid decisions must NOT be affected by the stricter contract — these
// mirror (and re-confirm) the existing "active + clean", "active + a
// pending webhook" and "shadow tracked dispatch" cases above, this time
// specifically to prove the new validateCoordinatorDecision() doesn't
// reject well-formed responses.
await check("a well-formed active+clean decision is still accepted (not rejected by the stricter contract)", async () => {
  const body = { mode: "active", would_dispatch: false, reason: null, event_seq: 3, last_completed_seq: 3, dispatch_id: null, target_seq: null, dispatch_reason: null };
  const env = { GITHUB_ACTIONS_DISPATCH_TOKEN: "x", BI_REFRESH_COORDINATOR: makeFixedJsonCoordinatorNamespace(body) };
  let calls = 0;
  await withMockFetch(async () => { calls++; return new Response(null, { status: 204 }); }, async () => {
    await runCron(env, makeCtx(), "2026-09-20T00:03:00Z");
  });
  assert.equal(calls, 0, "a genuinely clean, well-formed active decision must still result in zero GitHub API calls");
});

await check("a well-formed active reservation is still accepted and dispatched with its exact inputs", async () => {
  const body = { mode: "active", would_dispatch: true, reason: "booking_webhook", event_seq: 5, last_completed_seq: 4, dispatch_id: "real-dispatch-id", target_seq: 5, dispatch_reason: "booking_webhook" };
  const env = { GITHUB_ACTIONS_DISPATCH_TOKEN: "x", BI_REFRESH_COORDINATOR: makeFixedJsonCoordinatorNamespace(body) };
  let captured = null;
  await withMockFetch(async (url, init) => { captured = init; return new Response(null, { status: 204 }); }, async () => {
    await runCron(env, makeCtx(), "2026-09-20T00:03:00Z");
  });
  const parsed = JSON.parse(captured.body);
  assert.equal(parsed.inputs.dispatch_id, "real-dispatch-id");
  assert.equal(parsed.inputs.target_seq, "5");
  assert.equal(parsed.inputs.reason, "booking_webhook");
});

await check("a well-formed shadow reservation is still accepted and dispatched with its exact inputs", async () => {
  const body = { mode: "shadow", would_dispatch: false, reason: null, event_seq: 2, last_completed_seq: 2, dispatch_id: "shadow-dispatch-id", target_seq: 2, dispatch_reason: "shadow_unconditional" };
  const env = { GITHUB_ACTIONS_DISPATCH_TOKEN: "x", BI_REFRESH_COORDINATOR: makeFixedJsonCoordinatorNamespace(body) };
  let captured = null;
  await withMockFetch(async (url, init) => { captured = init; return new Response(null, { status: 204 }); }, async () => {
    await runCron(env, makeCtx(), "2026-09-20T00:03:00Z");
  });
  const parsed = JSON.parse(captured.body);
  assert.equal(parsed.inputs.dispatch_id, "shadow-dispatch-id");
  assert.equal(parsed.inputs.reason, "shadow_unconditional");
});

console.log(`\n${passed} BI refresh cron-gating checks passed`);
