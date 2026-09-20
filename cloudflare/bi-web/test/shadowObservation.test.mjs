// shadowObservation.test.mjs — KIRAKU BI PHASE 2 shadow semantic
// false-negative observation: BiRefreshCoordinator's /internal/observation
// counter logic (DO-level, direct instantiation like biRefreshCoordinator.
// test.mjs) plus the Worker-level POST /internal/bi-refresh/shadow-observation
// route (auth/privilege separation/status exposure, like biRefreshCallback.
// test.mjs).
//
// False-negative definition under test (PHASE 2 spec item 12): counted only
// when reason==="shadow_unconditional" (the planner itself would have
// skipped this cycle) AND at least one component reports "changed" AND
// neither component reports "error". "no_baseline"/"skipped" never count,
// and a real planner reason (e.g. booking_webhook) is not a "skip" cycle at
// all, so a change there is expected behavior, not a false negative.
//
// Every /internal/observation body below carries a dispatch_id — it is a
// required idempotency key (independent review blocker 4): a GitHub Actions
// rerun/retry of the same coordinator-tracked dispatch must not double-count
// the same 15-minute cycle.
import assert from "node:assert";
import { BiRefreshCoordinator } from "../src/biRefreshCoordinator.js";
import worker from "../src/worker.js";
import { makeCoordinatorNamespace } from "./testDoNamespace.js";

function makeDoStorage() {
  const map = new Map();
  return {
    async get(key) { return map.has(key) ? map.get(key) : undefined; },
    async put(key, value) { map.set(key, value); },
  };
}

function makeCoordinator(env = {}) {
  return { coord: new BiRefreshCoordinator({ storage: makeDoStorage() }, env) };
}

function makeCoordinatorWithStorage(env = {}) {
  const storage = makeDoStorage();
  return { coord: new BiRefreshCoordinator({ storage }, env), storage };
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

let passed = 0;
async function check(name, fn) { await fn(); passed++; console.log("ok -", name); }

// ================================================================== DO-level

await check("invalid body (missing dispatch_id/reason/statuses) is rejected with 400, no counters move", async () => {
  const { coord } = makeCoordinator();
  const r1 = await post(coord, "/internal/observation", {});
  assert.equal(r1.status, 400);
  const r2 = await post(coord, "/internal/observation", {
    dispatch_id: "11111111-1111-4111-8111-111111111111", reason: "shadow_unconditional", bi_status: "unchanged",
  });
  assert.equal(r2.status, 400, "missing staff_ops_status");
  const r3 = await post(coord, "/internal/observation", {
    dispatch_id: "11111111-1111-4111-8111-111111111111", reason: "shadow_unconditional", bi_status: "bogus", staff_ops_status: "unchanged",
  });
  assert.equal(r3.status, 400, "unrecognized status enum value");
  const r4 = await post(coord, "/internal/observation", {
    reason: "shadow_unconditional", bi_status: "unchanged", staff_ops_status: "unchanged",
  });
  assert.equal(r4.status, 400, "missing dispatch_id");
  const s = await get(coord, "/internal/status");
  assert.equal(s.body.shadow_observation.total, 0);
});

await check("a shadow_unconditional cycle with nothing changed only bumps total + planner_skip_total", async () => {
  const { coord } = makeCoordinator();
  const r = await post(coord, "/internal/observation", {
    dispatch_id: "11111111-1111-4111-8111-111111111111", reason: "shadow_unconditional", bi_status: "unchanged", staff_ops_status: "unchanged",
  });
  assert.equal(r.status, 200);
  assert.equal(r.body.false_negative, false);
  assert.equal(r.body.duplicate, false);
  const s = await get(coord, "/internal/status");
  assert.equal(s.body.shadow_observation.total, 1);
  assert.equal(s.body.shadow_observation.planner_skip_total, 1);
  assert.equal(s.body.shadow_observation.skip_any_changed_total, 0);
  assert.equal(s.body.shadow_observation.last_false_negative_at, null);
});

await check("a real planner reason (booking_webhook) with a change is NOT a false negative — that's expected, not silent", async () => {
  const { coord } = makeCoordinator();
  const r = await post(coord, "/internal/observation", {
    dispatch_id: "11111111-1111-4111-8111-111111111111", reason: "booking_webhook", bi_status: "changed", staff_ops_status: "unchanged",
  });
  assert.equal(r.body.false_negative, false);
  const s = await get(coord, "/internal/status");
  assert.equal(s.body.shadow_observation.total, 1);
  assert.equal(s.body.shadow_observation.planner_skip_total, 0, "booking_webhook is not a planner-skip cycle");
  assert.equal(s.body.shadow_observation.skip_bi_changed_total, 0);
});

await check("shadow_unconditional + BI changed => BI false negative counted", async () => {
  const { coord } = makeCoordinator();
  const r = await post(coord, "/internal/observation", {
    dispatch_id: "11111111-1111-4111-8111-111111111111", reason: "shadow_unconditional", bi_status: "changed", staff_ops_status: "unchanged",
  });
  assert.equal(r.body.false_negative, true);
  const s = await get(coord, "/internal/status");
  assert.equal(s.body.shadow_observation.skip_bi_changed_total, 1);
  assert.equal(s.body.shadow_observation.skip_staff_ops_changed_total, 0);
  assert.equal(s.body.shadow_observation.skip_any_changed_total, 1);
  assert.ok(s.body.shadow_observation.last_false_negative_at);
});

await check("shadow_unconditional + Staff Ops changed => Staff Ops false negative counted", async () => {
  const { coord } = makeCoordinator();
  const r = await post(coord, "/internal/observation", {
    dispatch_id: "11111111-1111-4111-8111-111111111111", reason: "shadow_unconditional", bi_status: "unchanged", staff_ops_status: "changed",
  });
  assert.equal(r.body.false_negative, true);
  const s = await get(coord, "/internal/status");
  assert.equal(s.body.shadow_observation.skip_bi_changed_total, 0);
  assert.equal(s.body.shadow_observation.skip_staff_ops_changed_total, 1);
  assert.equal(s.body.shadow_observation.skip_any_changed_total, 1);
});

await check("shadow_unconditional + both changed => one observation, correct category counters (not double-counted)", async () => {
  const { coord } = makeCoordinator();
  await post(coord, "/internal/observation", {
    dispatch_id: "11111111-1111-4111-8111-111111111111", reason: "shadow_unconditional", bi_status: "changed", staff_ops_status: "changed",
  });
  const s = await get(coord, "/internal/status");
  assert.equal(s.body.shadow_observation.total, 1);
  assert.equal(s.body.shadow_observation.skip_bi_changed_total, 1);
  assert.equal(s.body.shadow_observation.skip_staff_ops_changed_total, 1);
  assert.equal(s.body.shadow_observation.skip_any_changed_total, 1, "one changed cycle, not two");
});

await check("no_baseline never counts as a false negative even on a shadow_unconditional cycle", async () => {
  const { coord } = makeCoordinator();
  const r = await post(coord, "/internal/observation", {
    dispatch_id: "11111111-1111-4111-8111-111111111111", reason: "shadow_unconditional", bi_status: "no_baseline", staff_ops_status: "no_baseline",
  });
  assert.equal(r.body.false_negative, false);
  const s = await get(coord, "/internal/status");
  assert.equal(s.body.shadow_observation.total, 1);
  assert.equal(s.body.shadow_observation.planner_skip_total, 1);
  assert.equal(s.body.shadow_observation.skip_any_changed_total, 0);
  assert.equal(s.body.shadow_observation.errors_total, 0);
});

await check("skipped (gate closed) never counts as a false negative", async () => {
  const { coord } = makeCoordinator();
  const r = await post(coord, "/internal/observation", {
    dispatch_id: "11111111-1111-4111-8111-111111111111", reason: "shadow_unconditional", bi_status: "unchanged", staff_ops_status: "skipped",
  });
  assert.equal(r.body.false_negative, false);
  const s = await get(coord, "/internal/status");
  assert.equal(s.body.shadow_observation.skip_any_changed_total, 0);
});

await check("an observer error is counted separately and never treated as a false negative, even if the other component changed", async () => {
  const { coord } = makeCoordinator();
  const r = await post(coord, "/internal/observation", {
    dispatch_id: "11111111-1111-4111-8111-111111111111", reason: "shadow_unconditional", bi_status: "error", staff_ops_status: "changed",
  });
  assert.equal(r.body.false_negative, false, "an error means we don't actually know — must not claim a false negative");
  const s = await get(coord, "/internal/status");
  assert.equal(s.body.shadow_observation.errors_total, 1);
  assert.ok(s.body.shadow_observation.last_observation_error_at);
  assert.equal(s.body.shadow_observation.skip_bi_changed_total, 0);
  assert.equal(s.body.shadow_observation.skip_staff_ops_changed_total, 0);
  assert.equal(s.body.shadow_observation.skip_any_changed_total, 0);
});

await check("observation reporting never touches dispatch-tracking state (event_seq/in_flight/last_completed_seq)", async () => {
  const { coord } = makeCoordinator({ BI_REFRESH_DEFAULT_MODE: "active" });
  await post(coord, "/internal/event", {}); // event_seq -> 1
  const before = await get(coord, "/internal/status");
  await post(coord, "/internal/observation", {
    dispatch_id: "11111111-1111-4111-8111-111111111111", reason: "shadow_unconditional", bi_status: "changed", staff_ops_status: "changed",
  });
  const after = await get(coord, "/internal/status");
  assert.equal(after.body.event_seq, before.body.event_seq);
  assert.equal(after.body.last_completed_seq, before.body.last_completed_seq);
  assert.deepEqual(after.body.in_flight, before.body.in_flight);
});

// -------------------------------------------------------- idempotency (blocker 4)

await check("duplicate dispatch_id: total is not double-counted, and a false negative is not double-counted either", async () => {
  const { coord } = makeCoordinator();
  const first = await post(coord, "/internal/observation", {
    dispatch_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", reason: "shadow_unconditional", bi_status: "changed", staff_ops_status: "unchanged",
  });
  assert.equal(first.body.duplicate, false);
  assert.equal(first.body.false_negative, true);
  let s = await get(coord, "/internal/status");
  assert.equal(s.body.shadow_observation.total, 1);
  assert.equal(s.body.shadow_observation.skip_any_changed_total, 1);

  // Same dispatch_id reported again (GitHub Actions rerun/retry): no-op.
  const dup = await post(coord, "/internal/observation", {
    dispatch_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", reason: "shadow_unconditional", bi_status: "changed", staff_ops_status: "unchanged",
  });
  assert.equal(dup.status, 200);
  assert.equal(dup.body.duplicate, true);
  assert.equal(dup.body.false_negative, false, "a duplicate never re-reports a false negative");
  s = await get(coord, "/internal/status");
  assert.equal(s.body.shadow_observation.total, 1, "duplicate must not bump total");
  assert.equal(s.body.shadow_observation.skip_any_changed_total, 1, "duplicate must not double-count the false negative");

  // A genuinely new dispatch_id (dispatch-B) counts as a new observation.
  const second = await post(coord, "/internal/observation", {
    dispatch_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", reason: "shadow_unconditional", bi_status: "unchanged", staff_ops_status: "unchanged",
  });
  assert.equal(second.body.duplicate, false);
  s = await get(coord, "/internal/status");
  assert.equal(s.body.shadow_observation.total, 2);
});

await check("duplicate observation report never touches dispatch-tracking or reason counters", async () => {
  const { coord } = makeCoordinator();
  await post(coord, "/internal/observation", {
    dispatch_id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc", reason: "booking_webhook", bi_status: "unchanged", staff_ops_status: "unchanged",
  });
  const before = await get(coord, "/internal/status");
  await post(coord, "/internal/observation", {
    dispatch_id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc", reason: "booking_webhook", bi_status: "unchanged", staff_ops_status: "unchanged",
  });
  const after = await get(coord, "/internal/status");
  assert.deepEqual(after.body.shadow_observation.by_reason, before.body.shadow_observation.by_reason);
  assert.equal(after.body.shadow_observation.total, before.body.shadow_observation.total);
});

// -------------------------------------------------------- reason counters (completeness)

await check("reason counters break down observations by planner reason, including an unknown reason bucketed as other", async () => {
  const { coord } = makeCoordinator();
  const reasons = [
    ["shadow_unconditional", "10000000-0000-4000-8000-000000000001"],
    ["booking_webhook", "10000000-0000-4000-8000-000000000002"],
    ["full_reconciliation", "10000000-0000-4000-8000-000000000003"],
    ["jst_date_rollover", "10000000-0000-4000-8000-000000000004"],
    ["manual_force", "10000000-0000-4000-8000-000000000005"],
    ["some_future_reason_not_in_the_known_set", "10000000-0000-4000-8000-000000000006"],
  ];
  for (const [reason, dispatchId] of reasons) {
    const r = await post(coord, "/internal/observation", {
      dispatch_id: dispatchId, reason, bi_status: "unchanged", staff_ops_status: "unchanged",
    });
    assert.equal(r.status, 200, `reason=${reason} should be accepted, not rejected`);
    const expectedBucket = reason === "some_future_reason_not_in_the_known_set" ? "other" : reason;
    assert.equal(r.body.reason_bucket, expectedBucket, `reason=${reason} should normalize to ${expectedBucket}`);
  }
  const s = await get(coord, "/internal/status");
  assert.equal(s.body.shadow_observation.total, 6);
  assert.equal(s.body.shadow_observation.by_reason.shadow_unconditional, 1);
  assert.equal(s.body.shadow_observation.by_reason.booking_webhook, 1);
  assert.equal(s.body.shadow_observation.by_reason.full_reconciliation, 1);
  assert.equal(s.body.shadow_observation.by_reason.jst_date_rollover, 1);
  assert.equal(s.body.shadow_observation.by_reason.manual_force, 1);
  assert.equal(s.body.shadow_observation.by_reason.other, 1, "an unrecognized reason must not be silently dropped");
});

// -------------------------------------------------------- PII-safe metadata contract (blocker 7)
//
// refresh-bi-r2.yml's dispatch_id/reason are workflow_dispatch inputs — an
// operator can trigger the workflow manually with arbitrary text in either
// field. These prove that arbitrary/PII-shaped text in either field can
// never be stored in DO state or echoed back in a response, using fake
// guest-PII-shaped strings (never real data).

const FAKE_GUEST_NAME = "TEST_GUEST_TARO";
const FAKE_PHONE = "090-0000-1234";
const FAKE_EMAIL = "guest@example.invalid";
const VALID_UUID = "44444444-4444-4444-8444-444444444444";

await check("a PII-shaped dispatch_id (not a canonical UUID) is rejected, never stored, and never echoed back", async () => {
  const { coord } = makeCoordinator();
  const piiDispatchId = `${FAKE_GUEST_NAME} ${FAKE_PHONE}`;
  const r = await post(coord, "/internal/observation", {
    dispatch_id: piiDispatchId, reason: "shadow_unconditional", bi_status: "unchanged", staff_ops_status: "unchanged",
  });
  assert.equal(r.status, 400);
  const rBodyText = JSON.stringify(r.body);
  assert.ok(!rBodyText.includes(FAKE_GUEST_NAME), "response body must not echo the rejected dispatch_id");
  assert.ok(!rBodyText.includes(FAKE_PHONE), "response body must not echo the rejected dispatch_id");

  const s = await get(coord, "/internal/status");
  assert.equal(s.body.shadow_observation.total, 0, "a rejected observation must not move any counter");
  assert.ok(!JSON.stringify(s.body).includes(FAKE_GUEST_NAME), "/status must never reflect a rejected dispatch_id");

  // A subsequent legitimate observation must not be blocked by, or see any
  // trace of, the rejected attempt — proves nothing was partially stored.
  const r2 = await post(coord, "/internal/observation", {
    dispatch_id: VALID_UUID, reason: "shadow_unconditional", bi_status: "unchanged", staff_ops_status: "unchanged",
  });
  assert.equal(r2.status, 200);
  assert.equal(r2.body.duplicate, false, "the PII-shaped attempt must not have been recorded as a prior dispatch_id");
});

await check("only a canonical UUID is accepted as dispatch_id (case-insensitive) — every other shape is rejected", async () => {
  const { coord } = makeCoordinator();
  const malformed = [
    "not-a-uuid",
    "12345",
    "dispatch-A",
    FAKE_GUEST_NAME,
    "44444444-4444-1444-8444-444444444444", // wrong version nibble (must be 4)
    "44444444-4444-4444-0444-444444444444", // wrong variant nibble (must be 8/9/a/b)
    "44444444-4444-4444-8444-44444444444",  // too short
  ];
  for (const bad of malformed) {
    const r = await post(coord, "/internal/observation", {
      dispatch_id: bad, reason: "shadow_unconditional", bi_status: "unchanged", staff_ops_status: "unchanged",
    });
    assert.equal(r.status, 400, `expected dispatch_id=${JSON.stringify(bad)} to be rejected`);
  }
  const r = await post(coord, "/internal/observation", {
    dispatch_id: VALID_UUID.toUpperCase(), reason: "shadow_unconditional", bi_status: "unchanged", staff_ops_status: "unchanged",
  });
  assert.equal(r.status, 200, "a canonical UUID must be accepted regardless of letter case");
});

await check("an unknown reason carrying PII-shaped text is bucketed as other, never stored or echoed raw", async () => {
  const { coord } = makeCoordinator();
  const piiReason = `${FAKE_GUEST_NAME} ${FAKE_EMAIL}`;
  const r = await post(coord, "/internal/observation", {
    dispatch_id: VALID_UUID, reason: piiReason, bi_status: "unchanged", staff_ops_status: "unchanged",
  });
  assert.equal(r.status, 200);
  assert.equal(r.body.reason_bucket, "other");
  const rBodyText = JSON.stringify(r.body);
  assert.ok(!rBodyText.includes(FAKE_GUEST_NAME), "response must not echo the raw reason");
  assert.ok(!rBodyText.includes(FAKE_EMAIL), "response must not echo the raw reason");

  const s = await get(coord, "/internal/status");
  assert.equal(s.body.shadow_observation.by_reason.other, 1);
  const sBodyText = JSON.stringify(s.body);
  assert.ok(!sBodyText.includes(FAKE_GUEST_NAME), "/status must not echo the raw reason");
  assert.ok(!sBodyText.includes(FAKE_EMAIL), "/status must not echo the raw reason");
});

// -------------------------------------------------------- Phase 1 state upgrade (blocker 1)

// A Phase-1-shaped stored state has none of the Phase-2 fields at all (this
// is exactly what production's existing BiRefreshCoordinator DO storage
// looks like today). #load() must hydrate it against current defaults
// without ever losing an already-stored Phase-1 value.
function phase1ShapedState() {
  return {
    event_seq: 7,
    last_completed_seq: 5,
    in_flight_dispatch_id: "phase1-in-flight",
    in_flight_target_seq: 6,
    in_flight_started_at: "2026-09-19T00:00:00.000Z",
    in_flight_reason: "booking_webhook",
    last_webhook_at: "2026-09-19T00:00:00.000Z",
    last_dispatch_at: "2026-09-19T00:00:00.000Z",
    last_success_at: "2026-09-18T23:00:00.000Z",
    last_full_reconcile_at: "2026-09-18T20:00:00.000Z",
    last_successful_jst_date: "2026-09-19",
    consecutive_failures: 2,
    last_failure_at: "2026-09-18T22:00:00.000Z",
    mode: "shadow",
    force_dispatch_requested: true,
    // No shadow_observations_total / shadow_reason_*_total /
    // shadow_observed_dispatch_ids at all — this is the point of the test.
  };
}

await check("Phase 1 stored state upgrade: /status returns valid zeros (not null/undefined/NaN) before any Phase 2 observation", async () => {
  const { coord, storage } = makeCoordinatorWithStorage();
  await storage.put("state", phase1ShapedState());

  const s = await get(coord, "/internal/status");
  assert.equal(s.status, 200);
  const so = s.body.shadow_observation;
  for (const key of ["total", "planner_skip_total", "skip_bi_changed_total",
    "skip_staff_ops_changed_total", "skip_any_changed_total", "errors_total"]) {
    assert.equal(so[key], 0, `${key} must be a valid zero on an upgraded Phase 1 state, got ${so[key]}`);
    assert.ok(Number.isFinite(so[key]), `${key} must not be NaN`);
  }
  for (const key of Object.keys(so.by_reason)) {
    assert.equal(so.by_reason[key], 0, `by_reason.${key} must be a valid zero, got ${so.by_reason[key]}`);
  }
  assert.equal(so.last_observation_at, null);
  assert.equal(so.last_observation_error_at, null);
  assert.equal(so.last_false_negative_at, null);

  // Phase 1 fields are exactly preserved, untouched by the hydration.
  assert.equal(s.body.event_seq, 7);
  assert.equal(s.body.last_completed_seq, 5);
  assert.equal(s.body.consecutive_failures, 2);
  assert.equal(s.body.force_dispatch_requested, true);
  assert.equal(s.body.in_flight.dispatch_id, "phase1-in-flight");
  assert.equal(s.body.in_flight.target_seq, 6);
  assert.equal(s.body.in_flight.reason, "booking_webhook");
  assert.equal(s.body.last_success_at, "2026-09-18T23:00:00.000Z");
  assert.equal(s.body.last_full_reconcile_at, "2026-09-18T20:00:00.000Z");
  assert.equal(s.body.last_successful_jst_date, "2026-09-19");
});

await check("Phase 1 stored state upgrade: the first Phase 2 observation on an upgraded state becomes a valid integer, not NaN", async () => {
  const { coord, storage } = makeCoordinatorWithStorage();
  await storage.put("state", phase1ShapedState());

  const r = await post(coord, "/internal/observation", {
    dispatch_id: "dddddddd-dddd-4ddd-8ddd-dddddddddddd", reason: "shadow_unconditional",
    bi_status: "changed", staff_ops_status: "unchanged",
  });
  assert.equal(r.status, 200);
  assert.equal(r.body.false_negative, true);

  const s = await get(coord, "/internal/status");
  const so = s.body.shadow_observation;
  assert.equal(so.total, 1);
  assert.ok(Number.isFinite(so.total), "total must not be NaN after the first observation on an upgraded state");
  assert.equal(so.planner_skip_total, 1);
  assert.equal(so.skip_bi_changed_total, 1);
  assert.equal(so.skip_any_changed_total, 1);
  assert.equal(so.by_reason.shadow_unconditional, 1);

  // Phase 1 dispatch-tracking fields remain exactly as they were —
  // the observation endpoint never touches them (see the earlier
  // "observation reporting never touches dispatch-tracking state" check).
  assert.equal(s.body.event_seq, 7);
  assert.equal(s.body.last_completed_seq, 5);
  assert.equal(s.body.in_flight.dispatch_id, "phase1-in-flight");
});

// ================================================================ Worker-level

const CALLBACK_SECRET = "test-callback-secret-github-actions-only";
const OPS_SECRET = "test-ops-secret-operator-only";

function makeEnv(overrides = {}) {
  const env = { BI_REFRESH_CALLBACK_SECRET: CALLBACK_SECRET, BI_REFRESH_OPS_SECRET: OPS_SECRET, ...overrides };
  env.BI_REFRESH_COORDINATOR = makeCoordinatorNamespace(env);
  return env;
}

function observe(env, body, { headers = {} } = {}) {
  return worker.fetch(new Request("https://x/internal/bi-refresh/shadow-observation", {
    method: "POST",
    headers: { "content-type": "application/json", "Authorization": `Bearer ${CALLBACK_SECRET}`, ...headers },
    body: JSON.stringify(body),
  }), env);
}

async function status(env, headers = { "Authorization": `Bearer ${OPS_SECRET}` }) {
  const r = await worker.fetch(new Request("https://x/internal/bi-refresh/status", { headers }), env);
  return { status: r.status, body: await r.json() };
}

await check("shadow-observation endpoint requires the callback secret (missing/wrong bearer rejected)", async () => {
  const env = makeEnv();
  const r1 = await observe(env, {}, { headers: { Authorization: "" } });
  assert.equal(r1.status, 401);
  const r2 = await observe(env, {}, { headers: { Authorization: "Bearer wrong" } });
  assert.equal(r2.status, 401);
});

await check("the ops secret does NOT authenticate the shadow-observation endpoint (privilege separation)", async () => {
  const env = makeEnv();
  const r = await observe(env, {
    dispatch_id: "11111111-1111-4111-8111-111111111111", reason: "shadow_unconditional", bi_status: "unchanged", staff_ops_status: "unchanged",
  }, {
    headers: { Authorization: `Bearer ${OPS_SECRET}` },
  });
  assert.equal(r.status, 401);
});

await check("a valid observation report is accepted and reflected in /status", async () => {
  const env = makeEnv();
  const r = await observe(env, {
    dispatch_id: "11111111-1111-4111-8111-111111111111", reason: "shadow_unconditional", bi_status: "changed", staff_ops_status: "unchanged",
  });
  assert.equal(r.status, 200);
  const rBody = await r.json();
  assert.equal(rBody.false_negative, true);
  assert.equal(rBody.duplicate, false);
  const s = await status(env);
  assert.equal(s.body.shadow_observation.total, 1);
  assert.equal(s.body.shadow_observation.skip_bi_changed_total, 1);
});

await check("a duplicate observation report through the Worker route is a no-op", async () => {
  const env = makeEnv();
  await observe(env, {
    dispatch_id: "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee", reason: "shadow_unconditional", bi_status: "changed", staff_ops_status: "unchanged",
  });
  const r2 = await observe(env, {
    dispatch_id: "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee", reason: "shadow_unconditional", bi_status: "changed", staff_ops_status: "unchanged",
  });
  assert.equal(r2.status, 200);
  const r2Body = await r2.json();
  assert.equal(r2Body.duplicate, true);
  const s = await status(env);
  assert.equal(s.body.shadow_observation.total, 1);
});

await check("malformed status values are rejected with 400 through the Worker route too", async () => {
  const env = makeEnv();
  const r = await observe(env, {
    dispatch_id: "11111111-1111-4111-8111-111111111111", reason: "shadow_unconditional", bi_status: "totally_bogus", staff_ops_status: "unchanged",
  });
  assert.equal(r.status, 400);
});

await check("a missing dispatch_id is rejected with 400 through the Worker route too", async () => {
  const env = makeEnv();
  const r = await observe(env, {
    reason: "shadow_unconditional", bi_status: "unchanged", staff_ops_status: "unchanged",
  });
  assert.equal(r.status, 400);
});

await check("/internal/bi-refresh/status exposes shadow_observation (incl. by_reason) but never a PII-shaped field", async () => {
  const env = makeEnv();
  await observe(env, {
    dispatch_id: "11111111-1111-4111-8111-111111111111", reason: "shadow_unconditional", bi_status: "changed", staff_ops_status: "changed",
  });
  const s = await status(env);
  assert.equal(s.status, 200);
  assert.ok(s.body.shadow_observation);
  for (const key of ["total", "planner_skip_total", "skip_bi_changed_total", "skip_staff_ops_changed_total",
    "skip_any_changed_total", "errors_total", "by_reason", "last_observation_at", "last_false_negative_at"]) {
    assert.ok(key in s.body.shadow_observation, `missing shadow_observation.${key}`);
  }
  for (const key of ["shadow_unconditional", "booking_webhook", "full_reconciliation", "jst_date_rollover", "manual_force", "other"]) {
    assert.ok(key in s.body.shadow_observation.by_reason, `missing shadow_observation.by_reason.${key}`);
  }
  for (const forbidden of ["guest", "name", "phone", "email", "address", "comment", "secret", "token"]) {
    assert.ok(!JSON.stringify(s.body).toLowerCase().includes(forbidden), `status leaked a ${forbidden}-shaped field`);
  }
});

await check("Worker route: PII-shaped dispatch_id/reason never appear in the response or in Worker console logs", async () => {
  const env = makeEnv();
  const originalLog = console.log;
  const logged = [];
  console.log = (...args) => { logged.push(args.join(" ")); };
  let rejectedStatus, acceptedStatus, acceptedBody;
  try {
    const rejected = await observe(env, {
      dispatch_id: `${FAKE_GUEST_NAME} ${FAKE_PHONE}`, reason: "shadow_unconditional",
      bi_status: "unchanged", staff_ops_status: "unchanged",
    });
    rejectedStatus = rejected.status;

    const accepted = await observe(env, {
      dispatch_id: VALID_UUID, reason: `${FAKE_GUEST_NAME} ${FAKE_EMAIL}`,
      bi_status: "unchanged", staff_ops_status: "unchanged",
    });
    acceptedStatus = accepted.status;
    acceptedBody = await accepted.json();
  } finally {
    console.log = originalLog;
  }

  assert.equal(rejectedStatus, 400);
  assert.equal(acceptedStatus, 200);
  assert.equal(acceptedBody.reason_bucket, "other");

  const allLogged = logged.join("\n");
  for (const fake of [FAKE_GUEST_NAME, FAKE_PHONE, FAKE_EMAIL]) {
    assert.ok(!allLogged.includes(fake), `Worker console log leaked ${JSON.stringify(fake)}`);
  }
});

console.log(`\n${passed} shadow observation checks passed`);
