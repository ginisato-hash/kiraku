// 喜らく スタッフ Daily Ops Worker（表示 + 清掃上書きの1書き込みエンドポイントのみ）
// - HTML/JS/CSS は ASSETS binding（./public）から配信
// - 到着/出発/連泊/清掃データ(JSON)は R2 bucket kiraku-staff-ops-data の
//   latest/staff_ops_snapshot.json を読むだけ。生成は別プロセス(Pythonチーム)が
//   15分毎に担う。このWorkerはBeds24 APIを一切呼ばない。
// - このリポジトリの財務BI(cloudflare/bi-web)とは完全に別のWorker/データ源。
//   revenue/price/commission/ADR/RevPAR/payment/invoice等の財務系フィールドは
//   この契約に一切含まれない前提であり、このファイルもそれらを一切扱わない。
// - 唯一の書き込みエンドポイント(POST /api/cleaning/override)は、Cloudflare
//   Accessがedgeで設定されている前提だが、それが将来誤設定された場合の
//   defense-in-depthとして、Cf-Access-Authenticated-User-Emailヘッダーが
//   無ければ常に403で拒否する（fail closed）。

import { buildOverrideKey, mergeCleaningOverrides } from "./cleaningOverrides.js";

const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
const ALLOWED_OVERRIDE_FIELDS = ["room_number", "notes"];
const MAX_OVERRIDE_VALUE_LEN = 200;

// 日付跨ぎ後もCDN/ブラウザに古いデータをキャッシュさせない（bi-webと同じ方針）。
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
async function getSnapshot(env) {
  const obj = await env.OPS_DATA.get("latest/staff_ops_snapshot.json");
  if (!obj) return null;
  try {
    const text = typeof obj.text === "function" ? await obj.text() : obj.body;
    return JSON.parse(text);
  } catch (e) {
    return null;
  }
}

function getDayData(snapshot, date) {
  if (!snapshot || typeof snapshot !== "object" || !snapshot.dates) return null;
  return snapshot.dates[date] || null;
}

async function readOverridesForDate(env, date) {
  if (!env.CLEANING_OVERRIDES) return {};
  let raw = null;
  try {
    raw = await env.CLEANING_OVERRIDES.get(`overrides:${date}`);
  } catch (e) {
    return {};
  }
  if (!raw) return {};
  try {
    const parsed = JSON.parse(raw);
    return (parsed && typeof parsed === "object") ? parsed : {};
  } catch (e) {
    return {};
  }
}

async function handleDailyOps(env, url) {
  const date = url.searchParams.get("date");
  if (!date || !DATE_RE.test(date)) {
    return jsonResponse({ error: "invalid_date" }, 400);
  }
  const snapshot = await getSnapshot(env);
  const dayData = getDayData(snapshot, date);
  if (!dayData) {
    return jsonResponse({ error: "not_found" }, 404);
  }
  return jsonResponse(dayData, 200);
}

async function handleCleaningGet(env, url) {
  const date = url.searchParams.get("date");
  if (!date || !DATE_RE.test(date)) {
    return jsonResponse({ error: "invalid_date" }, 400);
  }
  const snapshot = await getSnapshot(env);
  const dayData = getDayData(snapshot, date);
  if (!dayData || !dayData.cleaning) {
    return jsonResponse({ error: "not_found" }, 404);
  }
  const baseRooms = Array.isArray(dayData.cleaning.rooms) ? dayData.cleaning.rooms : [];
  const overridesObj = await readOverridesForDate(env, date);
  const rooms = mergeCleaningOverrides(baseRooms, overridesObj);
  return jsonResponse({ date, rooms }, 200);
}

function isValidOverrideValue(value) {
  if (value === null) return true;
  return typeof value === "string" && value.length <= MAX_OVERRIDE_VALUE_LEN;
}

async function handleCleaningOverridePost(request, env) {
  let body;
  try {
    body = await request.json();
  } catch (e) {
    return jsonResponse({ error: "invalid_json" }, 400);
  }
  const { date, room_type_key, checkout_booking_id, checkin_booking_id, field, value } = body || {};

  if (!date || !DATE_RE.test(date)) {
    return jsonResponse({ error: "invalid_date" }, 400);
  }
  if (!room_type_key || typeof room_type_key !== "string") {
    return jsonResponse({ error: "invalid_room_type_key" }, 400);
  }
  if (!ALLOWED_OVERRIDE_FIELDS.includes(field)) {
    return jsonResponse({ error: "invalid_field" }, 400);
  }
  if (!isValidOverrideValue(value)) {
    return jsonResponse({ error: "invalid_value" }, 400);
  }

  // Defense-in-depth: this write endpoint must fail closed even if
  // Cloudflare Access is ever misconfigured for this route. We only ever
  // read this header to stamp updated_by — never log or expose any other
  // request header.
  const userEmail = request.headers.get("Cf-Access-Authenticated-User-Email");
  if (!userEmail) {
    return jsonResponse({ error: "access_required" }, 403);
  }

  if (!env.CLEANING_OVERRIDES) {
    return jsonResponse({ error: "kv_unavailable" }, 500);
  }

  const kvKey = `overrides:${date}`;
  const overridesObj = await readOverridesForDate(env, date);
  const roomKey = buildOverrideKey({ room_type_key, checkout_booking_id, checkin_booking_id });

  const existing = (overridesObj[roomKey] && typeof overridesObj[roomKey] === "object")
    ? overridesObj[roomKey] : {};
  const nextRecord = { ...existing };
  if (value === null) {
    delete nextRecord[field];
  } else {
    nextRecord[field] = value;
  }
  nextRecord.updated_by = userEmail;
  nextRecord.updated_at = new Date().toISOString();
  overridesObj[roomKey] = nextRecord;

  await env.CLEANING_OVERRIDES.put(kvKey, JSON.stringify(overridesObj));
  return jsonResponse({ ok: true });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const path = url.pathname;

    // /health : R2/KVを読まない
    if (path === "/health") {
      return jsonResponse({ status: "ok" });
    }

    if (path === "/api/daily-ops" && request.method === "GET") {
      return handleDailyOps(env, url);
    }

    if (path === "/api/cleaning" && request.method === "GET") {
      return handleCleaningGet(env, url);
    }

    if (path === "/api/cleaning/override" && request.method === "POST") {
      return handleCleaningOverridePost(request, env);
    }

    // それ以外 : 静的アセット（index.html / print pages / mobile pages 等）
    return env.ASSETS.fetch(request);
  },
};
