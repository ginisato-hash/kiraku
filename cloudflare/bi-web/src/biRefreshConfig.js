// biRefreshConfig.js — single source of truth for the event-driven BI refresh
// migration's tunable constants. Referenced by biRefreshCoordinator.js,
// worker.js and their tests so no magic number is duplicated/scattered.
//
// Values that are safe to change without a code review (thresholds/timeouts)
// can be overridden per-environment via wrangler.toml [vars] — see the
// `envOr*` helpers below, which read env first and fall back to these
// defaults. Nothing here is a secret.

// 喜らく単体のBeds24 property id。Webhookのpayload.booking.propertyIdが
// これと一致しない場合はrejectする（他物件のWebhookを誤って受理しない）。
export const DEFAULT_KIRAKU_PROPERTY_ID = "330695";

// 定期full reconciliationの最大許容間隔（秒）。この間隔を超えて成功実行が
// 無ければ、event_seqが変化していなくてもdispatchする。
export const DEFAULT_FULL_RECONCILE_MAX_AGE_SECONDS = 6 * 60 * 60; // 6時間

// in-flight dispatchのlease timeout（秒）。実測p95 146秒・最大176秒
// （2026-09-20時点、直近100回）に対し十分な余裕を持たせた値。この時間を
// 超えてcompletion callbackが来ないin-flightはstale（callback欠落/runner
// crash等）とみなし、次回shouldDispatch()判定時に回収して再dispatch可能にする。
export const DEFAULT_LEASE_TIMEOUT_SECONDS = 25 * 60; // 25分

// Beds24 Webhook request bodyの上限バイト数。実際のbooking webhookペイロード
// (booking本体+invoiceItems+messages)はこれより十分小さいはずで、上限超過は
// 異常系（誤送信/攻撃）として拒否する。
export const MAX_WEBHOOK_BODY_BYTES = 262144; // 256KiB

// Coordinator Durable Objectは単一シングルトンとして扱う（BI更新は喜らく単体
// につき1系統のみのため、property/月ごとに分ける必要が無い）。
export const COORDINATOR_INSTANCE_NAME = "singleton";

export const MODE_SHADOW = "shadow";
export const MODE_ACTIVE = "active";
export const VALID_MODES = [MODE_SHADOW, MODE_ACTIVE];

export function envKiirakuPropertyId(env) {
  return (env && env.KIRAKU_PROPERTY_ID) || DEFAULT_KIRAKU_PROPERTY_ID;
}

export function envFullReconcileMaxAgeSeconds(env) {
  const v = env && env.FULL_RECONCILE_MAX_AGE_SECONDS;
  const n = v == null ? NaN : Number(v);
  return Number.isFinite(n) && n > 0 ? n : DEFAULT_FULL_RECONCILE_MAX_AGE_SECONDS;
}

export function envLeaseTimeoutSeconds(env) {
  const v = env && env.LEASE_TIMEOUT_SECONDS;
  const n = v == null ? NaN : Number(v);
  return Number.isFinite(n) && n > 0 ? n : DEFAULT_LEASE_TIMEOUT_SECONDS;
}

export function envDefaultMode(env) {
  const v = env && env.BI_REFRESH_DEFAULT_MODE;
  return VALID_MODES.includes(v) ? v : MODE_SHADOW;
}
