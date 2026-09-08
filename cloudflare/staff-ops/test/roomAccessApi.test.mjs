// roomAccessApi.test.mjs — worker.js側の在室確認API:
//   - GET /api/cleaning が roomAccessStatus/roomAccessUpdatedAt をmergeすること
//   - POST /api/cleaning/access-status のバリデーション・認証・当日限定・
//     departing room限定・CleaningLiveState DOへの委譲
//
// CLEANING_LIVE binding は実際の CleaningLiveState クラスをin-memoryでラップした
// fakeネームスペースで代替する(worker.test.mjsがKV/R2をモックするのと同じ方針)。
// 日付は実際の todayJst() を使って組み立てる(POST側が今日以外を403で拒否する
// 仕様のため、固定の過去日ではPOST成功系のテストができない)。
import assert from "node:assert";
import worker from "../src/worker.js";
import { createSessionToken, SESSION_COOKIE_NAME } from "../src/auth.js";
import { KIRAKU_ROOM_ORDER } from "../src/roomMaster.js";
import { CleaningLiveState } from "../src/cleaningLiveState.js";
import { todayJst } from "../src/jstDate.js";

const TEST_PASSWORD = "test-shared-staff-password";
const TEST_SESSION_SECRET = "test-session-secret-do-not-use-in-prod";
const TEST_AUTH_VERSION = "1";
const TODAY = todayJst();

function buildSnapshotFor(date) {
  const rooms = KIRAKU_ROOM_ORDER.map((roomNumber, i) => {
    let status = "VACANT";
    if (i === 0) status = "TURNOVER"; // 401
    else if (i === 1) status = "CHECKOUT"; // 402
    else if (i === 2) status = "CHECKIN"; // 403
    else if (i === 3) status = "STAYOVER"; // 404
    return {
      date, room_number: roomNumber, status,
      departing_guest: null, arriving_guest: null, staying_guest: null,
      current_night_index: null, total_nights: null, source_instruction: "",
    };
  });
  return { dates: { [date]: { date, arrivals: [], departures: [], stayovers: [], cleaning: { rooms } } } };
}

function makeDoStorage() {
  const map = new Map();
  return {
    async get(key) { return map.has(key) ? map.get(key) : undefined; },
    async put(key, value) { map.set(key, value); },
    async list({ prefix } = {}) {
      const out = [];
      for (const [k, v] of map.entries()) if (!prefix || k.startsWith(prefix)) out.push([k, v]);
      return out;
    },
  };
}

// 実際のCleaningLiveStateクラスをin-memoryでラップしたfake DO namespace。
// idFromName/getは、実runtimeと同じく「同じnameなら同じインスタンス」を保証する。
function makeCleaningLiveNamespace() {
  const instances = new Map();
  function instanceFor(name) {
    if (!instances.has(name)) {
      const state = {
        storage: makeDoStorage(),
        _sockets: [],
        acceptWebSocket(ws) { this._sockets.push(ws); },
        getWebSockets() { return this._sockets; },
      };
      instances.set(name, new CleaningLiveState(state, {}));
    }
    return instances.get(name);
  }
  return {
    idFromName: (name) => name,
    get: (name) => ({
      fetch: (input, init) => {
        const request = typeof input === "string" ? new Request(input, init) : input;
        return instanceFor(name).fetch(request);
      },
    }),
  };
}

function makeEnv({ date = TODAY, withLiveDO = true } = {}) {
  const snapshot = buildSnapshotFor(date);
  const kvStore = {};
  return {
    ASSETS: { fetch: async () => new Response("ASSET", { status: 200 }) },
    OPS_DATA: { get: async (key) => (key === "latest/staff_ops_snapshot.json" ? { body: JSON.stringify(snapshot) } : null) },
    CLEANING_OVERRIDES: {
      get: async (key) => (key in kvStore ? kvStore[key] : null),
      put: async (key, value) => { kvStore[key] = value; },
      delete: async (key) => { delete kvStore[key]; },
    },
    CLEANING_LIVE: withLiveDO ? makeCleaningLiveNamespace() : undefined,
    STAFF_OPS_PASSWORD: TEST_PASSWORD,
    STAFF_OPS_SESSION_SECRET: TEST_SESSION_SECRET,
    AUTH_VERSION: TEST_AUTH_VERSION,
  };
}

async function validCookieHeader(env) {
  const token = await createSessionToken(env.STAFF_OPS_SESSION_SECRET, env.AUTH_VERSION);
  return `${SESSION_COOKIE_NAME}=${token}`;
}

const get = (env, p, headers) => worker.fetch(new Request("https://x" + p, { headers: headers || {} }), env);
const post = (env, p, body, headers) => worker.fetch(new Request("https://x" + p, {
  method: "POST",
  headers: { "content-type": "application/json", ...(headers || {}) },
  body: JSON.stringify(body),
}), env);

let passed = 0;
async function check(name, fn) { await fn(); passed++; console.log("ok -", name); }

// ---------------- GET /api/cleaning merges roomAccessStatus ----------------

await check("GET /api/cleaning attaches roomAccessStatus=WAITING_CHECKOUT to departing rooms (TURNOVER/CHECKOUT) with no live record yet", async () => {
  const env = makeEnv();
  const cookie = await validCookieHeader(env);
  const r = await get(env, `/api/cleaning?date=${TODAY}`, { Cookie: cookie });
  const j = await r.json();
  const turnover = j.rooms.find((x) => x.room_number === KIRAKU_ROOM_ORDER[0]);
  const checkout = j.rooms.find((x) => x.room_number === KIRAKU_ROOM_ORDER[1]);
  assert.equal(turnover.roomAccessStatus, "WAITING_CHECKOUT");
  assert.equal(checkout.roomAccessStatus, "WAITING_CHECKOUT");
  assert.equal(turnover.roomAccessUpdatedAt, null);
});

await check("GET /api/cleaning attaches roomAccessStatus=null to non-departing rooms (CHECKIN/STAYOVER/VACANT)", async () => {
  const env = makeEnv();
  const cookie = await validCookieHeader(env);
  const r = await get(env, `/api/cleaning?date=${TODAY}`, { Cookie: cookie });
  const j = await r.json();
  const checkin = j.rooms.find((x) => x.room_number === KIRAKU_ROOM_ORDER[2]);
  const stayover = j.rooms.find((x) => x.room_number === KIRAKU_ROOM_ORDER[3]);
  const vacant = j.rooms.find((x) => x.room_number === KIRAKU_ROOM_ORDER[4]);
  assert.equal(checkin.roomAccessStatus, null);
  assert.equal(stayover.roomAccessStatus, null);
  assert.equal(vacant.roomAccessStatus, null);
});

await check("GET /api/cleaning degrades to WAITING_CHECKOUT/null defaults (never throws) when CLEANING_LIVE binding is missing", async () => {
  const env = makeEnv({ withLiveDO: false });
  const cookie = await validCookieHeader(env);
  const r = await get(env, `/api/cleaning?date=${TODAY}`, { Cookie: cookie });
  assert.equal(r.status, 200);
  const j = await r.json();
  const turnover = j.rooms.find((x) => x.room_number === KIRAKU_ROOM_ORDER[0]);
  assert.equal(turnover.roomAccessStatus, "WAITING_CHECKOUT");
});

await check("GET /api/cleaning reflects a live CLEANING_ALLOWED record from the Durable Object", async () => {
  const env = makeEnv();
  const cookie = await validCookieHeader(env);
  await post(env, "/api/cleaning/access-status", {
    date: TODAY, roomNumber: KIRAKU_ROOM_ORDER[0], status: "CLEANING_ALLOWED",
  }, { Cookie: cookie });
  const r = await get(env, `/api/cleaning?date=${TODAY}`, { Cookie: cookie });
  const j = await r.json();
  const turnover = j.rooms.find((x) => x.room_number === KIRAKU_ROOM_ORDER[0]);
  assert.equal(turnover.roomAccessStatus, "CLEANING_ALLOWED");
  assert.ok(turnover.roomAccessUpdatedAt);
});

// ---------------- POST /api/cleaning/access-status ----------------

await check("unauthenticated POST /api/cleaning/access-status is rejected by the auth middleware (401) before any body validation", async () => {
  const env = makeEnv();
  const r = await post(env, "/api/cleaning/access-status", {
    date: TODAY, roomNumber: KIRAKU_ROOM_ORDER[0], status: "CLEANING_ALLOWED",
  });
  assert.equal(r.status, 401);
});

await check("authenticated POST succeeds for an eligible departing room, today, matching Origin", async () => {
  const env = makeEnv();
  const cookie = await validCookieHeader(env);
  const r = await post(env, "/api/cleaning/access-status", {
    date: TODAY, roomNumber: KIRAKU_ROOM_ORDER[0], status: "CLEANING_ALLOWED",
  }, { Cookie: cookie, Origin: "https://x" });
  assert.equal(r.status, 200);
  const j = await r.json();
  assert.equal(j.ok, true);
  assert.equal(j.status, "CLEANING_ALLOWED");
  assert.equal(j.previousStatus, "WAITING_CHECKOUT");
});

await check("POST rejects a cross-origin Origin header (403)", async () => {
  const env = makeEnv();
  const cookie = await validCookieHeader(env);
  const r = await post(env, "/api/cleaning/access-status", {
    date: TODAY, roomNumber: KIRAKU_ROOM_ORDER[0], status: "CLEANING_ALLOWED",
  }, { Cookie: cookie, Origin: "https://evil.example.com" });
  assert.equal(r.status, 403);
  assert.equal((await r.json()).error, "origin_not_allowed");
});

await check("POST rejects an invalid body shape (extra key) with 400 invalid_payload, same as the override endpoint", async () => {
  const env = makeEnv();
  const cookie = await validCookieHeader(env);
  const r = await post(env, "/api/cleaning/access-status", {
    date: TODAY, roomNumber: KIRAKU_ROOM_ORDER[0], status: "CLEANING_ALLOWED", price: 9800,
  }, { Cookie: cookie });
  assert.equal(r.status, 400);
  assert.equal((await r.json()).error, "invalid_payload");
});

await check("POST rejects an invalid date format", async () => {
  const env = makeEnv();
  const cookie = await validCookieHeader(env);
  const r = await post(env, "/api/cleaning/access-status", {
    date: "20260908", roomNumber: KIRAKU_ROOM_ORDER[0], status: "CLEANING_ALLOWED",
  }, { Cookie: cookie });
  assert.equal(r.status, 400);
  assert.equal((await r.json()).error, "invalid_date");
});

await check("POST rejects a room number outside KIRAKU_ROOM_ORDER", async () => {
  const env = makeEnv();
  const cookie = await validCookieHeader(env);
  const r = await post(env, "/api/cleaning/access-status", {
    date: TODAY, roomNumber: "999", status: "CLEANING_ALLOWED",
  }, { Cookie: cookie });
  assert.equal(r.status, 400);
  assert.equal((await r.json()).error, "invalid_room");
});

await check("POST rejects a status value outside the WAITING_CHECKOUT/CLEANING_ALLOWED enum", async () => {
  const env = makeEnv();
  const cookie = await validCookieHeader(env);
  const r = await post(env, "/api/cleaning/access-status", {
    date: TODAY, roomNumber: KIRAKU_ROOM_ORDER[0], status: "CHECKED_OUT",
  }, { Cookie: cookie });
  assert.equal(r.status, 400);
  assert.equal((await r.json()).error, "invalid_status");
});

await check("POST rejects malformed JSON with 400 invalid_json, not a 500", async () => {
  const env = makeEnv();
  const cookie = await validCookieHeader(env);
  const r = await worker.fetch(new Request("https://x/api/cleaning/access-status", {
    method: "POST", headers: { "content-type": "application/json", Cookie: cookie }, body: "{not json",
  }), env);
  assert.equal(r.status, 400);
  assert.equal((await r.json()).error, "invalid_json");
});

await check("POST is rejected for a date other than today JST (403 date_locked) — past/future dates are read-only", async () => {
  const env = makeEnv();
  const cookie = await validCookieHeader(env);
  const r = await post(env, "/api/cleaning/access-status", {
    date: "2020-01-01", roomNumber: KIRAKU_ROOM_ORDER[0], status: "CLEANING_ALLOWED",
  }, { Cookie: cookie });
  assert.equal(r.status, 403);
  assert.equal((await r.json()).error, "date_locked");
});

await check("POST is rejected (409 invalid_transition) for a non-departing room (STAYOVER)", async () => {
  const env = makeEnv();
  const cookie = await validCookieHeader(env);
  const r = await post(env, "/api/cleaning/access-status", {
    date: TODAY, roomNumber: KIRAKU_ROOM_ORDER[3], status: "CLEANING_ALLOWED",
  }, { Cookie: cookie });
  assert.equal(r.status, 409);
  assert.equal((await r.json()).error, "invalid_transition");
});

await check("POST is rejected (409 invalid_transition) for a VACANT room", async () => {
  const env = makeEnv();
  const cookie = await validCookieHeader(env);
  const r = await post(env, "/api/cleaning/access-status", {
    date: TODAY, roomNumber: KIRAKU_ROOM_ORDER[4], status: "CLEANING_ALLOWED",
  }, { Cookie: cookie });
  assert.equal(r.status, 409);
  assert.equal((await r.json()).error, "invalid_transition");
});

await check("POST is rejected (409 invalid_transition) for a CHECKIN-only room (arrival, not a departure)", async () => {
  const env = makeEnv();
  const cookie = await validCookieHeader(env);
  const r = await post(env, "/api/cleaning/access-status", {
    date: TODAY, roomNumber: KIRAKU_ROOM_ORDER[2], status: "CLEANING_ALLOWED",
  }, { Cookie: cookie });
  assert.equal(r.status, 409);
  assert.equal((await r.json()).error, "invalid_transition");
});

await check("POST returns 404 not_found when the date has no snapshot data at all", async () => {
  const env = makeEnv();
  const cookie = await validCookieHeader(env);
  const r = await post(env, "/api/cleaning/access-status", {
    date: TODAY, roomNumber: KIRAKU_ROOM_ORDER[0], status: "CLEANING_ALLOWED",
  }, { Cookie: cookie });
  // sanity: this env's snapshot only has TODAY populated; simulate a date with none.
  const env2 = makeEnv();
  env2.OPS_DATA.get = async () => null;
  const cookie2 = await validCookieHeader(env2);
  const r2 = await post(env2, "/api/cleaning/access-status", {
    date: TODAY, roomNumber: KIRAKU_ROOM_ORDER[0], status: "CLEANING_ALLOWED",
  }, { Cookie: cookie2 });
  assert.equal(r.status, 200); // control: the original env's normal path still works
  assert.equal(r2.status, 404);
  assert.equal((await r2.json()).error, "not_found");
});

await check("POST returns 500 do_unavailable when the CLEANING_LIVE Durable Object binding is missing", async () => {
  const env = makeEnv({ withLiveDO: false });
  const cookie = await validCookieHeader(env);
  const r = await post(env, "/api/cleaning/access-status", {
    date: TODAY, roomNumber: KIRAKU_ROOM_ORDER[0], status: "CLEANING_ALLOWED",
  }, { Cookie: cookie });
  assert.equal(r.status, 500);
  assert.equal((await r.json()).error, "do_unavailable");
});

await check("POST is idempotent at the API level too: posting CLEANING_ALLOWED twice both return 200 with the same status", async () => {
  const env = makeEnv();
  const cookie = await validCookieHeader(env);
  const first = await post(env, "/api/cleaning/access-status", {
    date: TODAY, roomNumber: KIRAKU_ROOM_ORDER[1], status: "CLEANING_ALLOWED",
  }, { Cookie: cookie });
  const second = await post(env, "/api/cleaning/access-status", {
    date: TODAY, roomNumber: KIRAKU_ROOM_ORDER[1], status: "CLEANING_ALLOWED",
  }, { Cookie: cookie });
  assert.equal(first.status, 200);
  assert.equal(second.status, 200);
  assert.equal((await second.json()).status, "CLEANING_ALLOWED");
});

await check("undo (CLEANING_ALLOWED -> WAITING_CHECKOUT) succeeds as a normal POST, not blocked as a downgrade", async () => {
  const env = makeEnv();
  const cookie = await validCookieHeader(env);
  await post(env, "/api/cleaning/access-status", {
    date: TODAY, roomNumber: KIRAKU_ROOM_ORDER[0], status: "CLEANING_ALLOWED",
  }, { Cookie: cookie });
  const r = await post(env, "/api/cleaning/access-status", {
    date: TODAY, roomNumber: KIRAKU_ROOM_ORDER[0], status: "WAITING_CHECKOUT",
  }, { Cookie: cookie });
  assert.equal(r.status, 200);
  const j = await r.json();
  assert.equal(j.previousStatus, "CLEANING_ALLOWED");
  assert.equal(j.status, "WAITING_CHECKOUT");
});

// ---------------- Date isolation across two different dates' DOs ----------------

await check("two different dates never share room_access_status state (separate Durable Object instances)", async () => {
  const env = makeEnv();
  const cookie = await validCookieHeader(env);
  await post(env, "/api/cleaning/access-status", {
    date: TODAY, roomNumber: KIRAKU_ROOM_ORDER[0], status: "CLEANING_ALLOWED",
  }, { Cookie: cookie });

  // A different date's snapshot, same room number, same env (same fake DO namespace).
  const otherDate = "2099-01-01";
  const otherSnapshot = buildSnapshotFor(otherDate);
  const originalGet = env.OPS_DATA.get;
  env.OPS_DATA.get = async (key) => (key === "latest/staff_ops_snapshot.json" ? { body: JSON.stringify(otherSnapshot) } : null);
  const r = await get(env, `/api/cleaning?date=${otherDate}`, { Cookie: cookie });
  const j = await r.json();
  const turnover = j.rooms.find((x) => x.room_number === KIRAKU_ROOM_ORDER[0]);
  assert.equal(turnover.roomAccessStatus, "WAITING_CHECKOUT", "a different date's DO must start fresh, not inherit TODAY's CLEANING_ALLOWED");
  env.OPS_DATA.get = originalGet;
});

console.log(`\n${passed} roomAccessApi checks passed`);
