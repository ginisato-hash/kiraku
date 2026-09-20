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

let passed = 0;
async function check(name, fn) { await fn(); passed++; console.log("ok -", name); }

// ================================================================== DO-level

await check("invalid body (missing reason/statuses) is rejected with 400, no counters move", async () => {
  const coord = makeCoordinator();
  const r1 = await post(coord, "/internal/observation", {});
  assert.equal(r1.status, 400);
  const r2 = await post(coord, "/internal/observation", { reason: "shadow_unconditional", bi_status: "unchanged" });
  assert.equal(r2.status, 400, "missing staff_ops_status");
  const r3 = await post(coord, "/internal/observation", { reason: "shadow_unconditional", bi_status: "bogus", staff_ops_status: "unchanged" });
  assert.equal(r3.status, 400, "unrecognized status enum value");
  const s = await get(coord, "/internal/status");
  assert.equal(s.body.shadow_observation.total, 0);
});

await check("a shadow_unconditional cycle with nothing changed only bumps total + planner_skip_total", async () => {
  const coord = makeCoordinator();
  const r = await post(coord, "/internal/observation", {
    reason: "shadow_unconditional", bi_status: "unchanged", staff_ops_status: "unchanged",
  });
  assert.equal(r.status, 200);
  assert.equal(r.body.false_negative, false);
  const s = await get(coord, "/internal/status");
  assert.equal(s.body.shadow_observation.total, 1);
  assert.equal(s.body.shadow_observation.planner_skip_total, 1);
  assert.equal(s.body.shadow_observation.skip_any_changed_total, 0);
  assert.equal(s.body.shadow_observation.last_false_negative_at, null);
});

await check("a real planner reason (booking_webhook) with a change is NOT a false negative — that's expected, not silent", async () => {
  const coord = makeCoordinator();
  const r = await post(coord, "/internal/observation", {
    reason: "booking_webhook", bi_status: "changed", staff_ops_status: "unchanged",
  });
  assert.equal(r.body.false_negative, false);
  const s = await get(coord, "/internal/status");
  assert.equal(s.body.shadow_observation.total, 1);
  assert.equal(s.body.shadow_observation.planner_skip_total, 0, "booking_webhook is not a planner-skip cycle");
  assert.equal(s.body.shadow_observation.skip_bi_changed_total, 0);
});

await check("shadow_unconditional + BI changed => BI false negative counted", async () => {
  const coord = makeCoordinator();
  const r = await post(coord, "/internal/observation", {
    reason: "shadow_unconditional", bi_status: "changed", staff_ops_status: "unchanged",
  });
  assert.equal(r.body.false_negative, true);
  const s = await get(coord, "/internal/status");
  assert.equal(s.body.shadow_observation.skip_bi_changed_total, 1);
  assert.equal(s.body.shadow_observation.skip_staff_ops_changed_total, 0);
  assert.equal(s.body.shadow_observation.skip_any_changed_total, 1);
  assert.ok(s.body.shadow_observation.last_false_negative_at);
});

await check("shadow_unconditional + Staff Ops changed => Staff Ops false negative counted", async () => {
  const coord = makeCoordinator();
  const r = await post(coord, "/internal/observation", {
    reason: "shadow_unconditional", bi_status: "unchanged", staff_ops_status: "changed",
  });
  assert.equal(r.body.false_negative, true);
  const s = await get(coord, "/internal/status");
  assert.equal(s.body.shadow_observation.skip_bi_changed_total, 0);
  assert.equal(s.body.shadow_observation.skip_staff_ops_changed_total, 1);
  assert.equal(s.body.shadow_observation.skip_any_changed_total, 1);
});

await check("shadow_unconditional + both changed => one observation, correct category counters (not double-counted)", async () => {
  const coord = makeCoordinator();
  await post(coord, "/internal/observation", {
    reason: "shadow_unconditional", bi_status: "changed", staff_ops_status: "changed",
  });
  const s = await get(coord, "/internal/status");
  assert.equal(s.body.shadow_observation.total, 1);
  assert.equal(s.body.shadow_observation.skip_bi_changed_total, 1);
  assert.equal(s.body.shadow_observation.skip_staff_ops_changed_total, 1);
  assert.equal(s.body.shadow_observation.skip_any_changed_total, 1, "one changed cycle, not two");
});

await check("no_baseline never counts as a false negative even on a shadow_unconditional cycle", async () => {
  const coord = makeCoordinator();
  const r = await post(coord, "/internal/observation", {
    reason: "shadow_unconditional", bi_status: "no_baseline", staff_ops_status: "no_baseline",
  });
  assert.equal(r.body.false_negative, false);
  const s = await get(coord, "/internal/status");
  assert.equal(s.body.shadow_observation.total, 1);
  assert.equal(s.body.shadow_observation.planner_skip_total, 1);
  assert.equal(s.body.shadow_observation.skip_any_changed_total, 0);
  assert.equal(s.body.shadow_observation.errors_total, 0);
});

await check("skipped (gate closed) never counts as a false negative", async () => {
  const coord = makeCoordinator();
  const r = await post(coord, "/internal/observation", {
    reason: "shadow_unconditional", bi_status: "unchanged", staff_ops_status: "skipped",
  });
  assert.equal(r.body.false_negative, false);
  const s = await get(coord, "/internal/status");
  assert.equal(s.body.shadow_observation.skip_any_changed_total, 0);
});

await check("an observer error is counted separately and never treated as a false negative, even if the other component changed", async () => {
  const coord = makeCoordinator();
  const r = await post(coord, "/internal/observation", {
    reason: "shadow_unconditional", bi_status: "error", staff_ops_status: "changed",
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
  const coord = makeCoordinator({ BI_REFRESH_DEFAULT_MODE: "active" });
  await post(coord, "/internal/event", {}); // event_seq -> 1
  const before = await get(coord, "/internal/status");
  await post(coord, "/internal/observation", {
    reason: "shadow_unconditional", bi_status: "changed", staff_ops_status: "changed",
  });
  const after = await get(coord, "/internal/status");
  assert.equal(after.body.event_seq, before.body.event_seq);
  assert.equal(after.body.last_completed_seq, before.body.last_completed_seq);
  assert.deepEqual(after.body.in_flight, before.body.in_flight);
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
  const r = await observe(env, { reason: "shadow_unconditional", bi_status: "unchanged", staff_ops_status: "unchanged" }, {
    headers: { Authorization: `Bearer ${OPS_SECRET}` },
  });
  assert.equal(r.status, 401);
});

await check("a valid observation report is accepted and reflected in /status", async () => {
  const env = makeEnv();
  const r = await observe(env, { reason: "shadow_unconditional", bi_status: "changed", staff_ops_status: "unchanged" });
  assert.equal(r.status, 200);
  assert.equal((await r.json()).false_negative, true);
  const s = await status(env);
  assert.equal(s.body.shadow_observation.total, 1);
  assert.equal(s.body.shadow_observation.skip_bi_changed_total, 1);
});

await check("malformed status values are rejected with 400 through the Worker route too", async () => {
  const env = makeEnv();
  const r = await observe(env, { reason: "shadow_unconditional", bi_status: "totally_bogus", staff_ops_status: "unchanged" });
  assert.equal(r.status, 400);
});

await check("/internal/bi-refresh/status exposes shadow_observation but never a PII-shaped field", async () => {
  const env = makeEnv();
  await observe(env, { reason: "shadow_unconditional", bi_status: "changed", staff_ops_status: "changed" });
  const s = await status(env);
  assert.equal(s.status, 200);
  assert.ok(s.body.shadow_observation);
  for (const key of ["total", "planner_skip_total", "skip_bi_changed_total", "skip_staff_ops_changed_total", "skip_any_changed_total", "errors_total", "last_observation_at", "last_false_negative_at"]) {
    assert.ok(key in s.body.shadow_observation, `missing shadow_observation.${key}`);
  }
  for (const forbidden of ["guest", "name", "phone", "email", "address", "comment", "secret", "token"]) {
    assert.ok(!JSON.stringify(s.body).toLowerCase().includes(forbidden), `status leaked a ${forbidden}-shaped field`);
  }
});

console.log(`\n${passed} shadow observation checks passed`);
