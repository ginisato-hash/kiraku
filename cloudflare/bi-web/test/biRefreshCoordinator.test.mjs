// biRefreshCoordinator.test.mjs — BiRefreshCoordinator Durable Object state
// machine. Instantiates the class directly against a fake in-memory
// state.storage (same pattern as staff-ops's DO tests) — no Miniflare
// needed since this DO never uses anything beyond storage.get/put.
import assert from "node:assert";
import { BiRefreshCoordinator } from "../src/biRefreshCoordinator.js";

function makeDoStorage() {
  const map = new Map();
  return {
    async get(key) { return map.has(key) ? map.get(key) : undefined; },
    async put(key, value) { map.set(key, value); },
  };
}

function makeCoordinator(env = {}) {
  return new BiRefreshCoordinator({ storage: makeDoStorage() }, env);
}

async function post(coord, path, body) {
  const res = await coord.fetch(new Request(`https://do${path}`, {
    method: "POST", body: body === undefined ? undefined : JSON.stringify(body),
  }));
  return { status: res.status, body: await res.json() };
}

async function get(coord, path) {
  const res = await coord.fetch(new Request(`https://do${path}`));
  return { status: res.status, body: await res.json() };
}

const ACTIVE_ENV = { BI_REFRESH_DEFAULT_MODE: "active" };
const SHADOW_ENV = { BI_REFRESH_DEFAULT_MODE: "shadow" };

let passed = 0;
async function check(name, fn) { await fn(); passed++; console.log("ok -", name); }

// ---------------------------------------------------------------- clean state
await check("clean state (no webhooks, fresh reconcile) -> would_dispatch=false in active mode", async () => {
  const coord = makeCoordinator(ACTIVE_ENV);
  const now = new Date().toISOString();
  // Prime last_full_reconcile_at/last_successful_jst_date so the bootstrap
  // full-reconciliation branch doesn't fire — simulate "just ran".
  await post(coord, "/internal/complete", { dispatch_id: "00000000-0000-4000-8000-000000000000", target_seq: 0, status: "success", completed_at: now });
  const r = await post(coord, "/internal/evaluate", { now_iso: now, today_jst: "2026-09-20" });
  assert.equal(r.body.would_dispatch, false);
  assert.equal(r.body.reason, null);
});

// ---------------------------------------------------------------- webhook -> seq increment
await check("a webhook event increments event_seq by exactly 1 and records last_webhook_at", async () => {
  const coord = makeCoordinator();
  const r1 = await post(coord, "/internal/event");
  assert.equal(r1.body.event_seq, 1);
  const r2 = await post(coord, "/internal/event");
  assert.equal(r2.body.event_seq, 2);
  const status = await get(coord, "/internal/status");
  assert.ok(status.body.last_webhook_at);
});

// ---------------------------------------------------------------- multiple webhooks -> one dispatch
await check("10 webhooks in a window collapse into exactly one dispatch reservation for the latest seq", async () => {
  const coord = makeCoordinator(ACTIVE_ENV);
  for (let i = 0; i < 10; i++) await post(coord, "/internal/event");
  const r = await post(coord, "/internal/evaluate", { today_jst: "2026-09-20" });
  assert.equal(r.body.would_dispatch, true);
  assert.equal(r.body.reason, "booking_webhook");
  assert.equal(r.body.target_seq, 10);
  assert.ok(r.body.dispatch_id);
  // A second evaluate before completion must NOT reserve a second dispatch.
  const r2 = await post(coord, "/internal/evaluate", { today_jst: "2026-09-20" });
  assert.equal(r2.body.would_dispatch, false);
  assert.equal(r2.body.reason, "in_flight");
});

// ---------------------------------------------------------------- dispatch reservation shape
await check("a reservation carries a unique dispatch_id and the event_seq snapshot as target_seq", async () => {
  const coord = makeCoordinator(ACTIVE_ENV);
  await post(coord, "/internal/event");
  await post(coord, "/internal/event");
  const r = await post(coord, "/internal/evaluate", { now_iso: "2026-09-20T00:00:00.000Z", today_jst: "2026-09-20" });
  assert.equal(r.body.target_seq, 2);
  assert.match(r.body.dispatch_id, /^[0-9a-f-]{36}$/);
  const status = await get(coord, "/internal/status");
  assert.deepEqual(status.body.in_flight.dispatch_id, r.body.dispatch_id);
  assert.equal(status.body.in_flight.target_seq, 2);
  assert.equal(status.body.in_flight.reason, "booking_webhook");
});

// ---------------------------------------------------------------- completion success
await check("completion success advances last_completed_seq and clears in_flight", async () => {
  const coord = makeCoordinator(ACTIVE_ENV);
  await post(coord, "/internal/event");
  const r = await post(coord, "/internal/evaluate", { today_jst: "2026-09-20" });
  const complete = await post(coord, "/internal/complete", {
    dispatch_id: r.body.dispatch_id, target_seq: r.body.target_seq,
    status: "success", reason: "booking_webhook", completed_at: "2026-09-20T01:00:00.000Z",
  });
  assert.equal(complete.body.ok, true);
  assert.equal(complete.body.last_completed_seq, 1);
  const status = await get(coord, "/internal/status");
  assert.equal(status.body.in_flight, null);
  assert.equal(status.body.last_completed_seq, 1);
  assert.equal(status.body.last_success_at, "2026-09-20T01:00:00.000Z");
  assert.equal(status.body.last_successful_jst_date, "2026-09-20");
  assert.equal(status.body.consecutive_failures, 0);
});

// ---------------------------------------------------------------- completion failure
await check("completion failure does NOT advance last_completed_seq, clears in_flight, bumps failures", async () => {
  const coord = makeCoordinator(ACTIVE_ENV);
  await post(coord, "/internal/event");
  const r = await post(coord, "/internal/evaluate", { today_jst: "2026-09-20" });
  const complete = await post(coord, "/internal/complete", {
    dispatch_id: r.body.dispatch_id, target_seq: r.body.target_seq, status: "failure",
  });
  assert.equal(complete.body.last_completed_seq, 0);
  const status = await get(coord, "/internal/status");
  assert.equal(status.body.in_flight, null, "failure still releases the lease for immediate retry");
  assert.equal(status.body.consecutive_failures, 1);
  assert.ok(status.body.last_failure_at);
});

// ---------------------------------------------------------------- new webhook while in-flight (the race the whole design exists for)
await check("a webhook arriving while a dispatch is in-flight is not lost (race-condition proof)", async () => {
  const coord = makeCoordinator(ACTIVE_ENV);
  await post(coord, "/internal/event"); // event_seq=1
  const r = await post(coord, "/internal/evaluate", { now_iso: "2026-09-20T00:00:00.000Z", today_jst: "2026-09-20" });
  assert.equal(r.body.target_seq, 1);

  await post(coord, "/internal/event"); // event B arrives mid-flight -> event_seq=2

  await post(coord, "/internal/complete", {
    dispatch_id: r.body.dispatch_id, target_seq: r.body.target_seq, status: "success",
    completed_at: "2026-09-20T00:02:00.000Z",
  });

  const afterFirst = await get(coord, "/internal/status");
  assert.equal(afterFirst.body.last_completed_seq, 1);
  assert.equal(afterFirst.body.event_seq, 2);
  assert.equal(afterFirst.body.dirty_count, 1, "event B's signal must survive — this is exactly what boolean dirty would have lost");

  const r2 = await post(coord, "/internal/evaluate", { now_iso: "2026-09-20T00:03:00.000Z", today_jst: "2026-09-20" });
  assert.equal(r2.body.would_dispatch, true);
  assert.equal(r2.body.reason, "booking_webhook");
  assert.equal(r2.body.target_seq, 2);
});

// ---------------------------------------------------------------- stale lease recovery
await check("an in-flight dispatch past its lease timeout is reclaimed and a new one can be reserved", async () => {
  const coord = makeCoordinator({ ...ACTIVE_ENV, LEASE_TIMEOUT_SECONDS: "60" });
  await post(coord, "/internal/event");
  const r = await post(coord, "/internal/evaluate", { now_iso: "2026-09-20T00:00:00.000Z", today_jst: "2026-09-20" });
  assert.ok(r.body.dispatch_id);

  // Still within lease: no reclaim, no new dispatch.
  const withinLease = await post(coord, "/internal/evaluate", { now_iso: "2026-09-20T00:00:30.000Z", today_jst: "2026-09-20" });
  assert.equal(withinLease.body.would_dispatch, false);
  assert.equal(withinLease.body.stale_reclaimed, false);

  // Past lease timeout: reclaimed and re-reserved for the SAME target_seq
  // (the original event hasn't been superseded, we don't lose it).
  const pastLease = await post(coord, "/internal/evaluate", { now_iso: "2026-09-20T00:02:00.000Z", today_jst: "2026-09-20" });
  assert.equal(pastLease.body.stale_reclaimed, true);
  assert.equal(pastLease.body.would_dispatch, true);
  assert.equal(pastLease.body.target_seq, 1);
  assert.notEqual(pastLease.body.dispatch_id, r.body.dispatch_id, "stale reclaim issues a fresh dispatch_id");

  // The original run's late callback (with the old dispatch_id) must still
  // be accepted (duplicate execution is allowed) without corrupting the
  // NEW in-flight reservation.
  const lateCallback = await post(coord, "/internal/complete", {
    dispatch_id: r.body.dispatch_id, target_seq: 1, status: "success", completed_at: "2026-09-20T00:03:00.000Z",
  });
  assert.equal(lateCallback.body.matched_in_flight, false);
  assert.equal(lateCallback.body.last_completed_seq, 1);
  const status = await get(coord, "/internal/status");
  assert.ok(status.body.in_flight, "the newer (reclaimed) in-flight reservation must remain untouched");
  assert.equal(status.body.in_flight.dispatch_id, pastLease.body.dispatch_id);
});

// ---------------------------------------------------------------- full reconciliation due
await check("full reconciliation fires when the max age is exceeded even with zero webhooks", async () => {
  const coord = makeCoordinator({ ...ACTIVE_ENV, FULL_RECONCILE_MAX_AGE_SECONDS: "3600" });
  await post(coord, "/internal/complete", {
    dispatch_id: "00000000-0000-4000-8000-000000000000", target_seq: 0, status: "success", completed_at: "2026-09-20T00:00:00.000Z",
  });
  const stillFresh = await post(coord, "/internal/evaluate", { now_iso: "2026-09-20T00:30:00.000Z", today_jst: "2026-09-20" });
  assert.equal(stillFresh.body.would_dispatch, false);

  const overdue = await post(coord, "/internal/evaluate", { now_iso: "2026-09-20T01:01:00.000Z", today_jst: "2026-09-20" });
  assert.equal(overdue.body.would_dispatch, true);
  assert.equal(overdue.body.reason, "full_reconciliation");
});

await check("a bootstrap coordinator (never run) treats the missing reconcile timestamp as overdue", async () => {
  const coord = makeCoordinator(ACTIVE_ENV);
  const r = await post(coord, "/internal/evaluate", { today_jst: "2026-09-20" });
  assert.equal(r.body.would_dispatch, true);
  assert.equal(r.body.reason, "full_reconciliation");
});

// ---------------------------------------------------------------- JST day rollover
await check("JST date rollover forces a dispatch even with zero new webhooks and a fresh reconcile", async () => {
  const coord = makeCoordinator({ ...ACTIVE_ENV, FULL_RECONCILE_MAX_AGE_SECONDS: "21600" });
  await post(coord, "/internal/complete", {
    dispatch_id: "00000000-0000-4000-8000-000000000000", target_seq: 0, status: "success", completed_at: "2026-09-19T10:00:00.000Z",
  });
  const sameDayStatus = await get(coord, "/internal/status");
  assert.equal(sameDayStatus.body.last_successful_jst_date, "2026-09-19");

  const rollover = await post(coord, "/internal/evaluate", { now_iso: "2026-09-20T00:05:00.000Z", today_jst: "2026-09-20" });
  assert.equal(rollover.body.would_dispatch, true);
  assert.equal(rollover.body.reason, "jst_date_rollover");
});

// ---------------------------------------------------------------- manual force
await check("manual force triggers a dispatch on the next evaluate and then clears itself", async () => {
  const coord = makeCoordinator(ACTIVE_ENV);
  await post(coord, "/internal/complete", {
    dispatch_id: "00000000-0000-4000-8000-000000000000", target_seq: 0, status: "success", completed_at: "2026-09-20T00:00:00.000Z",
  });
  const clean = await post(coord, "/internal/evaluate", { now_iso: "2026-09-20T00:01:00.000Z", today_jst: "2026-09-20" });
  assert.equal(clean.body.would_dispatch, false);

  await post(coord, "/internal/force");
  const forced = await post(coord, "/internal/evaluate", { now_iso: "2026-09-20T00:02:00.000Z", today_jst: "2026-09-20" });
  assert.equal(forced.body.would_dispatch, true);
  assert.equal(forced.body.reason, "manual_force");

  await post(coord, "/internal/complete", {
    dispatch_id: forced.body.dispatch_id, target_seq: forced.body.target_seq, status: "success",
    completed_at: "2026-09-20T00:03:00.000Z",
  });
  const afterForced = await post(coord, "/internal/evaluate", { now_iso: "2026-09-20T00:04:00.000Z", today_jst: "2026-09-20" });
  assert.equal(afterForced.body.would_dispatch, false, "force_dispatch_requested must clear itself once honored");
});

// ---------------------------------------------------------------- duplicate completion callback
await check("a duplicate completion callback (same dispatch_id, sent twice) is idempotent", async () => {
  const coord = makeCoordinator(ACTIVE_ENV);
  await post(coord, "/internal/event");
  const r = await post(coord, "/internal/evaluate", { today_jst: "2026-09-20" });
  const first = await post(coord, "/internal/complete", { dispatch_id: r.body.dispatch_id, target_seq: 1, status: "success" });
  const second = await post(coord, "/internal/complete", { dispatch_id: r.body.dispatch_id, target_seq: 1, status: "success" });
  assert.equal(first.body.last_completed_seq, 1);
  assert.equal(second.body.last_completed_seq, 1);
  const status = await get(coord, "/internal/status");
  assert.equal(status.body.consecutive_failures, 0);
});

// ---------------------------------------------------------------- wrong dispatch_id callback
await check("a callback with a dispatch_id that doesn't match the current in-flight never clears it", async () => {
  const coord = makeCoordinator(ACTIVE_ENV);
  await post(coord, "/internal/event");
  const r = await post(coord, "/internal/evaluate", { today_jst: "2026-09-20" });
  const wrong = await post(coord, "/internal/complete", { dispatch_id: "99999999-9999-4999-8999-999999999999", target_seq: 0, status: "success" });
  assert.equal(wrong.body.matched_in_flight, false);
  const status = await get(coord, "/internal/status");
  assert.ok(status.body.in_flight, "the real in-flight reservation must still be there");
  assert.equal(status.body.in_flight.dispatch_id, r.body.dispatch_id);
});

// ---------------------------------------------------------------- old completion callback
await check("an old completion callback for an already-superseded seq is a safe no-op (monotonic max)", async () => {
  const coord = makeCoordinator(ACTIVE_ENV);
  for (let i = 0; i < 5; i++) await post(coord, "/internal/event");
  const r1 = await post(coord, "/internal/evaluate", { now_iso: "2026-09-20T00:00:00.000Z", today_jst: "2026-09-20" });
  await post(coord, "/internal/complete", { dispatch_id: r1.body.dispatch_id, target_seq: r1.body.target_seq, status: "success" });
  await post(coord, "/internal/event");
  const r2 = await post(coord, "/internal/evaluate", { now_iso: "2026-09-20T00:10:00.000Z", today_jst: "2026-09-20" });
  await post(coord, "/internal/complete", { dispatch_id: r2.body.dispatch_id, target_seq: r2.body.target_seq, status: "success" });

  // A very late callback for the FIRST (already-superseded) dispatch arrives last.
  const stale = await post(coord, "/internal/complete", { dispatch_id: r1.body.dispatch_id, target_seq: r1.body.target_seq, status: "success" });
  assert.equal(stale.body.last_completed_seq, 6, "must never regress below the already-advanced value");
});

// ---------------------------------------------------------------- input validation
await check("complete rejects malformed payloads with 400 instead of corrupting state", async () => {
  const coord = makeCoordinator(ACTIVE_ENV);
  const noId = await post(coord, "/internal/complete", { target_seq: 1, status: "success" });
  assert.equal(noId.status, 400);
  const badSeq = await post(coord, "/internal/complete", { dispatch_id: "66666666-6666-4666-8666-666666666666", target_seq: "1", status: "success" });
  assert.equal(badSeq.status, 400);
  const badStatus = await post(coord, "/internal/complete", { dispatch_id: "66666666-6666-4666-8666-666666666666", target_seq: 1, status: "maybe" });
  assert.equal(badStatus.status, 400);
});

await check("set-mode rejects an invalid mode value", async () => {
  const coord = makeCoordinator();
  const r = await post(coord, "/internal/mode", { mode: "turbo" });
  assert.equal(r.status, 400);
  const ok = await post(coord, "/internal/mode", { mode: "active" });
  assert.equal(ok.body.mode, "active");
});

// ---------------------------------------------------------------- fix round: shadow state tracking (blocker 1)
await check("shadow: a clean successful unconditional run updates last_full_reconcile_at/last_successful_jst_date", async () => {
  const coord = makeCoordinator({ ...SHADOW_ENV, FULL_RECONCILE_MAX_AGE_SECONDS: "21600" });
  // Prime a very recent success so the bootstrap/full-reconcile-overdue
  // branch doesn't fire — isolates the "genuinely clean" case.
  await post(coord, "/internal/complete", {
    dispatch_id: "00000000-0000-4000-8000-000000000000", target_seq: 0, status: "success", completed_at: "2026-09-20T00:00:00.000Z",
  });

  const r = await post(coord, "/internal/evaluate", { now_iso: "2026-09-20T00:01:00.000Z", today_jst: "2026-09-20" });
  assert.equal(r.body.mode, "shadow");
  assert.equal(r.body.would_dispatch, false, "planner verdict: nothing warrants a dispatch");
  assert.ok(r.body.dispatch_id, "shadow must still reserve/track its unconditional dispatch");
  assert.equal(r.body.dispatch_reason, "shadow_unconditional");

  await post(coord, "/internal/complete", {
    dispatch_id: r.body.dispatch_id, target_seq: r.body.target_seq, status: "success",
    completed_at: "2026-09-20T00:02:00.000Z",
  });
  const status = await get(coord, "/internal/status");
  assert.equal(status.body.last_full_reconcile_at, "2026-09-20T00:02:00.000Z");
  assert.equal(status.body.last_successful_jst_date, "2026-09-20");
});

await check("shadow: a successful run advances last_completed_seq only to the dispatch-time target_seq snapshot", async () => {
  const coord = makeCoordinator(SHADOW_ENV);
  await post(coord, "/internal/event");
  await post(coord, "/internal/event");
  const r = await post(coord, "/internal/evaluate", { now_iso: "2026-09-20T00:00:00.000Z", today_jst: "2026-09-20" });
  assert.equal(r.body.target_seq, 2);
  await post(coord, "/internal/complete", { dispatch_id: r.body.dispatch_id, target_seq: r.body.target_seq, status: "success" });
  const status = await get(coord, "/internal/status");
  assert.equal(status.body.last_completed_seq, 2);
});

await check("shadow: a webhook arriving mid-run remains dirty after that run's completion (the whole race-condition point, in shadow too)", async () => {
  const coord = makeCoordinator(SHADOW_ENV);
  await post(coord, "/internal/event"); // event_seq=1
  const r = await post(coord, "/internal/evaluate", { now_iso: "2026-09-20T00:00:00.000Z", today_jst: "2026-09-20" });
  assert.equal(r.body.target_seq, 1);

  await post(coord, "/internal/event"); // event_seq=2, arrives while the shadow-tracked run is "in flight"

  // shadow still can't reserve a second dispatch while one is in-flight —
  // it will still unconditionally call GitHub next tick, just untracked;
  // the important guarantee is the FIRST run's own completion is correct.
  const midFlight = await post(coord, "/internal/evaluate", { now_iso: "2026-09-20T00:01:00.000Z", today_jst: "2026-09-20" });
  assert.equal(midFlight.body.dispatch_id, null);
  assert.equal(midFlight.body.reason, "in_flight");

  await post(coord, "/internal/complete", {
    dispatch_id: r.body.dispatch_id, target_seq: r.body.target_seq, status: "success",
    completed_at: "2026-09-20T00:02:00.000Z",
  });
  const status = await get(coord, "/internal/status");
  assert.equal(status.body.last_completed_seq, 1);
  assert.equal(status.body.event_seq, 2);
  assert.equal(status.body.dirty_count, 1, "event 2 must still be pending — this is what a boolean dirty flag would have lost");
});

await check("shadow: the planner becomes clean (would_dispatch=false) after the unconditional run it was observing succeeds", async () => {
  const coord = makeCoordinator(SHADOW_ENV);
  await post(coord, "/internal/event");
  const r = await post(coord, "/internal/evaluate", { now_iso: "2026-09-20T00:00:00.000Z", today_jst: "2026-09-20" });
  assert.equal(r.body.would_dispatch, true);
  assert.equal(r.body.reason, "booking_webhook");
  await post(coord, "/internal/complete", {
    dispatch_id: r.body.dispatch_id, target_seq: r.body.target_seq, status: "success",
    completed_at: "2026-09-20T00:02:00.000Z",
  });
  const clean = await post(coord, "/internal/evaluate", { now_iso: "2026-09-20T00:03:00.000Z", today_jst: "2026-09-20" });
  assert.equal(clean.body.would_dispatch, false, "planner must now independently agree nothing is pending");
});

// ---------------------------------------------------------------- fix round: manual force survives dispatch failure (blocker 3)
await check("manual force + a dispatch-API failure re-arms the force request for the next tick", async () => {
  const coord = makeCoordinator(ACTIVE_ENV);
  await post(coord, "/internal/complete", {
    dispatch_id: "00000000-0000-4000-8000-000000000000", target_seq: 0, status: "success", completed_at: "2026-09-20T00:00:00.000Z",
  });
  await post(coord, "/internal/force");

  const r1 = await post(coord, "/internal/evaluate", { now_iso: "2026-09-20T00:01:00.000Z", today_jst: "2026-09-20" });
  assert.equal(r1.body.reason, "manual_force");
  // GitHub workflow_dispatch API itself failed (500/network/timeout) — worker.js reports this back.
  await post(coord, "/internal/dispatch-failure", { dispatch_id: r1.body.dispatch_id });

  const midStatus = await get(coord, "/internal/status");
  assert.equal(midStatus.body.force_dispatch_requested, true, "the force request must not be silently dropped");
  assert.equal(midStatus.body.in_flight, null);

  const r2 = await post(coord, "/internal/evaluate", { now_iso: "2026-09-20T00:02:00.000Z", today_jst: "2026-09-20" });
  assert.equal(r2.body.reason, "manual_force", "the next tick must retry the same manual force");
  assert.notEqual(r2.body.dispatch_id, r1.body.dispatch_id);

  // This time the GitHub API call succeeds.
  await post(coord, "/internal/complete", { dispatch_id: r2.body.dispatch_id, target_seq: r2.body.target_seq, status: "success" });
  const finalStatus = await get(coord, "/internal/status");
  assert.equal(finalStatus.body.force_dispatch_requested, false, "a genuinely successful dispatch must still consume the force flag");
});

await check("a dispatch-API failure for a non-force reason does NOT resurrect force_dispatch_requested", async () => {
  const coord = makeCoordinator(ACTIVE_ENV);
  await post(coord, "/internal/event");
  const r = await post(coord, "/internal/evaluate", { today_jst: "2026-09-20" });
  assert.equal(r.body.reason, "booking_webhook");
  await post(coord, "/internal/dispatch-failure", { dispatch_id: r.body.dispatch_id });
  const status = await get(coord, "/internal/status");
  assert.equal(status.body.force_dispatch_requested, false);
});

// ---------------------------------------------------------------- fix round: target_seq invariant (blocker 5)
await check("a completion callback claiming a target_seq beyond the current event_seq is rejected without mutating state", async () => {
  const coord = makeCoordinator(ACTIVE_ENV);
  for (let i = 0; i < 10; i++) await post(coord, "/internal/event"); // event_seq=10
  const before = await get(coord, "/internal/status");
  assert.equal(before.body.event_seq, 10);

  const r = await post(coord, "/internal/complete", { dispatch_id: "88888888-8888-4888-8888-888888888888", target_seq: 999999, status: "success" });
  assert.equal(r.status, 400);
  assert.equal(r.body.error, "target_seq_exceeds_event_seq");

  const after = await get(coord, "/internal/status");
  assert.equal(after.body.last_completed_seq, 0, "must not have advanced");
  assert.equal(after.body.event_seq, 10, "must not have been mutated at all");
});

await check("a completion callback whose target_seq exactly equals the current event_seq is accepted (boundary)", async () => {
  const coord = makeCoordinator(ACTIVE_ENV);
  await post(coord, "/internal/event");
  await post(coord, "/internal/event");
  const r = await post(coord, "/internal/complete", { dispatch_id: "77777777-7777-4777-8777-777777777777", target_seq: 2, status: "success" });
  assert.equal(r.status, 200);
  assert.equal(r.body.last_completed_seq, 2);
});

console.log(`\n${passed} BiRefreshCoordinator checks passed`);
