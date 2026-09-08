// cleaningLiveState.js のテスト: CleaningLiveState Durable Objectの内部ロジック
// (/internal/state, /internal/mutate, WebSocket upgrade routing, broadcast,
// idempotency, history cap)。実際のCloudflare runtime(Miniflare)は使わず、
// このリポジトリの他のテストと同じ方針でstorage/ctxを素のオブジェクトで
// モックする(worker.test.mjsがKV/R2をモックするのと同じ考え方)。
//
// WebSocketPair/WebSocketはWorkers runtime専用のグローバルで、素のNodeには
// 無いため、このファイルの先頭で最小限のテスト用stubを用意する。
import assert from "node:assert";
import { CleaningLiveState } from "../src/cleaningLiveState.js";

if (typeof globalThis.WebSocketPair === "undefined") {
  class FakeWebSocket {
    constructor() {
      this.sent = [];
      this.closed = false;
      this.closeArgs = null;
    }
    send(message) { this.sent.push(message); }
    close(code, reason) { this.closed = true; this.closeArgs = [code, reason]; }
  }
  globalThis.WebSocketPair = class {
    constructor() {
      return [new FakeWebSocket(), new FakeWebSocket()];
    }
  };
}

function makeStorage() {
  const map = new Map();
  return {
    _map: map,
    async get(key) {
      return map.has(key) ? map.get(key) : undefined;
    },
    async put(key, value) {
      map.set(key, value);
    },
    async list({ prefix } = {}) {
      const entries = [];
      for (const [k, v] of map.entries()) {
        if (!prefix || k.startsWith(prefix)) entries.push([k, v]);
      }
      return entries;
    },
  };
}

function makeState() {
  const sockets = [];
  return {
    storage: makeStorage(),
    _sockets: sockets,
    acceptWebSocket(ws) { sockets.push(ws); },
    getWebSockets() { return sockets; },
  };
}

function req(url, init) {
  return new Request(url, init);
}

let passed = 0;
async function check(name, fn) { await fn(); passed++; console.log("ok -", name); }

// ---------------- /internal/state ----------------

await check("GET /internal/state returns an empty rooms object when nothing has been recorded yet", async () => {
  const state = makeState();
  const doInstance = new CleaningLiveState(state, {});
  const res = await doInstance.fetch(req("https://cleaning-live/internal/state"));
  assert.equal(res.status, 200);
  const body = await res.json();
  assert.deepEqual(body, { rooms: {} });
});

await check("unknown path/method returns 404", async () => {
  const state = makeState();
  const doInstance = new CleaningLiveState(state, {});
  const res = await doInstance.fetch(req("https://cleaning-live/nope"));
  assert.equal(res.status, 404);
});

// ---------------- /internal/mutate ----------------

await check("POST /internal/mutate on a room with no prior record treats WAITING_CHECKOUT as the implicit current status", async () => {
  const state = makeState();
  const doInstance = new CleaningLiveState(state, {});
  const res = await doInstance.fetch(req("https://cleaning-live/internal/mutate", {
    method: "POST",
    body: JSON.stringify({ date: "2026-09-08", roomNumber: "601", status: "CLEANING_ALLOWED" }),
  }));
  assert.equal(res.status, 200);
  const body = await res.json();
  assert.equal(body.ok, true);
  assert.equal(body.previousStatus, "WAITING_CHECKOUT");
  assert.equal(body.status, "CLEANING_ALLOWED");
  assert.ok(body.updatedAt);
});

await check("a successful mutation persists to storage and is visible via GET /internal/state", async () => {
  const state = makeState();
  const doInstance = new CleaningLiveState(state, {});
  await doInstance.fetch(req("https://cleaning-live/internal/mutate", {
    method: "POST",
    body: JSON.stringify({ date: "2026-09-08", roomNumber: "602", status: "CLEANING_ALLOWED" }),
  }));
  const res = await doInstance.fetch(req("https://cleaning-live/internal/state"));
  const body = await res.json();
  assert.equal(body.rooms["602"].status, "CLEANING_ALLOWED");
  assert.ok(body.rooms["602"].updatedAt);
});

await check("idempotent: posting the same status twice does not change updatedAt or add a second audit entry", async () => {
  const state = makeState();
  const doInstance = new CleaningLiveState(state, {});
  const first = await (await doInstance.fetch(req("https://cleaning-live/internal/mutate", {
    method: "POST",
    body: JSON.stringify({ date: "2026-09-08", roomNumber: "603", status: "CLEANING_ALLOWED" }),
  }))).json();
  const second = await (await doInstance.fetch(req("https://cleaning-live/internal/mutate", {
    method: "POST",
    body: JSON.stringify({ date: "2026-09-08", roomNumber: "603", status: "CLEANING_ALLOWED" }),
  }))).json();
  assert.equal(second.updatedAt, first.updatedAt);
  const history = await state.storage.get("history");
  assert.equal(history.length, 1, "idempotent no-op must not append a second audit entry");
});

await check("undo: CLEANING_ALLOWED -> WAITING_CHECKOUT is a normal transition, not blocked", async () => {
  const state = makeState();
  const doInstance = new CleaningLiveState(state, {});
  await doInstance.fetch(req("https://cleaning-live/internal/mutate", {
    method: "POST",
    body: JSON.stringify({ date: "2026-09-08", roomNumber: "604", status: "CLEANING_ALLOWED" }),
  }));
  const res = await doInstance.fetch(req("https://cleaning-live/internal/mutate", {
    method: "POST",
    body: JSON.stringify({ date: "2026-09-08", roomNumber: "604", status: "WAITING_CHECKOUT" }),
  }));
  const body = await res.json();
  assert.equal(body.previousStatus, "CLEANING_ALLOWED");
  assert.equal(body.status, "WAITING_CHECKOUT");
});

await check("POST /internal/mutate rejects an invalid status value", async () => {
  const state = makeState();
  const doInstance = new CleaningLiveState(state, {});
  const res = await doInstance.fetch(req("https://cleaning-live/internal/mutate", {
    method: "POST",
    body: JSON.stringify({ date: "2026-09-08", roomNumber: "605", status: "CHECKED_OUT" }),
  }));
  assert.equal(res.status, 400);
  assert.equal((await res.json()).error, "invalid_status");
});

await check("POST /internal/mutate rejects a missing/blank roomNumber", async () => {
  const state = makeState();
  const doInstance = new CleaningLiveState(state, {});
  const res = await doInstance.fetch(req("https://cleaning-live/internal/mutate", {
    method: "POST",
    body: JSON.stringify({ date: "2026-09-08", status: "CLEANING_ALLOWED" }),
  }));
  assert.equal(res.status, 400);
  assert.equal((await res.json()).error, "invalid_room");
});

await check("POST /internal/mutate rejects malformed JSON without throwing", async () => {
  const state = makeState();
  const doInstance = new CleaningLiveState(state, {});
  const res = await doInstance.fetch(req("https://cleaning-live/internal/mutate", {
    method: "POST",
    body: "{not json",
  }));
  assert.equal(res.status, 400);
  assert.equal((await res.json()).error, "invalid_json");
});

await check("fetch() routes an Upgrade: websocket request into acceptWebSocket() before hitting Node's runtime-only 101-Response limitation", async () => {
  const state = makeState();
  const doInstance = new CleaningLiveState(state, {});
  await assert.rejects(
    () => doInstance.fetch(req("https://cleaning-live/internal/connect", { headers: { Upgrade: "websocket" } })),
    /status.*200 to 599/,
  );
  assert.equal(state._sockets.length, 1, "acceptWebSocket must have been called before the (Node-only) Response(101) construction failure");
});

// ---------------- broadcast ----------------

// handleWebSocketUpgrade() itself constructs `new Response(null, {status:101,
// webSocket:...})`, which is a Workers-runtime-only Response shape that
// plain Node's fetch polyfill rejects (RangeError: status must be
// 200-599) — this is the same category as the other bootstrap files in this
// repo that touch runtime/browser-only globals and are therefore only
// source-checked, not executed, in Node tests. Here we instead simulate an
// already-upgraded connection by calling state.acceptWebSocket() directly
// (exactly what handleWebSocketUpgrade does internally after building the
// pair), so the broadcast logic itself is still fully exercised. The literal
// 101-upgrade code path is verified by local wrangler dev + the two-browser
// production acceptance test.
function connectFakeSocket(state) {
  const ws = new WebSocketPair()[1];
  state.acceptWebSocket(ws);
  return ws;
}

await check("a real (non-idempotent) mutation broadcasts a room_access_status message to every connected WebSocket", async () => {
  const state = makeState();
  const doInstance = new CleaningLiveState(state, {});
  connectFakeSocket(state);
  assert.equal(state._sockets.length, 1);

  await doInstance.fetch(req("https://cleaning-live/internal/mutate", {
    method: "POST",
    body: JSON.stringify({ date: "2026-09-08", roomNumber: "607", status: "CLEANING_ALLOWED" }),
  }));

  const sentSocket = state._sockets[0];
  assert.equal(sentSocket.sent.length, 1);
  const payload = JSON.parse(sentSocket.sent[0]);
  assert.equal(payload.type, "room_access_status");
  assert.equal(payload.date, "2026-09-08");
  assert.equal(payload.roomNumber, "607");
  assert.equal(payload.status, "CLEANING_ALLOWED");
  assert.ok(payload.updatedAt);
});

await check("an idempotent no-op does NOT broadcast anything", async () => {
  const state = makeState();
  const doInstance = new CleaningLiveState(state, {});
  connectFakeSocket(state);
  await doInstance.fetch(req("https://cleaning-live/internal/mutate", {
    method: "POST",
    body: JSON.stringify({ date: "2026-09-08", roomNumber: "601", status: "WAITING_CHECKOUT" }),
  }));
  assert.equal(state._sockets[0].sent.length, 0, "no state change -> no broadcast");
});

// ---------------- history cap ----------------

await check("history is capped at 100 entries (oldest dropped first)", async () => {
  const state = makeState();
  const doInstance = new CleaningLiveState(state, {});
  for (let i = 0; i < 105; i++) {
    const status = i % 2 === 0 ? "CLEANING_ALLOWED" : "WAITING_CHECKOUT";
    await doInstance.fetch(req("https://cleaning-live/internal/mutate", {
      method: "POST",
      body: JSON.stringify({ date: "2026-09-08", roomNumber: "601", status }),
    }));
  }
  const history = await state.storage.get("history");
  assert.equal(history.length, 100);
});

console.log(`\n${passed} cleaningLiveState checks passed`);
