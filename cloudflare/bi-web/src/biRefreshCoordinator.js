// biRefreshCoordinator.js — BiRefreshCoordinator Durable Object (SQLite-backed).
//
// Single global singleton (Worker always addresses it via
// `env.BI_REFRESH_COORDINATOR.idFromName(COORDINATOR_INSTANCE_NAME)`). Holds
// ONLY refresh-scheduling state — no Beds24 booking data, no guest PII, no
// financial figures. See wrangler.toml [[migrations]] new_sqlite_classes.
//
// This is a monotonic-generation coordinator, not a boolean "dirty" flag:
// event_seq only ever increases (one per accepted Beds24 webhook), and
// last_completed_seq only ever advances via completion callbacks
// (Math.max, never assigned backwards). This is what makes the design safe
// against the classic race:
//   event A -> event_seq=1 (dispatch reserves target_seq=1)
//   [action running]
//   event B -> event_seq=2                  <-- would be LOST by a boolean flag
//   action completes -> last_completed_seq=1
//   next evaluate(): event_seq(2) > last_completed_seq(1) -> dispatch again
// A boolean `dirty=false` reset on completion would instead have cleared
// event B's signal along with event A's, because both compress to the same
// single bit. See PR description "Why boolean dirty was rejected".
//
// All state mutation happens inside this DO via plain KV storage.get/put on
// ONE JSON blob (key STATE_KEY) — Durable Object requests to the same
// instance are processed one at a time (single-threaded per instance), and
// every method below does its read-modify-write with no `await` on
// anything other than storage itself in between, so there is no window for
// another request's code to interleave mid-mutation. Network calls (the
// actual GitHub workflow_dispatch API call, the actual GitHub Actions run)
// happen OUTSIDE this DO, in worker.js — this DO only ever hands out and
// reconciles *reservations*, it never itself calls fetch() to an external
// service. That is a deliberate simplification: it means this class's
// concurrency story never depends on Workers' input/output gate nuances
// around awaiting non-storage I/O.
import { todayJst } from "./jstDate.js";
import {
  MODE_ACTIVE, MODE_SHADOW, VALID_MODES,
  envDefaultMode, envFullReconcileMaxAgeSeconds, envLeaseTimeoutSeconds,
} from "./biRefreshConfig.js";

const STATE_KEY = "state";

function defaultState() {
  return {
    event_seq: 0,
    last_completed_seq: 0,
    in_flight_dispatch_id: null,
    in_flight_target_seq: null,
    in_flight_started_at: null,
    in_flight_reason: null,
    last_webhook_at: null,
    last_dispatch_at: null,
    last_success_at: null,
    last_full_reconcile_at: null,
    last_successful_jst_date: null,
    consecutive_failures: 0,
    last_failure_at: null,
    mode: null, // null = not explicitly set yet; effective mode falls back to env default
    force_dispatch_requested: false,
  };
}

function jsonResponse(obj, status = 200) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { "content-type": "application/json; charset=utf-8" },
  });
}

function clearInFlight(state) {
  state.in_flight_dispatch_id = null;
  state.in_flight_target_seq = null;
  state.in_flight_started_at = null;
  state.in_flight_reason = null;
}

export class BiRefreshCoordinator {
  constructor(state, env) {
    this.state = state;
    this.env = env;
  }

  async #load() {
    const stored = await this.state.storage.get(STATE_KEY);
    return stored || defaultState();
  }

  async #save(state) {
    await this.state.storage.put(STATE_KEY, state);
  }

  async fetch(request) {
    const url = new URL(request.url);
    const { pathname } = url;
    try {
      if (request.method === "POST" && pathname === "/internal/event") {
        return await this.handleEvent();
      }
      if (request.method === "POST" && pathname === "/internal/evaluate") {
        return await this.handleEvaluate(await this.#readJson(request));
      }
      if (request.method === "POST" && pathname === "/internal/dispatch-failure") {
        return await this.handleDispatchFailure(await this.#readJson(request));
      }
      if (request.method === "POST" && pathname === "/internal/complete") {
        return await this.handleComplete(await this.#readJson(request));
      }
      if (request.method === "POST" && pathname === "/internal/force") {
        return await this.handleForce();
      }
      if (request.method === "POST" && pathname === "/internal/mode") {
        return await this.handleSetMode(await this.#readJson(request));
      }
      if (request.method === "GET" && pathname === "/internal/status") {
        return await this.handleStatus();
      }
    } catch (e) {
      if (e instanceof SyntaxError) return jsonResponse({ error: "invalid_json" }, 400);
      throw e;
    }
    return jsonResponse({ error: "not_found" }, 404);
  }

  async #readJson(request) {
    const text = await request.text();
    if (!text) return {};
    return JSON.parse(text);
  }

  // 1 accepted Beds24 webhook = 1 event_seq increment. Never resets to a
  // boolean — see module comment.
  async handleEvent() {
    const state = await this.#load();
    state.event_seq += 1;
    state.last_webhook_at = new Date().toISOString();
    await this.#save(state);
    return jsonResponse({ event_seq: state.event_seq });
  }

  async handleForce() {
    const state = await this.#load();
    state.force_dispatch_requested = true;
    await this.#save(state);
    return jsonResponse({ ok: true });
  }

  async handleSetMode(body) {
    const mode = body && body.mode;
    if (!VALID_MODES.includes(mode)) {
      return jsonResponse({ error: "invalid_mode", valid_modes: VALID_MODES }, 400);
    }
    const state = await this.#load();
    state.mode = mode;
    await this.#save(state);
    return jsonResponse({ mode: state.mode });
  }

  // Core decision (Cron planner). Mutates state for: stale-lease reclaim
  // (always, regardless of mode) and, only when mode=active AND a dispatch
  // is warranted, the atomic reservation of a new in-flight dispatch.
  // mode=shadow computes the same would_dispatch/reason but never reserves
  // — the existing unconditional Cron dispatch keeps running in shadow so
  // the planner can be observed against real traffic before it controls
  // anything (Stage B in the PR description).
  async handleEvaluate(body) {
    const state = await this.#load();
    const nowIso = (body && body.now_iso) || new Date().toISOString();
    const nowMs = Date.parse(nowIso);
    const todayJstStr = (body && body.today_jst) || todayJst(nowMs);
    const leaseTimeoutSeconds = envLeaseTimeoutSeconds(this.env);
    const fullReconcileMaxAgeSeconds = envFullReconcileMaxAgeSeconds(this.env);
    const mode = state.mode || envDefaultMode(this.env);

    let staleReclaimed = false;
    if (state.in_flight_dispatch_id) {
      const startedMs = Date.parse(state.in_flight_started_at || "");
      if (Number.isFinite(startedMs) && (nowMs - startedMs) > leaseTimeoutSeconds * 1000) {
        staleReclaimed = true;
        clearInFlight(state);
      }
    }

    if (state.in_flight_dispatch_id) {
      await this.#save(state);
      return jsonResponse({
        would_dispatch: false, reason: "in_flight", mode,
        event_seq: state.event_seq, last_completed_seq: state.last_completed_seq,
        stale_reclaimed: staleReclaimed,
      });
    }

    let reason = null;
    if (state.force_dispatch_requested) {
      reason = "manual_force";
    } else if (state.last_successful_jst_date && todayJstStr !== state.last_successful_jst_date) {
      reason = "jst_date_rollover";
    } else if (state.event_seq > state.last_completed_seq) {
      reason = "booking_webhook";
    } else {
      const lastReconcileMs = state.last_full_reconcile_at ? Date.parse(state.last_full_reconcile_at) : NaN;
      const ageSeconds = Number.isFinite(lastReconcileMs) ? (nowMs - lastReconcileMs) / 1000 : Infinity;
      if (ageSeconds > fullReconcileMaxAgeSeconds) {
        reason = "full_reconciliation";
      }
    }

    const wouldDispatch = reason != null;

    if (mode !== MODE_ACTIVE || !wouldDispatch) {
      await this.#save(state); // persist any stale-lease reclaim even when not dispatching
      return jsonResponse({
        would_dispatch: wouldDispatch, reason, mode,
        event_seq: state.event_seq, last_completed_seq: state.last_completed_seq,
        stale_reclaimed: staleReclaimed,
      });
    }

    // active mode, dispatch warranted: reserve atomically.
    const dispatchId = crypto.randomUUID();
    state.in_flight_dispatch_id = dispatchId;
    state.in_flight_target_seq = state.event_seq;
    state.in_flight_started_at = nowIso;
    state.in_flight_reason = reason;
    state.last_dispatch_at = nowIso;
    if (reason === "manual_force") state.force_dispatch_requested = false;
    await this.#save(state);

    return jsonResponse({
      would_dispatch: true, reason, mode,
      event_seq: state.event_seq, last_completed_seq: state.last_completed_seq,
      stale_reclaimed: staleReclaimed,
      dispatch_id: dispatchId, target_seq: state.in_flight_target_seq,
    });
  }

  // Called by worker.js right after a failed (non-204/network/timeout)
  // GitHub workflow_dispatch API call, so the reservation doesn't sit
  // blocking new dispatches until its lease times out. Only releases /
  // counts a failure when dispatch_id matches the CURRENT in-flight
  // reservation — a stale/duplicate report can never clobber a newer,
  // unrelated in-flight dispatch (see "wrong dispatch_id" test coverage).
  async handleDispatchFailure(body) {
    const state = await this.#load();
    const dispatchId = body && body.dispatch_id;
    const matches = Boolean(dispatchId) && dispatchId === state.in_flight_dispatch_id;
    if (matches) clearInFlight(state);
    if (matches) {
      state.consecutive_failures += 1;
      state.last_failure_at = (body && body.now_iso) || new Date().toISOString();
    }
    await this.#save(state);
    return jsonResponse({ ok: true, released: matches });
  }

  // GitHub Actions completion callback (always sent, even on job failure —
  // see refresh-bi-r2.yml's `if: always()` step). success unconditionally
  // advances last_completed_seq via Math.max (monotonic, idempotent,
  // never regresses — safe even for a duplicate or out-of-order/"old"
  // callback). in_flight is only cleared when dispatch_id matches the
  // CURRENT reservation, so a stale/duplicate/wrong-dispatch-id callback
  // can never clear a different, still-running dispatch's lease.
  async handleComplete(body) {
    const dispatchId = body && body.dispatch_id;
    const targetSeq = body && body.target_seq;
    const status = body && body.status;
    if (typeof dispatchId !== "string" || !dispatchId) {
      return jsonResponse({ error: "invalid_dispatch_id" }, 400);
    }
    if (!Number.isInteger(targetSeq) || targetSeq < 0) {
      return jsonResponse({ error: "invalid_target_seq" }, 400);
    }
    if (status !== "success" && status !== "failure") {
      return jsonResponse({ error: "invalid_status" }, 400);
    }

    const state = await this.#load();
    const nowIso = (body && body.completed_at) || new Date().toISOString();
    const matchesInFlight = dispatchId === state.in_flight_dispatch_id;

    if (status === "success") {
      state.last_completed_seq = Math.max(state.last_completed_seq, targetSeq);
      state.last_success_at = nowIso;
      // 既存パイプラインは常にBeds24全件再取得（差分取得は存在しない）ため、
      // どの理由でdispatchされたかに関わらず、成功=full reconciliation完了
      // とみなしてよい。full reconcile専用のjob typeを別途持つ必要が無い。
      state.last_full_reconcile_at = nowIso;
      state.last_successful_jst_date = todayJst(nowIso);
      state.consecutive_failures = 0;
    } else {
      state.consecutive_failures += 1;
      state.last_failure_at = nowIso;
    }

    if (matchesInFlight) clearInFlight(state);

    await this.#save(state);
    return jsonResponse({ ok: true, last_completed_seq: state.last_completed_seq, matched_in_flight: matchesInFlight });
  }

  // Read-only. PII-safe by construction: only ever built from this DO's own
  // scheduling state, which never holds Beds24 booking data or guest PII.
  async handleStatus() {
    const state = await this.#load();
    const mode = state.mode || envDefaultMode(this.env);
    const fullReconcileMaxAgeSeconds = envFullReconcileMaxAgeSeconds(this.env);
    const lastReconcileMs = state.last_full_reconcile_at ? Date.parse(state.last_full_reconcile_at) : null;
    const nextReconcileDueAt = lastReconcileMs != null
      ? new Date(lastReconcileMs + fullReconcileMaxAgeSeconds * 1000).toISOString()
      : null;
    const leaseTimeoutSeconds = envLeaseTimeoutSeconds(this.env);
    const inFlightStartedMs = state.in_flight_started_at ? Date.parse(state.in_flight_started_at) : null;
    const inFlightStale = inFlightStartedMs != null
      && (Date.now() - inFlightStartedMs) > leaseTimeoutSeconds * 1000;

    return jsonResponse({
      mode,
      event_seq: state.event_seq,
      last_completed_seq: state.last_completed_seq,
      dirty_count: Math.max(0, state.event_seq - state.last_completed_seq),
      in_flight: state.in_flight_dispatch_id ? {
        dispatch_id: state.in_flight_dispatch_id,
        target_seq: state.in_flight_target_seq,
        started_at: state.in_flight_started_at,
        reason: state.in_flight_reason,
        stale: inFlightStale,
      } : null,
      force_dispatch_requested: state.force_dispatch_requested,
      last_webhook_at: state.last_webhook_at,
      last_dispatch_at: state.last_dispatch_at,
      last_success_at: state.last_success_at,
      last_full_reconcile_at: state.last_full_reconcile_at,
      last_successful_jst_date: state.last_successful_jst_date,
      consecutive_failures: state.consecutive_failures,
      last_failure_at: state.last_failure_at,
      next_reconcile_due_at: nextReconcileDueAt,
    });
  }
}

export { MODE_ACTIVE, MODE_SHADOW };
