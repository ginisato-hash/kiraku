// beds24Webhook.test.mjs — POST /internal/beds24/booking-webhook.
// Confirms: POST-only, auth required (constant-time compare), content-type
// validated, oversized body rejected, malformed JSON rejected, wrong
// property rejected, a valid webhook increments Coordinator event_seq
// exactly once, and — critically — no guest PII ever reaches the response
// body or console.log.
import assert from "node:assert";
import worker from "../src/worker.js";
import { makeCoordinatorNamespace } from "./testDoNamespace.js";

const SECRET = "test-beds24-webhook-shared-secret";
const KIRAKU_PROPERTY_ID = "330695";

function makeEnv(overrides = {}) {
  return {
    BEDS24_WEBHOOK_SECRET: SECRET,
    BI_REFRESH_COORDINATOR: makeCoordinatorNamespace(),
    ...overrides,
  };
}

function post(env, body, { headers = {}, rawBody } = {}) {
  const payload = rawBody !== undefined ? rawBody : JSON.stringify(body);
  return worker.fetch(new Request("https://x/internal/beds24/booking-webhook", {
    method: "POST",
    headers: { "content-type": "application/json", "X-Kiraku-Webhook-Token": SECRET, ...headers },
    body: payload,
  }), env);
}

function validBookingPayload(overrides = {}) {
  return {
    timeStamp: "2026-09-20T00:00:00Z",
    booking: {
      id: 123456, propertyId: Number(KIRAKU_PROPERTY_ID), status: "confirmed",
      firstName: "Taro", lastName: "Yamada", email: "taro@example.com",
      phone: "090-0000-0000", comments: "Please prepare a crib for our baby.",
      ...overrides,
    },
  };
}

async function coordinatorEventSeq(env) {
  const stub = env.BI_REFRESH_COORDINATOR.get("singleton");
  const res = await stub.fetch(new Request("https://do/internal/status"));
  return (await res.json()).event_seq;
}

let passed = 0;
async function check(name, fn) { await fn(); passed++; console.log("ok -", name); }

await check("GET is rejected (POST only)", async () => {
  const env = makeEnv();
  const r = await worker.fetch(new Request("https://x/internal/beds24/booking-webhook", {
    method: "GET", headers: { "X-Kiraku-Webhook-Token": SECRET },
  }), env);
  assert.equal(r.status, 405);
});

await check("missing/invalid X-Kiraku-Webhook-Token is rejected before touching the coordinator", async () => {
  const env = makeEnv();
  const r1 = await post(env, validBookingPayload(), { headers: { "X-Kiraku-Webhook-Token": "" } });
  assert.equal(r1.status, 401);
  const r2 = await post(env, validBookingPayload(), { headers: { "X-Kiraku-Webhook-Token": "wrong-value" } });
  assert.equal(r2.status, 401);
  assert.equal(await coordinatorEventSeq(env), 0);
});

await check("an unconfigured secret fails closed (500), never silently accepts", async () => {
  const env = makeEnv({ BEDS24_WEBHOOK_SECRET: undefined });
  const r = await post(env, validBookingPayload());
  assert.equal(r.status, 500);
});

await check("invalid content type is rejected", async () => {
  const env = makeEnv();
  const r = await post(env, validBookingPayload(), { headers: { "content-type": "text/plain" } });
  assert.equal(r.status, 415);
});

await check("an oversized body is rejected via Content-Length before parsing", async () => {
  const env = makeEnv();
  const bigJson = JSON.stringify({ padding: "x".repeat(300000) });
  const r = await post(env, null, { rawBody: bigJson, headers: { "content-length": String(bigJson.length) } });
  assert.equal(r.status, 413);
  assert.equal(await coordinatorEventSeq(env), 0);
});

await check("malformed JSON is rejected", async () => {
  const env = makeEnv();
  const r = await post(env, null, { rawBody: "{not json", headers: { "content-length": "9" } });
  assert.equal(r.status, 400);
});

await check("a booking for a different property is rejected, event_seq untouched", async () => {
  const env = makeEnv();
  const r = await post(env, validBookingPayload({ propertyId: 999999 }));
  assert.equal(r.status, 403);
  assert.equal(await coordinatorEventSeq(env), 0);
});

await check("a valid webhook for the kiraku property is accepted and increments event_seq exactly once", async () => {
  const env = makeEnv();
  const r = await post(env, validBookingPayload());
  assert.equal(r.status, 200);
  assert.deepEqual(await r.json(), { ok: true });
  assert.equal(await coordinatorEventSeq(env), 1);
});

await check("duplicate webhooks for the same change are normal — each accepted call still increments by 1 (dedup happens at the Cron/dispatch layer, not here)", async () => {
  const env = makeEnv();
  for (let i = 0; i < 10; i++) {
    const r = await post(env, validBookingPayload());
    assert.equal(r.status, 200);
  }
  assert.equal(await coordinatorEventSeq(env), 10);
});

await check("no guest PII (name/email/phone/comments) ever appears in the response body", async () => {
  const env = makeEnv();
  const r = await post(env, validBookingPayload());
  const text = await r.text();
  for (const pii of ["Taro", "Yamada", "taro@example.com", "090-0000-0000", "crib", "baby"]) {
    assert.ok(!text.includes(pii), `response leaked PII fragment: ${pii}`);
  }
});

await check("no guest PII ever appears in console.log output for accept or reject paths", async () => {
  const env = makeEnv();
  const originalLog = console.log;
  const logged = [];
  console.log = (...args) => logged.push(args.join(" "));
  try {
    await post(env, validBookingPayload());
    await post(env, validBookingPayload({ propertyId: 1 }));
    await post(env, null, { rawBody: "{bad", headers: { "content-length": "4" } });
  } finally {
    console.log = originalLog;
  }
  const allLogs = logged.join("\n");
  for (const pii of ["Taro", "Yamada", "taro@example.com", "090-0000-0000", "crib", "baby"]) {
    assert.ok(!allLogs.includes(pii), `console.log leaked PII fragment: ${pii}`);
  }
});

console.log(`\n${passed} Beds24 booking webhook checks passed`);
