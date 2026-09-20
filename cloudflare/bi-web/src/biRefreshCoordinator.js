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
    // Shadow semantic false-negative observation (KIRAKU BI PHASE 2). PII-safe
    // aggregate counters only — never any booking/guest content. See
    // handleObservation() for the counting rules.
    shadow_observations_total: 0,
    shadow_planner_skip_total: 0,
    shadow_skip_bi_changed_total: 0,
    shadow_skip_staff_ops_changed_total: 0,
    shadow_skip_any_changed_total: 0,
    shadow_observation_errors_total: 0,
    last_shadow_observation_at: null,
    last_shadow_observation_error_at: null,
    last_shadow_false_negative_at: null,
    // Reason counters (PHASE 2 completeness requirement): per-unique-
    // observation breakdown of `reason`, so the webhook-dirty denominator
    // (booking_webhook) can be told apart from shadow_unconditional/
    // full_reconciliation/jst_date_rollover/manual_force. A reason outside
    // this known set still increments shadow_reason_other_total rather than
    // being silently dropped (see REASON_COUNTER_KEYS below).
    shadow_reason_shadow_unconditional_total: 0,
    shadow_reason_booking_webhook_total: 0,
    shadow_reason_full_reconciliation_total: 0,
    shadow_reason_jst_date_rollover_total: 0,
    shadow_reason_manual_force_total: 0,
    shadow_reason_other_total: 0,
    // Bounded recent-dispatch-id set for observation idempotency (Blocker 4).
    // 96 dispatches/day at the 15-minute cadence, so 512 entries covers
    // several days — comfortably more than the shadow-observation window.
    // A plain array (not a Set) because DO storage serializes as JSON.
    shadow_observed_dispatch_ids: [],
  };
}

const REASON_COUNTER_KEYS = {
  shadow_unconditional: "shadow_reason_shadow_unconditional_total",
  booking_webhook: "shadow_reason_booking_webhook_total",
  full_reconciliation: "shadow_reason_full_reconciliation_total",
  jst_date_rollover: "shadow_reason_jst_date_rollover_total",
  manual_force: "shadow_reason_manual_force_total",
};
const REASON_OTHER_COUNTER_KEY = "shadow_reason_other_total";
const REASON_OTHER_BUCKET = "other";

// refresh-bi-r2.yml's dispatch_id/reason workflow_dispatch inputs are
// human-editable (an operator can run the workflow manually with any text
// in either field), so this endpoint must not trust either one to be safe
// to store or log verbatim.
//
// The only dispatch_id the automated pipeline ever sends is one this
// Coordinator itself issued via crypto.randomUUID() (handleEvaluate) —
// always a canonical RFC 4122 version-4 UUID. Restricting acceptance to
// exactly that shape means an operator typing arbitrary text (or PII) into
// the manual dispatch_id input can never have it stored in
// shadow_observed_dispatch_ids or reach a log line; it simply gets
// rejected as invalid_observation, same as any other malformed value.
const DISPATCH_ID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

// Bound on the recent-dispatch-id set (see defaultState() comment).
const MAX_OBSERVED_DISPATCH_IDS = 512;

// Statuses the semantic observer (GitHub Actions step, via
// yuge-finance shadow-observe-compare) may report per component. "skipped"
// covers a component that legitimately did not run this cycle (e.g. the
// Staff Ops export gate is closed) — like "no_baseline", it never counts
// toward a false negative.
const OBSERVATION_STATUSES = ["changed", "unchanged", "no_baseline", "skipped", "error"];

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

  // Hydrates whatever is actually in storage against the CURRENT default
  // shape. Production already has a Phase-1-shaped stored state (no
  // shadow_observation_*/shadow_reason_*/shadow_observed_dispatch_ids
  // fields at all) — returning it as-is would make the first Phase-2
  // observation do e.g. `undefined + 1` on a missing counter, producing
  // NaN. Spreading defaults first and the stored object second keeps every
  // already-stored value (Phase 1 or Phase 2) while filling in only the
  // fields a genuinely older stored blob never had. All state fields are
  // top-level primitives/arrays by design (see defaultState()) specifically
  // so this one-level spread is enough — a future nested field would need
  // its own explicit deep-hydration, not just this spread.
  async #load() {
    const stored = await this.state.storage.get(STATE_KEY);
    if (!stored) return defaultState();
    return { ...defaultState(), ...stored };
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
      if (request.method === "POST" && pathname === "/internal/observation") {
        return await this.handleObservation(await this.#readJson(request));
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
  // (always, regardless of mode), and the atomic reservation of a new
  // in-flight dispatch whenever one is actually needed to keep tracking
  // correct — which differs by mode:
  //   active: reserves ONLY when would_dispatch is true. When clean, the
  //     Worker skips calling GitHub entirely — this is the actual
  //     Actions-count savings.
  //   shadow: the Worker calls GitHub UNCONDITIONALLY every tick regardless
  //     of would_dispatch (that's the whole point of shadow mode — zero
  //     production behavior change while the planner is only observed).
  //     But that unconditional call still needs a target_seq snapshot and a
  //     dispatch_id so its completion callback can advance
  //     last_completed_seq/last_full_reconcile_at/last_successful_jst_date
  //     — otherwise shadow-mode observation never reflects real successful
  //     runs and can never show "clean". So shadow ALWAYS reserves too
  //     (unless something is already in-flight), just with `reason` falling
  //     back to "shadow_unconditional" when the planner itself found
  //     nothing warranting a dispatch. `would_dispatch`/`reason` in the
  //     response always reflect the planner's own independent judgement
  //     (what active mode would have done) for observability; the
  //     dispatch/reservation actually issued is reported separately via
  //     dispatch_id/target_seq/dispatch_reason.
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
        stale_reclaimed: staleReclaimed, dispatch_id: null, target_seq: null, dispatch_reason: null,
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
    // active only reserves when actually warranted; shadow always reserves
    // (it dispatches every tick regardless of the planner's own verdict).
    const shouldReserve = mode === MODE_ACTIVE ? wouldDispatch : true;

    if (!shouldReserve) {
      await this.#save(state); // persist any stale-lease reclaim even when not dispatching
      return jsonResponse({
        would_dispatch: wouldDispatch, reason, mode,
        event_seq: state.event_seq, last_completed_seq: state.last_completed_seq,
        stale_reclaimed: staleReclaimed, dispatch_id: null, target_seq: null, dispatch_reason: null,
      });
    }

    // Reserve atomically. dispatch_reason is what actually gets reported to
    // GitHub as the `reason` input — the real planner reason when there is
    // one (including in shadow mode, e.g. a webhook came in), or
    // "shadow_unconditional" when shadow is dispatching solely because it
    // always does, not because the planner found anything.
    const dispatchReason = reason || "shadow_unconditional";
    const dispatchId = crypto.randomUUID();
    state.in_flight_dispatch_id = dispatchId;
    state.in_flight_target_seq = state.event_seq;
    state.in_flight_started_at = nowIso;
    state.in_flight_reason = dispatchReason;
    state.last_dispatch_at = nowIso;
    if (reason === "manual_force") state.force_dispatch_requested = false;
    await this.#save(state);

    return jsonResponse({
      would_dispatch: wouldDispatch, reason, mode,
      event_seq: state.event_seq, last_completed_seq: state.last_completed_seq,
      stale_reclaimed: staleReclaimed,
      dispatch_id: dispatchId, target_seq: state.in_flight_target_seq, dispatch_reason: dispatchReason,
    });
  }

  // Called by worker.js right after a failed (non-204/network/timeout)
  // GitHub workflow_dispatch API call, so the reservation doesn't sit
  // blocking new dispatches until its lease times out. Only releases /
  // counts a failure when dispatch_id matches the CURRENT in-flight
  // reservation — a stale/duplicate report can never clobber a newer,
  // unrelated in-flight dispatch (see "wrong dispatch_id" test coverage).
  //
  // If the reservation being released was for a manual force
  // (in_flight_reason === "manual_force"), force_dispatch_requested is
  // re-armed: handleEvaluate() already consumed it at reservation time
  // (before the GitHub API call even happened), so without re-arming here
  // a dispatch-API-layer failure would silently and permanently drop an
  // operator's force request instead of retrying it on the next tick.
  async handleDispatchFailure(body) {
    const state = await this.#load();
    const dispatchId = body && body.dispatch_id;
    const matches = Boolean(dispatchId) && dispatchId === state.in_flight_dispatch_id;
    if (matches) {
      const wasManualForce = state.in_flight_reason === "manual_force";
      clearInFlight(state);
      state.consecutive_failures += 1;
      state.last_failure_at = (body && body.now_iso) || new Date().toISOString();
      if (wasManualForce) state.force_dispatch_requested = true;
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
    // A legitimate dispatch's target_seq is always an event_seq snapshot
    // taken at reservation time, so it can never legitimately exceed the
    // CURRENT event_seq (which only grows). A callback claiming otherwise
    // is either a bug or forged input — reject before mutating any state.
    // An old/stale target_seq (<= last_completed_seq already) remains
    // accepted, per the existing "old completion callback" guarantee.
    if (targetSeq > state.event_seq) {
      return jsonResponse({ error: "target_seq_exceeds_event_seq" }, 400);
    }
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

  // Shadow semantic false-negative observation (KIRAKU BI PHASE 2). Called
  // once per refresh-bi-r2.yml run (from its own "Report shadow observation"
  // step, alongside — not instead of — handleComplete's completion
  // callback). Body is already PII-free by construction on the caller's
  // side (GitHub Actions never has the actual snapshot content, only the
  // opaque status strings yuge-finance shadow-observe-compare produced) —
  // this handler additionally never persists anything but counters/
  // timestamps, so even a caller bug could not smuggle booking content into
  // Durable Object storage through this endpoint.
  //
  // Counting rules (PHASE 2 spec):
  //   - shadow_observations_total: every accepted observation report.
  //   - shadow_planner_skip_total: subset where reason==="shadow_unconditional"
  //     (the planner itself would have skipped this cycle in active mode).
  //   - shadow_observation_errors_total: subset where either component
  //     reported "error" (the comparison itself failed — never treated as a
  //     false negative, since we don't know what actually happened).
  //   - A "false negative" is counted ONLY when: reason==="shadow_unconditional"
  //     (planner would have skipped) AND at least one component reported
  //     "changed" AND neither component errored. "no_baseline"/"skipped"
  //     never count toward this — see PHASE 2 spec item 12.
  //   - Idempotency (fix round blocker 4): dispatch_id is required and acts
  //     as an idempotency key. A GitHub Actions rerun/retry of the same
  //     coordinator-tracked dispatch reports the same 15-minute cycle again
  //     — repeating that report must be a no-op for every counter (a
  //     duplicate response still returns 200, since the caller's report
  //     genuinely succeeded, it just didn't need to change anything).
  //   - PII-safe metadata contract (fix round blocker 7): dispatch_id must
  //     be a canonical UUID (see DISPATCH_ID_RE) and reason is only ever
  //     stored/returned/logged as its normalized `reason_bucket` — one of
  //     the known reason names or "other" — never the raw string. Both
  //     `dispatch_id` and `reason` are human-editable workflow_dispatch
  //     inputs; this is what stops an operator's manual-run typo (or
  //     malicious input) from smuggling arbitrary text into DO storage or
  //     a Cloudflare log line, on top of the caller-side PII-safety this
  //     endpoint already assumed for bi_status/staff_ops_status.
  async handleObservation(body) {
    const rawDispatchId = body && body.dispatch_id;
    const dispatchId = typeof rawDispatchId === "string" && DISPATCH_ID_RE.test(rawDispatchId)
      ? rawDispatchId : null;
    const reason = typeof (body && body.reason) === "string" && body.reason ? body.reason : null;
    const biStatus = OBSERVATION_STATUSES.includes(body && body.bi_status) ? body.bi_status : null;
    const staffOpsStatus = OBSERVATION_STATUSES.includes(body && body.staff_ops_status) ? body.staff_ops_status : null;
    if (!dispatchId || !reason || !biStatus || !staffOpsStatus) {
      // Never echo rawDispatchId/body.reason back — a malformed dispatch_id
      // (including one carrying arbitrary/PII-shaped text) or an unrecognized
      // field must not appear anywhere in the response.
      return jsonResponse({ error: "invalid_observation" }, 400);
    }
    const reasonBucket = Object.prototype.hasOwnProperty.call(REASON_COUNTER_KEYS, reason)
      ? reason : REASON_OTHER_BUCKET;

    const state = await this.#load();

    const alreadySeen = state.shadow_observed_dispatch_ids.includes(dispatchId);
    if (alreadySeen) {
      // No counters move for a duplicate — same cycle, already counted.
      return jsonResponse({ ok: true, duplicate: true, false_negative: false, reason_bucket: reasonBucket });
    }
    state.shadow_observed_dispatch_ids =
      [...state.shadow_observed_dispatch_ids, dispatchId].slice(-MAX_OBSERVED_DISPATCH_IDS);

    const nowIso = (body && body.observed_at) || new Date().toISOString();

    state.shadow_observations_total += 1;
    if (reasonBucket === REASON_OTHER_BUCKET) {
      // Unknown reason string: never stored/logged raw — only ever
      // bucketed as "other" so it stays visible in /status without
      // vanishing OR smuggling arbitrary text anywhere.
      state[REASON_OTHER_COUNTER_KEY] += 1;
    } else {
      state[REASON_COUNTER_KEYS[reasonBucket]] += 1;
    }
    const plannerWouldHaveSkipped = reason === "shadow_unconditional";
    if (plannerWouldHaveSkipped) state.shadow_planner_skip_total += 1;

    const hadError = biStatus === "error" || staffOpsStatus === "error";
    if (hadError) {
      state.shadow_observation_errors_total += 1;
      state.last_shadow_observation_error_at = nowIso;
    }

    let isFalseNegative = false;
    if (plannerWouldHaveSkipped && !hadError) {
      const biChanged = biStatus === "changed";
      const staffOpsChanged = staffOpsStatus === "changed";
      if (biChanged) state.shadow_skip_bi_changed_total += 1;
      if (staffOpsChanged) state.shadow_skip_staff_ops_changed_total += 1;
      if (biChanged || staffOpsChanged) {
        isFalseNegative = true;
        state.shadow_skip_any_changed_total += 1;
        state.last_shadow_false_negative_at = nowIso;
      }
    }

    state.last_shadow_observation_at = nowIso;
    await this.#save(state);
    return jsonResponse({ ok: true, duplicate: false, false_negative: isFalseNegative, reason_bucket: reasonBucket });
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
      shadow_observation: {
        total: state.shadow_observations_total,
        planner_skip_total: state.shadow_planner_skip_total,
        skip_bi_changed_total: state.shadow_skip_bi_changed_total,
        skip_staff_ops_changed_total: state.shadow_skip_staff_ops_changed_total,
        skip_any_changed_total: state.shadow_skip_any_changed_total,
        errors_total: state.shadow_observation_errors_total,
        by_reason: {
          shadow_unconditional: state.shadow_reason_shadow_unconditional_total,
          booking_webhook: state.shadow_reason_booking_webhook_total,
          full_reconciliation: state.shadow_reason_full_reconciliation_total,
          jst_date_rollover: state.shadow_reason_jst_date_rollover_total,
          manual_force: state.shadow_reason_manual_force_total,
          other: state.shadow_reason_other_total,
        },
        last_observation_at: state.last_shadow_observation_at,
        last_observation_error_at: state.last_shadow_observation_error_at,
        last_false_negative_at: state.last_shadow_false_negative_at,
      },
    });
  }
}

export { MODE_ACTIVE, MODE_SHADOW };
