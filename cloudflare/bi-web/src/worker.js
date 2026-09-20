// 喜らく 速報BI Worker（表示 + event-driven BI更新スケジューラ）
// - HTML/JS/CSS は ASSETS binding（./public）から配信
// - BIデータ JSON/CSV は R2 bucket kiraku-bi-data の latest/ から読むだけ
// - Beds24 API は呼ばない。Beds24 token/.env は参照しない。
// - scheduled(): 15分毎のCloudflare Cron Triggerから、BiRefreshCoordinator
//   Durable Object（./biRefreshCoordinator.js）にdispatch要否を問い合わせる。
//   mode=shadowでは従来通り毎回無条件でworkflow_dispatchを叩く（新plannerを
//   本番トラフィックで検証するだけで、Actions起動数はまだ変えない）。
//   mode=activeではCoordinatorがreserveしたdispatch_id/target_seqをinputsに
//   載せてworkflow_dispatchを叩き、要否が無ければ何もしない。
// - POST /internal/beds24/booking-webhook: Beds24 Booking WebhookをCoordinator
//   のevent_seq++にだけ変換する（副作用はそれだけ — R2 mutation/Beds24
//   write/直接dispatchは一切しない）。guest PII(氏名/電話/email/住所/comments)
//   は一切ログ・保存しない。
// - POST /internal/bi-refresh/complete: GitHub Actions completion callback。
// - GET /internal/bi-refresh/status: 認証付きPII-safe観測エンドポイント。
// - 実際のBeds24取得・BI生成・R2 publishは既存の refresh-bi-r2.yml
//   （refresh-beds24-bi → publish-bi-r2）がそのまま担う。ここでは何も
//   fetch/生成/publishしない。dispatch token/webhook secret/callback secretの
//   値は絶対にログしない。
import { BiRefreshCoordinator } from "./biRefreshCoordinator.js";
import { todayJst } from "./jstDate.js";
import { timingSafeEqual } from "./timingSafeEqual.js";
import {
  COORDINATOR_INSTANCE_NAME, MAX_WEBHOOK_BODY_BYTES, MODE_ACTIVE, MODE_SHADOW,
  envKiirakuPropertyId,
} from "./biRefreshConfig.js";

const R2_PREFIX = "latest/";
const CALLBACK_MAX_BODY_BYTES = 8192;

// GitHub Actions workflow_dispatch target — matches refresh-bi-r2.yml exactly.
// This Worker never touches Beds24/R2 publish logic itself; it only asks
// GitHub to run the existing workflow, on a schedule GitHub's own
// `on.schedule` cron cannot reliably guarantee (see fc1ffca commit message
// for the original diagnosis; this Cron Trigger replaces that unreliable
// path, it does not duplicate the workflow's own logic).
const GITHUB_OWNER = "ginisato-hash";
const GITHUB_REPO = "kiraku";
const GITHUB_WORKFLOW_FILE = "refresh-bi-r2.yml";
const GITHUB_DISPATCH_REF = "main";
const GITHUB_API_VERSION = "2022-11-28";

// Fires the existing refresh-bi-r2.yml workflow via workflow_dispatch. Success
// is HTTP 204 (GitHub returns no body for this endpoint). Never logs the
// token value — only the outcome (status code / ok / error text).
// `inputs` (dispatch_id/target_seq/reason, all strings — GitHub's
// workflow_dispatch API requires string input values) is optional and only
// added to the request body when provided, so the legacy no-arg call shape
// (shadow mode, unconditional dispatch) is byte-for-byte unchanged.
async function dispatchBiRefreshWorkflow(env, inputs) {
  const token = env.GITHUB_ACTIONS_DISPATCH_TOKEN;
  if (!token) {
    return { ok: false, status: null, error: "GITHUB_ACTIONS_DISPATCH_TOKEN secret is not set" };
  }
  const url = `https://api.github.com/repos/${GITHUB_OWNER}/${GITHUB_REPO}/actions/workflows/${GITHUB_WORKFLOW_FILE}/dispatches`;
  const body = inputs ? { ref: GITHUB_DISPATCH_REF, inputs } : { ref: GITHUB_DISPATCH_REF };
  try {
    const res = await fetch(url, {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${token}`,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": GITHUB_API_VERSION,
        "User-Agent": "kiraku-bi-worker-cron-dispatcher",
        "Content-Type": "application/json",
      },
      body: JSON.stringify(body),
    });
    if (res.status === 204) {
      return { ok: true, status: 204, error: null };
    }
    // Read the body for diagnostics on failure (GitHub's error JSON never
    // contains the token), but still never echo request headers.
    const bodyText = await res.text().catch(() => "");
    return { ok: false, status: res.status, error: bodyText.slice(0, 500) };
  } catch (e) {
    return { ok: false, status: null, error: e instanceof Error ? e.message : String(e) };
  }
}
const MONTH_RE = /^\d{4}-\d{2}$/;

// path -> { key: R2 object key (R2_PREFIX付与前), type: Content-Type }
const DATA_ROUTES = {
  "/api/manifest": { key: "manifest.json", type: "application/json; charset=utf-8" },
  "/api/snapshot": { key: "bi_snapshot.json", type: "application/json; charset=utf-8" },
  "/data/manifest.json": { key: "manifest.json", type: "application/json; charset=utf-8" },
  "/data/bi_snapshot.json": { key: "bi_snapshot.json", type: "application/json; charset=utf-8" },
  "/data/bi_daily_timeseries.csv": { key: "bi_daily_timeseries.csv", type: "text/csv; charset=utf-8" },
  "/data/bi_monthly_kpi.csv": { key: "bi_monthly_kpi.csv", type: "text/csv; charset=utf-8" },
  "/data/bi_validation_status.json": { key: "bi_validation_status.json", type: "application/json; charset=utf-8" },
  "/data/bi_exception_summary.json": { key: "bi_exception_summary.json", type: "application/json; charset=utf-8" },
};

// 月別 /data/months/{YYYY-MM}/{filename} のfilename -> Content-Type
const MONTH_FILE_TYPES = {
  "bi_snapshot.json": "application/json; charset=utf-8",
  "bi_daily_timeseries.csv": "text/csv; charset=utf-8",
  "bi_monthly_kpi.csv": "text/csv; charset=utf-8",
  "bi_validation_status.json": "application/json; charset=utf-8",
  "bi_exception_summary.json": "application/json; charset=utf-8",
};

// 日付跨ぎ後もCDN/ブラウザに古いBIデータをキャッシュさせない（重大不具合対応。Phase 7）。
const NO_STORE_HEADERS = {
  "cache-control": "no-store, no-cache, must-revalidate",
  "pragma": "no-cache",
  "expires": "0",
};

function jsonResponse(obj, status = 200) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { "content-type": "application/json; charset=utf-8", ...NO_STORE_HEADERS },
  });
}

// R2オブジェクトをJSONとして読む。実R2Object(.text())とテスト用の簡易mock({body: "..."})の両方に対応する。
async function getR2Json(env, key) {
  const obj = await env.BI_DATA.get(R2_PREFIX + key);
  if (!obj) return null;
  try {
    const text = typeof obj.text === "function" ? await obj.text() : obj.body;
    return JSON.parse(text);
  } catch (e) {
    return null;
  }
}

async function r2ObjectResponse(env, key, type) {
  const obj = await env.BI_DATA.get(R2_PREFIX + key);
  if (!obj) {
    return jsonResponse({ ok: false, error: "not found", key: R2_PREFIX + key }, 404);
  }
  return new Response(obj.body, {
    status: 200,
    headers: { "content-type": type, ...NO_STORE_HEADERS },
  });
}

async function handleApiMonths(env) {
  const manifest = await getR2Json(env, "manifest.json");
  if (!manifest) {
    return jsonResponse({ ok: false, error: "manifest not found" }, 404);
  }
  return jsonResponse({
    default_month: manifest.default_month ?? null,
    available_months: manifest.available_months || [],
    months_with_any_booking: manifest.months_with_any_booking || [],
    months_with_active_booking: manifest.months_with_active_booking || [],
    today_global_summary: manifest.today_global_summary ?? null,
  });
}

async function handleApiSnapshot(env, url) {
  const monthParam = url.searchParams.get("month");
  if (!monthParam) {
    // month指定なし: manifest.default_month があればそのmonth別snapshot、無ければ従来のlatest/bi_snapshot.json
    const manifest = await getR2Json(env, "manifest.json");
    if (manifest && manifest.default_month) {
      const key = `months/${manifest.default_month}/bi_snapshot.json`;
      const obj = await env.BI_DATA.get(R2_PREFIX + key);
      if (obj) {
        return new Response(obj.body, {
          status: 200,
          headers: { "content-type": "application/json; charset=utf-8", ...NO_STORE_HEADERS },
        });
      }
    }
    return r2ObjectResponse(env, "bi_snapshot.json", "application/json; charset=utf-8");
  }

  if (!MONTH_RE.test(monthParam)) {
    return jsonResponse({ ok: false, error: "invalid month format (expected YYYY-MM)" }, 400);
  }
  const manifest = await getR2Json(env, "manifest.json");
  const available = (manifest && manifest.available_months) || [];
  if (!available.includes(monthParam)) {
    return jsonResponse({ ok: false, error: "month not available", month: monthParam }, 404);
  }
  return r2ObjectResponse(env, `months/${monthParam}/bi_snapshot.json`, "application/json; charset=utf-8");
}

async function handleMonthDataFile(env, month, filename) {
  const type = MONTH_FILE_TYPES[filename];
  if (!type) {
    return jsonResponse({ ok: false, error: "not found" }, 404);
  }
  if (!MONTH_RE.test(month)) {
    return jsonResponse({ ok: false, error: "invalid month format (expected YYYY-MM)" }, 400);
  }
  return r2ObjectResponse(env, `months/${month}/${filename}`, type);
}

function coordinatorStub(env) {
  const id = env.BI_REFRESH_COORDINATOR.idFromName(COORDINATOR_INSTANCE_NAME);
  return env.BI_REFRESH_COORDINATOR.get(id);
}

async function coordinatorPost(env, path, body) {
  const stub = coordinatorStub(env);
  const res = await stub.fetch(new Request(`https://coordinator${path}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body || {}),
  }));
  return { status: res.status, body: await res.json() };
}

async function coordinatorGet(env, path) {
  const stub = coordinatorStub(env);
  const res = await stub.fetch(new Request(`https://coordinator${path}`));
  return { status: res.status, body: await res.json() };
}

// 「Beds24 V2 Booking Webhook」を受信し、Coordinatorのevent_seqを1つ進める
// だけの最小エンドポイント。実装方針（PR本文「Webhook security」参照）:
//   - 認証: X-Kiraku-Webhook-Token をBEDS24_WEBHOOK_SECRETと定数時間比較。
//     Beds24側のBooking Webhook設定画面(Settings > Properties > Access >
//     Booking webhooks)の「Custom Header」欄に同じ値を設定する
//     （Beds24は通知に署名しないため、この共有シークレットが唯一の認証手段）。
//   - Beds24 Webhook Version は「2 (without personal data)」を強く推奨
//     （運用手順書参照）。ただしこのハンドラ自体もPIIを一切ログ・保存しない
//     ため、仮にVersion 2 (with personal data)が設定されていても安全側。
//   - body全文・guest系フィールドは絶対にログしない。ログ可能なのは
//     property id / status / event_seq / accepted-or-rejected のみ。
//   - 副作用はCoordinatorのevent_seq incrementだけ。R2/Beds24 write/直接
//     GitHub dispatchはこのハンドラから一切行わない。
async function handleBeds24BookingWebhook(request, env) {
  if (request.method !== "POST") {
    return jsonResponse({ ok: false, error: "method_not_allowed" }, 405);
  }

  const configuredSecret = env.BEDS24_WEBHOOK_SECRET;
  if (!configuredSecret) {
    console.log("beds24_webhook_rejected reason=secret_not_configured");
    return jsonResponse({ ok: false, error: "webhook_not_configured" }, 500);
  }
  const suppliedToken = request.headers.get("X-Kiraku-Webhook-Token") || "";
  if (!timingSafeEqual(suppliedToken, configuredSecret)) {
    console.log("beds24_webhook_rejected reason=unauthorized");
    return jsonResponse({ ok: false, error: "unauthorized" }, 401);
  }

  const contentType = (request.headers.get("content-type") || "").toLowerCase();
  if (!contentType.startsWith("application/json")) {
    console.log("beds24_webhook_rejected reason=invalid_content_type");
    return jsonResponse({ ok: false, error: "invalid_content_type" }, 415);
  }

  const contentLength = Number(request.headers.get("content-length") || "0");
  if (contentLength > MAX_WEBHOOK_BODY_BYTES) {
    console.log("beds24_webhook_rejected reason=payload_too_large");
    return jsonResponse({ ok: false, error: "payload_too_large" }, 413);
  }

  let bodyText;
  try {
    bodyText = await request.text();
  } catch (e) {
    console.log("beds24_webhook_rejected reason=body_read_error");
    return jsonResponse({ ok: false, error: "body_read_error" }, 400);
  }
  if (bodyText.length > MAX_WEBHOOK_BODY_BYTES) {
    console.log("beds24_webhook_rejected reason=payload_too_large");
    return jsonResponse({ ok: false, error: "payload_too_large" }, 413);
  }

  let payload;
  try {
    payload = JSON.parse(bodyText);
  } catch (e) {
    console.log("beds24_webhook_rejected reason=malformed_json");
    return jsonResponse({ ok: false, error: "malformed_json" }, 400);
  }
  // bodyTextは以降どの変数にも保持しない（GC対象にする）— PIIを含み得る
  // 生JSONをこのハンドラのスコープ外へ持ち出さないため。
  bodyText = null;

  const booking = payload && typeof payload === "object" ? payload.booking : null;
  const rawPropertyId = booking && typeof booking === "object" ? booking.propertyId : undefined;
  const propertyId = rawPropertyId == null ? "" : String(rawPropertyId);
  const expectedPropertyId = envKiirakuPropertyId(env);
  if (propertyId !== expectedPropertyId) {
    console.log(`beds24_webhook_rejected reason=unsupported_property property_id=${propertyId || "(missing)"}`);
    return jsonResponse({ ok: false, error: "unsupported_property" }, 403);
  }

  // status/subStatusは短い列挙値であってPIIではない（allowlistされた文字列
  // のみをログする — 万一予期しない形の値でも、可観測性のためだけに使い、
  // レスポンスへは一切反映しない）。
  const status = typeof booking.status === "string" ? booking.status.slice(0, 40) : "(unknown)";

  const { body: eventResult } = await coordinatorPost(env, "/internal/event", {});
  console.log(`beds24_webhook_accepted property_id=${propertyId} status=${status} event_seq=${eventResult.event_seq}`);
  return jsonResponse({ ok: true });
}

// 権限分離（fix round blocker 7）: BI_REFRESH_CALLBACK_SECRETはGitHub
// Actionsのcompletion callback専用。status/mode/forceはBI_REFRESH_OPS_SECRET
// という別のCloudflare Worker Secretで認証する — GitHub Actions Secretsには
// この値を一切持たせない。GitHub Actionsのworkflowログ・repo設定が万一漏れても、
// mode切替やmanual forceといった運用操作までは行えないようにする境界。
function authenticateWithSecret(request, env, secretName) {
  const configuredSecret = env[secretName];
  if (!configuredSecret) return { ok: false, status: 500, error: `${secretName.toLowerCase()}_not_configured` };
  const authHeader = request.headers.get("Authorization") || "";
  const match = /^Bearer (.+)$/.exec(authHeader);
  const supplied = match ? match[1] : "";
  if (!timingSafeEqual(supplied, configuredSecret)) return { ok: false, status: 401, error: "unauthorized" };
  return { ok: true };
}

function authenticateCallbackRequest(request, env) {
  return authenticateWithSecret(request, env, "BI_REFRESH_CALLBACK_SECRET");
}

function authenticateOpsRequest(request, env) {
  return authenticateWithSecret(request, env, "BI_REFRESH_OPS_SECRET");
}

// GitHub Actions completion callback（refresh-bi-r2.ymlの最終step、
// `if: always()`で成功/失敗どちらでも送信される）。PIIはこの経路に一切
// 乗らない（GitHub Actions側はdispatch_id/target_seq/status/reasonしか
// 知らない）。認証は BI_REFRESH_CALLBACK_SECRET のみ（status/mode/force用の
// BI_REFRESH_OPS_SECRETとは別— GitHub Actionsにはops secretを渡さない）。
async function handleBiRefreshComplete(request, env) {
  if (request.method !== "POST") {
    return jsonResponse({ ok: false, error: "method_not_allowed" }, 405);
  }
  const auth = authenticateCallbackRequest(request, env);
  if (!auth.ok) return jsonResponse({ ok: false, error: auth.error }, auth.status);

  const contentLength = Number(request.headers.get("content-length") || "0");
  if (contentLength > CALLBACK_MAX_BODY_BYTES) {
    return jsonResponse({ ok: false, error: "payload_too_large" }, 413);
  }
  let bodyText;
  try {
    bodyText = await request.text();
  } catch (e) {
    return jsonResponse({ ok: false, error: "body_read_error" }, 400);
  }
  if (bodyText.length > CALLBACK_MAX_BODY_BYTES) {
    return jsonResponse({ ok: false, error: "payload_too_large" }, 413);
  }
  let payload;
  try {
    payload = JSON.parse(bodyText);
  } catch (e) {
    return jsonResponse({ ok: false, error: "malformed_json" }, 400);
  }

  const { status: doStatus, body: result } = await coordinatorPost(env, "/internal/complete", {
    dispatch_id: payload.dispatch_id,
    target_seq: payload.target_seq,
    status: payload.status,
    completed_at: payload.completed_at,
  });
  console.log(`bi_refresh_complete dispatch_id=${payload.dispatch_id} target_seq=${payload.target_seq} `
    + `status=${payload.status} reason=${payload.reason || "(none)"} accepted=${doStatus === 200}`);
  if (doStatus !== 200) return jsonResponse({ ok: false, error: result.error || "invalid_payload" }, 400);
  return jsonResponse({ ok: true, last_completed_seq: result.last_completed_seq });
}

// PII-safeな内部observability endpoint。認証必須。
async function handleBiRefreshStatus(request, env) {
  if (request.method !== "GET") {
    return jsonResponse({ ok: false, error: "method_not_allowed" }, 405);
  }
  const auth = authenticateOpsRequest(request, env);
  if (!auth.ok) return jsonResponse({ ok: false, error: auth.error }, auth.status);

  const { body: s } = await coordinatorGet(env, "/internal/status");
  // 明示的に許可フィールドだけを再構築する（DOの返り値を無条件にspreadしない
  // — 将来DO側のstatusにフィールドが増えても、ここで明示追加しない限り
  // 露出しない設計にする）。
  return jsonResponse({
    mode: s.mode,
    event_seq: s.event_seq,
    last_completed_seq: s.last_completed_seq,
    dirty_count: s.dirty_count,
    in_flight: s.in_flight,
    force_dispatch_requested: s.force_dispatch_requested,
    last_webhook_at: s.last_webhook_at,
    last_dispatch_at: s.last_dispatch_at,
    last_success_at: s.last_success_at,
    last_full_reconcile_at: s.last_full_reconcile_at,
    consecutive_failures: s.consecutive_failures,
    next_reconcile_due_at: s.next_reconcile_due_at,
    shadow_observation: s.shadow_observation ? {
      total: s.shadow_observation.total,
      planner_skip_total: s.shadow_observation.planner_skip_total,
      skip_bi_changed_total: s.shadow_observation.skip_bi_changed_total,
      skip_staff_ops_changed_total: s.shadow_observation.skip_staff_ops_changed_total,
      skip_any_changed_total: s.shadow_observation.skip_any_changed_total,
      errors_total: s.shadow_observation.errors_total,
      by_reason: s.shadow_observation.by_reason ? {
        shadow_unconditional: s.shadow_observation.by_reason.shadow_unconditional,
        booking_webhook: s.shadow_observation.by_reason.booking_webhook,
        full_reconciliation: s.shadow_observation.by_reason.full_reconciliation,
        jst_date_rollover: s.shadow_observation.by_reason.jst_date_rollover,
        manual_force: s.shadow_observation.by_reason.manual_force,
        other: s.shadow_observation.by_reason.other,
      } : null,
      last_observation_at: s.shadow_observation.last_observation_at,
      last_observation_error_at: s.shadow_observation.last_observation_error_at,
      last_false_negative_at: s.shadow_observation.last_false_negative_at,
    } : null,
  });
}

// GitHub Actions completion callbackと対になる、shadow false-negative観測用の
// 別endpoint（PHASE 2）。責務を分離するため /internal/bi-refresh/complete には
// 混ぜない — completeはdispatch/target_seqのreservation解決、こちらは
// PII-safeな集計カウンタの加算だけを行う。認証はcompleteと同じ
// BI_REFRESH_CALLBACK_SECRET（GitHub Actions→Coordinatorのmachine callbackで
// あり、BI_REFRESH_OPS_SECRETはGitHub Actionsに一切渡さない方針は変えない）。
// bodyにはBI/Staff Opsのopaqueなstatus文字列（changed/unchanged/no_baseline/
// skipped/error）以外は乗らない設計（yuge-finance shadow-observe-compare側の
// 契約）。念のためこの層でも許可された文字列以外はコード側でrejectされる
// （biRefreshCoordinator.js の OBSERVATION_STATUSES）。
async function handleBiRefreshShadowObservation(request, env) {
  if (request.method !== "POST") {
    return jsonResponse({ ok: false, error: "method_not_allowed" }, 405);
  }
  const auth = authenticateCallbackRequest(request, env);
  if (!auth.ok) return jsonResponse({ ok: false, error: auth.error }, auth.status);

  const contentLength = Number(request.headers.get("content-length") || "0");
  if (contentLength > CALLBACK_MAX_BODY_BYTES) {
    return jsonResponse({ ok: false, error: "payload_too_large" }, 413);
  }
  let bodyText;
  try {
    bodyText = await request.text();
  } catch (e) {
    return jsonResponse({ ok: false, error: "body_read_error" }, 400);
  }
  if (bodyText.length > CALLBACK_MAX_BODY_BYTES) {
    return jsonResponse({ ok: false, error: "payload_too_large" }, 413);
  }
  let payload;
  try {
    payload = JSON.parse(bodyText);
  } catch (e) {
    return jsonResponse({ ok: false, error: "malformed_json" }, 400);
  }

  const { status: doStatus, body: result } = await coordinatorPost(env, "/internal/observation", {
    dispatch_id: payload.dispatch_id,
    reason: payload.reason,
    bi_status: payload.bi_status,
    staff_ops_status: payload.staff_ops_status,
    observed_at: payload.observed_at,
  });
  // dispatch_id/reason are human-editable refresh-bi-r2.yml workflow_dispatch
  // inputs (an operator can run the workflow manually with arbitrary text in
  // either field), so the RAW request values are never logged here — only
  // once the Coordinator has validated dispatch_id as a canonical UUID
  // (fix round blocker 7; a rejection logs a fixed "(rejected)" placeholder
  // instead) and normalized reason into result.reason_bucket (one of the
  // known reason names or "other" — never the raw string). bi_status/
  // staff_ops_status are not operator-editable workflow_dispatch inputs
  // (only ever the fixed enum shadow-observe-compare's own CLI output can
  // produce), so those remain safe to log verbatim as before.
  const loggedDispatchId = doStatus === 200 ? payload.dispatch_id : "(rejected)";
  const reasonBucket = (result && result.reason_bucket) || "(rejected)";
  console.log(`bi_shadow_observation dispatch_id=${loggedDispatchId} reason_bucket=${reasonBucket} `
    + `bi_status=${payload.bi_status} staff_ops_status=${payload.staff_ops_status} `
    + `accepted=${doStatus === 200} duplicate=${result && result.duplicate} `
    + `false_negative=${result && result.false_negative}`);
  if (doStatus !== 200) return jsonResponse({ ok: false, error: result.error || "invalid_payload" }, 400);
  return jsonResponse({
    ok: true, duplicate: result.duplicate, false_negative: result.false_negative, reason_bucket: result.reason_bucket,
  });
}

// 運用者用: mode切替（shadow/active）・手動force。どちらも再deploy不要で
// DO storageへ即時反映される。BI_REFRESH_OPS_SECRETで認証
// （BI_REFRESH_CALLBACK_SECRETとは別のCloudflare Worker Secret。GitHub
// Actionsにはこの値を一切渡さない — fix round blocker 7）。
async function handleBiRefreshMode(request, env) {
  if (request.method !== "POST") {
    return jsonResponse({ ok: false, error: "method_not_allowed" }, 405);
  }
  const auth = authenticateOpsRequest(request, env);
  if (!auth.ok) return jsonResponse({ ok: false, error: auth.error }, auth.status);
  let payload;
  try {
    payload = JSON.parse(await request.text());
  } catch (e) {
    return jsonResponse({ ok: false, error: "malformed_json" }, 400);
  }
  const { status, body } = await coordinatorPost(env, "/internal/mode", { mode: payload.mode });
  if (status !== 200) return jsonResponse({ ok: false, error: body.error }, 400);
  console.log(`bi_refresh_mode_changed mode=${body.mode}`);
  return jsonResponse({ ok: true, mode: body.mode });
}

async function handleBiRefreshForce(request, env) {
  if (request.method !== "POST") {
    return jsonResponse({ ok: false, error: "method_not_allowed" }, 405);
  }
  const auth = authenticateOpsRequest(request, env);
  if (!auth.ok) return jsonResponse({ ok: false, error: auth.error }, auth.status);
  await coordinatorPost(env, "/internal/force", {});
  console.log("bi_refresh_force_requested");
  return jsonResponse({ ok: true });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const path = url.pathname;

    // /health : R2を読まない
    if (path === "/health") {
      return jsonResponse({
        ok: true,
        service: "kiraku-bi",
        data_source: "r2",
        r2_binding: "BI_DATA",
      });
    }

    if (path === "/api/months") {
      return handleApiMonths(env);
    }

    if (path === "/api/snapshot") {
      return handleApiSnapshot(env, url);
    }

    if (path === "/internal/beds24/booking-webhook") {
      return handleBeds24BookingWebhook(request, env);
    }
    if (path === "/internal/bi-refresh/complete") {
      return handleBiRefreshComplete(request, env);
    }
    if (path === "/internal/bi-refresh/shadow-observation") {
      return handleBiRefreshShadowObservation(request, env);
    }
    if (path === "/internal/bi-refresh/status") {
      return handleBiRefreshStatus(request, env);
    }
    if (path === "/internal/bi-refresh/mode") {
      return handleBiRefreshMode(request, env);
    }
    if (path === "/internal/bi-refresh/force") {
      return handleBiRefreshForce(request, env);
    }

    const monthFileMatch = path.match(/^\/data\/months\/([^/]+)\/([^/]+)$/);
    if (monthFileMatch) {
      const [, month, filename] = monthFileMatch;
      return handleMonthDataFile(env, decodeURIComponent(month), filename);
    }

    // BIデータ系ルート : R2(BI_DATA)から返す
    const route = DATA_ROUTES[path];
    if (route) {
      try {
        const obj = await env.BI_DATA.get(R2_PREFIX + route.key);
        if (!obj) {
          return jsonResponse(
            { ok: false, error: "not found", key: R2_PREFIX + route.key }, 404);
        }
        return new Response(obj.body, {
          status: 200,
          headers: { "content-type": route.type, ...NO_STORE_HEADERS },
        });
      } catch (e) {
        return jsonResponse({ ok: false, error: String(e) }, 500);
      }
    }

    // それ以外 : 静的アセット（index.html / app.js 等）
    return env.ASSETS.fetch(request);
  },

  // Cloudflare Cron Trigger (see wrangler.toml [triggers] crons) — fires every
  // 15 minutes. Asks BiRefreshCoordinator whether a dispatch is warranted;
  // never fetches Beds24, never touches R2, never generates BI data itself
  // — that all stays in refresh-bi-r2.yml.
  // ctx.waitUntil keeps everything alive past the handler's return
  // (Cloudflare Workers may otherwise cancel in-flight fetches once
  // scheduled() returns).
  async scheduled(event, env, ctx) {
    ctx.waitUntil(runScheduledEvaluation(event, env));
  },
};

// Coordinator response contract validation (fix round: strict contract).
// The previous fail-open check only confirmed `status===200 && typeof
// body.mode === "string"` — that is NOT enough. A response like
// `{"mode":"active"}` (missing would_dispatch entirely) passed that check,
// then `!decision.would_dispatch` evaluated true (undefined is falsy),
// silently taking the "active mode, nothing to do" early-return path and
// stopping BI refresh entirely on a Coordinator bug/partial-response —
// exactly the kind of failure fail-open exists to prevent. This validates
// the full shape BEFORE any dispatch decision is made from it; any
// violation throws, which the caller's try/catch turns into the same
// legacy unconditional dispatch fallback as a hard Coordinator error.
function validateCoordinatorDecision(body) {
  if (!body || typeof body !== "object") {
    throw new Error("coordinator response is not an object");
  }
  const { mode, would_dispatch: wouldDispatch, event_seq: eventSeq, last_completed_seq: lastCompletedSeq } = body;
  if (mode !== MODE_SHADOW && mode !== MODE_ACTIVE) {
    throw new Error(`coordinator response has an unsupported mode: ${mode}`);
  }
  if (typeof wouldDispatch !== "boolean") {
    throw new Error("coordinator response would_dispatch is not a boolean");
  }
  if (!Number.isInteger(eventSeq) || eventSeq < 0) {
    throw new Error("coordinator response event_seq is not a non-negative integer");
  }
  if (!Number.isInteger(lastCompletedSeq) || lastCompletedSeq < 0) {
    throw new Error("coordinator response last_completed_seq is not a non-negative integer");
  }

  // dispatch_id present == the Coordinator reserved a tracked dispatch for
  // us (shadow always does this unless already in-flight; active does it
  // only when would_dispatch is true). Whenever it's present, its whole
  // tracking triple must be well-formed.
  const hasTrackedDispatch = body.dispatch_id != null;
  if (hasTrackedDispatch) {
    if (typeof body.dispatch_id !== "string" || !body.dispatch_id) {
      throw new Error("coordinator response dispatch_id is present but not a non-empty string");
    }
    if (!Number.isInteger(body.target_seq) || body.target_seq < 0) {
      throw new Error("coordinator response target_seq is not a non-negative integer");
    }
    const dispatchReason = body.dispatch_reason || body.reason;
    if (typeof dispatchReason !== "string" || !dispatchReason) {
      throw new Error("coordinator response has a tracked dispatch but no usable reason string");
    }
  }

  // The exact bug this validation exists to catch: active mode saying
  // "dispatch" without having actually reserved anything to dispatch with.
  if (mode === MODE_ACTIVE && wouldDispatch && !hasTrackedDispatch) {
    throw new Error("coordinator response: active mode would_dispatch=true but no dispatch_id was reserved");
  }

  return body;
}

// shadow: 従来通り15分ごとに必ずGitHub Actionsをdispatchする — ここは
// 一切変えない（zero production behavior change）。ただしCoordinatorは
// shadowでも常にreserveする（handleEvaluate参照）ので、dispatch_id/
// target_seq/reasonをinputsとして渡し、completion callbackで
// last_completed_seq/last_full_reconcile_at/last_successful_jst_dateを
// 正しく前進させられるようにする。これが無いと、shadow実行が実際には
// 成功していてもCoordinatorの状態には一切反映されず、shadow planner
// observationの「今はcleanなはず」という判定が永久にstaleなままになる
// （fix round blocker 1）。
// active: Coordinatorがreserveした場合のみdispatchする（Actions起動数の
// 実削減はここ）。
// どちらのモードでも、GitHub API呼び出し自体が失敗した場合は
// reservationをCoordinatorへ返却し（次回Cronで再試行可能にする）、
// 成功扱いにしない。
//
// fail-open（blocker 4）: Coordinator自体が例外を投げる・不正なレスポンス
// を返す等、評価そのものが失敗した場合は、modeに関わらず従来通りの
// 無条件dispatchへ必ずfallbackする。「取りこぼし禁止 >
// 重複実行回避」の原則どおり、Coordinator障害でBI更新が止まる方が
// Actions実行数が多少増えるより重大なため。
async function runScheduledEvaluation(event, env) {
  const scheduledTime = new Date(event.scheduledTime);
  const nowIso = scheduledTime.toISOString();
  const todayJstStr = todayJst(scheduledTime);

  let decision;
  try {
    const { status, body } = await coordinatorPost(env, "/internal/evaluate", {
      now_iso: nowIso, today_jst: todayJstStr,
    });
    if (status !== 200) {
      throw new Error(`unexpected coordinator response status=${status}`);
    }
    decision = validateCoordinatorDecision(body);
  } catch (e) {
    console.log(`bi_coordinator_error_fallback_dispatch cron=${event.cron} `
      + `error=${e instanceof Error ? e.message : String(e)}`);
    const result = await dispatchBiRefreshWorkflow(env);
    if (result.ok) {
      console.log(`bi_dispatch_ok cron=${event.cron} scheduled_time=${nowIso} mode=fallback`);
    } else {
      console.log(`bi_dispatch_failed cron=${event.cron} status=${result.status} error=${result.error} mode=fallback`);
    }
    return;
  }

  const isShadow = decision.mode !== MODE_ACTIVE;

  if (isShadow) {
    console.log(`bi_shadow_evaluate cron=${event.cron} would_dispatch=${decision.would_dispatch} `
      + `reason=${decision.reason} event_seq=${decision.event_seq} last_completed_seq=${decision.last_completed_seq}`);
  } else if (!decision.would_dispatch) {
    console.log(`bi_active_skip cron=${event.cron} reason=${decision.reason || "clean"} `
      + `event_seq=${decision.event_seq} last_completed_seq=${decision.last_completed_seq}`);
    return;
  }

  // Reached only when: shadow (always dispatches), or active with
  // would_dispatch===true. Either way, the Coordinator has already
  // reserved a dispatch_id/target_seq for us (handleEvaluate's
  // shouldReserve covers both cases) unless something else raced in
  // between — guard defensively rather than assume.
  const inputs = decision.dispatch_id ? {
    dispatch_id: decision.dispatch_id,
    target_seq: String(decision.target_seq),
    reason: decision.dispatch_reason || decision.reason || "shadow_unconditional",
  } : undefined;

  const modeLabel = isShadow ? "shadow" : "active";
  const result = await dispatchBiRefreshWorkflow(env, inputs);
  if (result.ok) {
    console.log(`bi_dispatch_ok cron=${event.cron} mode=${modeLabel} dispatch_id=${decision.dispatch_id || "(none)"} `
      + `target_seq=${decision.target_seq ?? "(none)"} reason=${inputs ? inputs.reason : "(none)"}`);
    return;
  }
  console.log(`bi_dispatch_failed cron=${event.cron} mode=${modeLabel} dispatch_id=${decision.dispatch_id || "(none)"} `
    + `status=${result.status} error=${result.error}`);
  if (decision.dispatch_id) {
    // GitHub側のdispatch自体が失敗したので、reservationしたin-flightを
    // 成功扱いにしない — Coordinatorへ返却して次回Cronで再試行可能にする
    // （manual forceだった場合はCoordinator側でre-armされる）。
    try {
      await coordinatorPost(env, "/internal/dispatch-failure", { dispatch_id: decision.dispatch_id });
    } catch (e) {
      console.log(`bi_dispatch_failure_report_error cron=${event.cron} error=${e instanceof Error ? e.message : String(e)}`);
    }
  }
}

export { dispatchBiRefreshWorkflow, BiRefreshCoordinator };
