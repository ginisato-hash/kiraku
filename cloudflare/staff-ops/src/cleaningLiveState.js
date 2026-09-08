// cleaningLiveState.js — CleaningLiveState Durable Object (SQLite-backed).
//
// One instance per business date: the Worker always addresses it via
// `env.CLEANING_LIVE.idFromName(date)`, so "2026-09-08" and "2026-09-09" are
// two entirely separate DO instances/storage — state never carries over to
// the next day (spec: date isolation, no daily rollover job needed).
//
// This DO holds ONLY: per-room room_access_status + updatedAt, and a capped
// audit history. It never receives or stores guest name/address/phone/
// payment/price/any PII or financial data — the Worker validates eligibility
// (which rooms may transition, whether the date is today) BEFORE calling
// this DO; this class only knows about room numbers and the two-value enum.
//
// Storage (Durable Object KV-style API — SQLite-backed for new namespaces,
// see wrangler.toml [[migrations]] new_sqlite_classes):
//   `room:{roomNumber}` -> { status, updatedAt }
//   `history`           -> capped array (last MAX_HISTORY) of
//                          { roomNumber, from, to, updatedAt, source }
//
// Realtime: Hibernation WebSocket API (this.state.acceptWebSocket /
// this.state.getWebSockets()) — connections survive DO hibernation between
// messages without keeping the DO warm/billed for idle time.
import { ROOM_ACCESS_STATUSES, DEFAULT_ROOM_ACCESS_STATUS, isValidRoomAccessStatus } from "./roomAccessState.js";

const ROOM_KEY_PREFIX = "room:";
const HISTORY_KEY = "history";
const MAX_HISTORY = 100;

function roomKey(roomNumber) {
  return `${ROOM_KEY_PREFIX}${roomNumber}`;
}

function jsonResponse(obj, status = 200) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { "content-type": "application/json; charset=utf-8" },
  });
}

export class CleaningLiveState {
  constructor(state, env) {
    this.state = state;
    this.env = env;
  }

  async fetch(request) {
    const upgrade = request.headers.get("Upgrade") || "";
    if (upgrade.toLowerCase() === "websocket") {
      return this.handleWebSocketUpgrade();
    }

    const url = new URL(request.url);
    if (url.pathname === "/internal/state" && request.method === "GET") {
      return this.handleGetState();
    }
    if (url.pathname === "/internal/mutate" && request.method === "POST") {
      return this.handleMutate(request);
    }
    return jsonResponse({ error: "not_found" }, 404);
  }

  handleWebSocketUpgrade() {
    const pair = new WebSocketPair();
    // Hibernation API: the DO can evict from memory between messages while
    // the socket itself stays open at the edge — required for a real-time
    // view that a cleaner may leave open on a phone for hours.
    this.state.acceptWebSocket(pair[1]);
    return new Response(null, { status: 101, webSocket: pair[0] });
  }

  async handleGetState() {
    const stored = await this.state.storage.list({ prefix: ROOM_KEY_PREFIX });
    const rooms = {};
    for (const [key, value] of stored) {
      rooms[key.slice(ROOM_KEY_PREFIX.length)] = value;
    }
    return jsonResponse({ rooms });
  }

  async handleMutate(request) {
    let body;
    try {
      body = await request.json();
    } catch (e) {
      return jsonResponse({ error: "invalid_json" }, 400);
    }
    if (!body || typeof body !== "object") return jsonResponse({ error: "invalid_payload" }, 400);
    const { date, roomNumber, status } = body;
    if (typeof roomNumber !== "string" || !roomNumber) return jsonResponse({ error: "invalid_room" }, 400);
    if (!isValidRoomAccessStatus(status)) return jsonResponse({ error: "invalid_status" }, 400);

    const key = roomKey(roomNumber);
    const current = (await this.state.storage.get(key)) || { status: DEFAULT_ROOM_ACCESS_STATUS, updatedAt: null };

    // Idempotent no-op: posting the same status twice (double-tap, retry
    // after a dropped response) must not bump updatedAt, add an audit entry,
    // or broadcast a redundant message — see spec item 39.
    if (current.status === status) {
      return jsonResponse({ ok: true, previousStatus: current.status, status, updatedAt: current.updatedAt });
    }

    const updatedAt = new Date().toISOString();
    const record = { status, updatedAt };
    // Durable Object requests to the same instance are serialized, so a
    // plain read-then-write here (no await between them below) is already
    // safe against concurrent front-desk mutations — see spec item 40.
    await this.state.storage.put(key, record);
    await this.appendHistory({ roomNumber, from: current.status, to: status, updatedAt, source: "staff_web" });

    this.broadcast({ type: "room_access_status", date, roomNumber, status, updatedAt });

    return jsonResponse({ ok: true, previousStatus: current.status, status, updatedAt });
  }

  async appendHistory(entry) {
    const history = (await this.state.storage.get(HISTORY_KEY)) || [];
    history.push(entry);
    while (history.length > MAX_HISTORY) history.shift();
    await this.state.storage.put(HISTORY_KEY, history);
  }

  broadcast(payload) {
    const message = JSON.stringify(payload);
    for (const ws of this.state.getWebSockets()) {
      try {
        ws.send(message);
      } catch (e) {
        // Best-effort: a socket that fails to send is already dead/closing
        // and will be cleaned up by webSocketClose/webSocketError below.
      }
    }
  }

  // The cleaner client (public/cleaning/today.js) is a passive listener —
  // there is no client->server protocol today. Required for the Hibernation
  // API contract even though it currently does nothing with the message.
  async webSocketMessage(ws, message) {
    // no-op
  }

  async webSocketClose(ws, code, reason, wasClean) {
    try {
      ws.close(code, reason);
    } catch (e) {
      // already closing
    }
  }

  async webSocketError(ws, error) {
    // No in-memory state to clean up (storage is the source of truth), and
    // the runtime removes the socket from getWebSockets() automatically.
  }
}

export { ROOM_ACCESS_STATUSES };
