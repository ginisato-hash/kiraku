// biRefreshCallback.test.mjs — POST /internal/bi-refresh/complete
// (GitHub Actions completion callback) and GET/POST /internal/bi-refresh/*
// operator endpoints (status/mode/force). Confirms auth, success/failure
// bookkeeping, duplicate/stale/wrong-dispatch-id handling end-to-end
// through the Worker (not just the DO unit tests), and that status/mode
// endpoints require auth too.
//
// Privilege separation (fix round blocker 7): /complete is authenticated by
// BI_REFRESH_CALLBACK_SECRET (the one GitHub Actions holds); /status,
// /mode and /force are authenticated by a DIFFERENT secret,
// BI_REFRESH_OPS_SECRET, that GitHub Actions never sees. CALLBACK_SECRET
// must never work against the ops endpoints and vice versa — asserted
// explicitly below, not just assumed from the two envs being different.
import assert from "node:assert";
import worker from "../src/worker.js";
import { makeCoordinatorNamespace } from "./testDoNamespace.js";

const CALLBACK_SECRET = "test-callback-secret-github-actions-only";
const OPS_SECRET = "test-ops-secret-operator-only";

function makeEnv(overrides = {}) {
  const env = { BI_REFRESH_CALLBACK_SECRET: CALLBACK_SECRET, BI_REFRESH_OPS_SECRET: OPS_SECRET, ...overrides };
  // Durable Objects exported from the same Worker script share the same
  // env/vars — the coordinator namespace must see the same overrides
  // (e.g. BI_REFRESH_DEFAULT_MODE) that the test passes to the Worker env.
  env.BI_REFRESH_COORDINATOR = makeCoordinatorNamespace(env);
  return env;
}

function complete(env, body, { headers = {} } = {}) {
  return worker.fetch(new Request("https://x/internal/bi-refresh/complete", {
    method: "POST",
    headers: { "content-type": "application/json", "Authorization": `Bearer ${CALLBACK_SECRET}`, ...headers },
    body: JSON.stringify(body),
  }), env);
}

async function status(env, headers = { "Authorization": `Bearer ${OPS_SECRET}` }) {
  const r = await worker.fetch(new Request("https://x/internal/bi-refresh/status", { headers }), env);
  return { status: r.status, body: await r.json() };
}

async function reserveOne(env) {
  const stub = env.BI_REFRESH_COORDINATOR.get("singleton");
  await stub.fetch(new Request("https://do/internal/event", { method: "POST" }));
  const evalRes = await stub.fetch(new Request("https://do/internal/evaluate", {
    method: "POST", body: JSON.stringify({ now_iso: "2026-09-20T00:00:00.000Z", today_jst: "2026-09-20" }),
  }));
  return evalRes.json();
}

let passed = 0;
async function check(name, fn) { await fn(); passed++; console.log("ok -", name); }

// ---------------------------------------------------------------- auth
await check("callback auth failure: missing/wrong bearer token is rejected", async () => {
  const env = makeEnv();
  const r1 = await complete(env, {}, { headers: { Authorization: "" } });
  assert.equal(r1.status, 401);
  const r2 = await complete(env, {}, { headers: { Authorization: "Bearer wrong" } });
  assert.equal(r2.status, 401);
});

await check("an unconfigured callback secret fails closed (500)", async () => {
  const env = makeEnv({ BI_REFRESH_CALLBACK_SECRET: undefined });
  const r = await complete(env, { dispatch_id: "x", target_seq: 1, status: "success" });
  assert.equal(r.status, 500);
});

// ---------------------------------------------------------------- privilege separation (fix round blocker 7)
await check("the ops secret does NOT authenticate the completion callback endpoint", async () => {
  const env = makeEnv();
  const r = await complete(env, { dispatch_id: "x", target_seq: 1, status: "success" }, {
    headers: { Authorization: `Bearer ${OPS_SECRET}` },
  });
  assert.equal(r.status, 401);
});

await check("the callback secret does NOT authenticate any ops endpoint (status/mode/force)", async () => {
  const env = makeEnv();
  const wrongAuthHeader = { Authorization: `Bearer ${CALLBACK_SECRET}` };
  const s = await worker.fetch(new Request("https://x/internal/bi-refresh/status", { headers: wrongAuthHeader }), env);
  assert.equal(s.status, 401);
  const m = await worker.fetch(new Request("https://x/internal/bi-refresh/mode", {
    method: "POST", headers: wrongAuthHeader, body: JSON.stringify({ mode: "active" }),
  }), env);
  assert.equal(m.status, 401);
  const f = await worker.fetch(new Request("https://x/internal/bi-refresh/force", { method: "POST", headers: wrongAuthHeader }), env);
  assert.equal(f.status, 401);
});

await check("GitHub Actions holding only the callback secret cannot flip mode or force a dispatch", async () => {
  const env = makeEnv({ BI_REFRESH_OPS_SECRET: undefined }); // simulates "GitHub Actions never has this secret"
  const s = await status(env, { Authorization: `Bearer ${CALLBACK_SECRET}` });
  assert.equal(s.status, 500, "ops endpoints must fail closed, not fall back to the callback secret");
});

// ---------------------------------------------------------------- success
await check("success callback advances last_completed_seq and is reflected in /status", async () => {
  const env = makeEnv({ BI_REFRESH_DEFAULT_MODE: "active" });
  const reservation = await reserveOne(env);
  const r = await complete(env, {
    dispatch_id: reservation.dispatch_id, target_seq: reservation.target_seq,
    status: "success", reason: "booking_webhook", completed_at: "2026-09-20T00:02:00.000Z",
  });
  assert.equal(r.status, 200);
  assert.equal((await r.json()).last_completed_seq, 1);
  const s = await status(env);
  assert.equal(s.body.last_completed_seq, 1);
  assert.equal(s.body.in_flight, null);
});

// ---------------------------------------------------------------- failure
await check("failure callback does not advance last_completed_seq and bumps consecutive_failures", async () => {
  const env = makeEnv({ BI_REFRESH_DEFAULT_MODE: "active" });
  const reservation = await reserveOne(env);
  await complete(env, { dispatch_id: reservation.dispatch_id, target_seq: reservation.target_seq, status: "failure" });
  const s = await status(env);
  assert.equal(s.body.last_completed_seq, 0);
  assert.equal(s.body.consecutive_failures, 1);
});

// ---------------------------------------------------------------- duplicate
await check("a duplicate completion callback is idempotent", async () => {
  const env = makeEnv({ BI_REFRESH_DEFAULT_MODE: "active" });
  const reservation = await reserveOne(env);
  const payload = { dispatch_id: reservation.dispatch_id, target_seq: reservation.target_seq, status: "success" };
  await complete(env, payload);
  const r2 = await complete(env, payload);
  assert.equal(r2.status, 200);
  const s = await status(env);
  assert.equal(s.body.last_completed_seq, 1);
});

// ---------------------------------------------------------------- stale dispatch id
await check("a stale dispatch_id (already superseded) is a safe no-op, never regresses last_completed_seq", async () => {
  const env = makeEnv({ BI_REFRESH_DEFAULT_MODE: "active" });
  const first = await reserveOne(env);
  await complete(env, { dispatch_id: first.dispatch_id, target_seq: first.target_seq, status: "success" });
  const second = await reserveOne(env);
  await complete(env, { dispatch_id: second.dispatch_id, target_seq: second.target_seq, status: "success" });

  const stale = await complete(env, { dispatch_id: first.dispatch_id, target_seq: first.target_seq, status: "success" });
  assert.equal(stale.status, 200);
  const s = await status(env);
  assert.equal(s.body.last_completed_seq, 2, "must not regress after a late/duplicate callback for an old dispatch");
});

// ---------------------------------------------------------------- future target_seq invariant (blocker 5, end-to-end through the Worker route)
await check("a callback claiming a target_seq beyond the current event_seq is rejected (400), state untouched", async () => {
  const env = makeEnv({ BI_REFRESH_DEFAULT_MODE: "active" });
  await reserveOne(env); // event_seq=1
  const r = await complete(env, { dispatch_id: "88888888-8888-4888-8888-888888888888", target_seq: 999999, status: "success" });
  assert.equal(r.status, 400);
  const s = await status(env);
  assert.equal(s.body.last_completed_seq, 0);
});

// ---------------------------------------------------------------- seq mismatch / malformed
await check("malformed payloads are rejected with 400", async () => {
  const env = makeEnv();
  const r1 = await complete(env, { target_seq: 1, status: "success" }); // missing dispatch_id
  assert.equal(r1.status, 400);
  const r2 = await complete(env, { dispatch_id: "66666666-6666-4666-8666-666666666666", target_seq: "one", status: "success" }); // non-integer seq
  assert.equal(r2.status, 400);
  const r3 = await complete(env, { dispatch_id: "66666666-6666-4666-8666-666666666666", target_seq: 1, status: "unknown" }); // bad status enum
  assert.equal(r3.status, 400);
});

// ---------------------------------------------------------------- always() semantics: a manual run with no dispatch context is not our concern here
// (covered by the GitHub Actions workflow test — the callback step itself
// skips entirely when dispatch_id is empty; this endpoint is simply never
// called in that case.)

// ---------------------------------------------------------------- PII-safe dispatch_id/reason contract (fix round blocker 8)
//
// refresh-bi-r2.yml's dispatch_id/reason are workflow_dispatch inputs — an
// operator can trigger the workflow manually with arbitrary text in
// either field. These use fake guest-PII-shaped strings (never real data)
// to prove neither field can reach a Cloudflare log line, the response, or
// DO state, mirroring the equivalent tests added for /internal/observation
// in shadowObservation.test.mjs.

const FAKE_GUEST_NAME = "TEST_GUEST_TARO";
const FAKE_PHONE = "090-0000-1234";
const FAKE_EMAIL = "guest@example.invalid";
const VALID_UUID = "44444444-4444-4444-8444-444444444444";

await check("Test A: a PII-shaped dispatch_id is rejected, never advances state, and never appears in the response, console log, or /status", async () => {
  const env = makeEnv({ BI_REFRESH_DEFAULT_MODE: "active" });
  await reserveOne(env); // event_seq=1, in_flight reserved

  const before = await status(env);

  const originalLog = console.log;
  const logged = [];
  console.log = (...args) => { logged.push(args.join(" ")); };
  let r;
  try {
    r = await complete(env, { dispatch_id: `${FAKE_GUEST_NAME} ${FAKE_PHONE}`, target_seq: 0, status: "success" });
  } finally {
    console.log = originalLog;
  }

  assert.equal(r.status, 400);
  const rBodyText = JSON.stringify(await r.json());
  assert.ok(!rBodyText.includes(FAKE_GUEST_NAME) && !rBodyText.includes(FAKE_PHONE), "response must not echo the rejected dispatch_id");
  assert.ok(!logged.join("\n").includes(FAKE_GUEST_NAME) && !logged.join("\n").includes(FAKE_PHONE),
    "console log must not contain the rejected dispatch_id");

  const after = await status(env);
  assert.equal(after.body.last_completed_seq, before.body.last_completed_seq, "last_completed_seq must be unchanged");
  assert.deepEqual(after.body.in_flight, before.body.in_flight, "in_flight must be unchanged");
  assert.ok(!JSON.stringify(after.body).includes(FAKE_GUEST_NAME), "/status must never reflect a rejected dispatch_id");
});

await check("Test B: a valid UUID with PII-shaped reason text never leaks the raw reason into the console log, response, or /status", async () => {
  const env = makeEnv({ BI_REFRESH_DEFAULT_MODE: "active" });
  const reservation = await reserveOne(env);

  const originalLog = console.log;
  const logged = [];
  console.log = (...args) => { logged.push(args.join(" ")); };
  let r;
  try {
    r = await complete(env, {
      dispatch_id: reservation.dispatch_id, target_seq: reservation.target_seq, status: "success",
      reason: `${FAKE_GUEST_NAME} ${FAKE_EMAIL}`,
    });
  } finally {
    console.log = originalLog;
  }

  assert.equal(r.status, 200, "the callback's own success/failure semantics must be unaffected by an unrecognized reason");
  const rBodyText = JSON.stringify(await r.json());
  assert.ok(!rBodyText.includes(FAKE_GUEST_NAME) && !rBodyText.includes(FAKE_EMAIL), "response must not echo the raw reason");
  const allLogged = logged.join("\n");
  assert.ok(!allLogged.includes(FAKE_GUEST_NAME) && !allLogged.includes(FAKE_EMAIL), "console log must not contain the raw reason");
  assert.ok(allLogged.includes("reason_bucket=other"), "an unrecognized reason must still be logged, normalized as other");

  const s = await status(env);
  assert.ok(!JSON.stringify(s.body).includes(FAKE_GUEST_NAME) && !JSON.stringify(s.body).includes(FAKE_EMAIL),
    "/status must never reflect the raw reason");
});

await check("only a canonical UUID is accepted as dispatch_id by /internal/complete too (defense in depth at the DO level)", async () => {
  const env = makeEnv({ BI_REFRESH_DEFAULT_MODE: "active" });
  const stub = env.BI_REFRESH_COORDINATOR.get("singleton");
  await stub.fetch(new Request("https://do/internal/event", { method: "POST" })); // event_seq=1

  const malformed = ["not-a-uuid", "dispatch-A", FAKE_GUEST_NAME];
  for (const bad of malformed) {
    const r = await stub.fetch(new Request("https://do/internal/complete", {
      method: "POST", body: JSON.stringify({ dispatch_id: bad, target_seq: 0, status: "success" }),
    }));
    assert.equal(r.status, 400, `expected dispatch_id=${JSON.stringify(bad)} to be rejected directly by the DO too`);
  }

  const ok = await stub.fetch(new Request("https://do/internal/complete", {
    method: "POST", body: JSON.stringify({ dispatch_id: VALID_UUID, target_seq: 0, status: "success" }),
  }));
  assert.equal(ok.status, 200, "a canonical UUID must still be accepted directly against the DO");
});

// ---------------------------------------------------------------- status endpoint
await check("/internal/bi-refresh/status requires auth and never exposes PII-shaped fields", async () => {
  const env = makeEnv();
  const unauth = await worker.fetch(new Request("https://x/internal/bi-refresh/status"), env);
  assert.equal(unauth.status, 401);
  const s = await status(env);
  assert.equal(s.status, 200);
  for (const forbidden of ["guest", "name", "phone", "email", "address", "comment", "secret", "token"]) {
    assert.ok(!JSON.stringify(s.body).toLowerCase().includes(forbidden), `status leaked a ${forbidden}-shaped field`);
  }
});

// ---------------------------------------------------------------- mode / force
await check("mode switching requires auth, validates the value, and takes effect without redeploy", async () => {
  const env = makeEnv();
  const unauth = await worker.fetch(new Request("https://x/internal/bi-refresh/mode", { method: "POST", body: "{}" }), env);
  assert.equal(unauth.status, 401);

  const bad = await worker.fetch(new Request("https://x/internal/bi-refresh/mode", {
    method: "POST", headers: { Authorization: `Bearer ${OPS_SECRET}` }, body: JSON.stringify({ mode: "turbo" }),
  }), env);
  assert.equal(bad.status, 400);

  const ok = await worker.fetch(new Request("https://x/internal/bi-refresh/mode", {
    method: "POST", headers: { Authorization: `Bearer ${OPS_SECRET}` }, body: JSON.stringify({ mode: "active" }),
  }), env);
  assert.equal(ok.status, 200);
  const s = await status(env);
  assert.equal(s.body.mode, "active");
});

await check("manual force via the operator endpoint causes the next evaluate to dispatch", async () => {
  const env = makeEnv({ BI_REFRESH_DEFAULT_MODE: "active" });
  const stub = env.BI_REFRESH_COORDINATOR.get("singleton");
  await stub.fetch(new Request("https://do/internal/complete", {
    method: "POST", body: JSON.stringify({ dispatch_id: "00000000-0000-4000-8000-000000000000", target_seq: 0, status: "success", completed_at: "2026-09-20T00:00:00.000Z" }),
  }));
  const forceRes = await worker.fetch(new Request("https://x/internal/bi-refresh/force", {
    method: "POST", headers: { Authorization: `Bearer ${OPS_SECRET}` },
  }), env);
  assert.equal(forceRes.status, 200);
  const evalRes = await stub.fetch(new Request("https://do/internal/evaluate", {
    method: "POST", body: JSON.stringify({ now_iso: "2026-09-20T00:01:00.000Z", today_jst: "2026-09-20" }),
  }));
  assert.equal((await evalRes.json()).reason, "manual_force");
});

console.log(`\n${passed} BI refresh callback/status/mode/force checks passed`);
