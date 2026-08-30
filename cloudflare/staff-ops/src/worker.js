// 喜らく スタッフ Daily Ops Worker（表示 + 清掃上書きの1書き込みエンドポイントのみ）
// - HTML/JS/CSS は ASSETS binding（./public）から配信
// - 到着/出発/連泊/清掃データ(JSON)は R2 bucket kiraku-staff-ops-data の
//   latest/staff_ops_snapshot.json を読むだけ。生成は別プロセス(Pythonチーム)が
//   15分毎に担う。このWorkerはBeds24 APIを一切呼ばない。
// - このリポジトリの財務BI(cloudflare/bi-web)とは完全に別のWorker/データ源。
//   revenue/price/commission/ADR/RevPAR/payment/invoice等の財務系フィールドは
//   この契約に一切含まれない前提であり、このファイルもそれらを一切扱わない。
// - 認証: Cloudflare Access(Zero Trust)は不採用。スタッフ共通パスワード
//   (Worker Secret STAFF_OPS_PASSWORD)+ 署名済みsession cookie(HMAC-SHA256,
//   Worker Secret STAFF_OPS_SESSION_SECRET)による認証を、このWorker自身の
//   ミドルウェアとして全route(/health, /login, /login.js, /styles.css,
//   /api/auth/*を除く)に適用する。デフォルト拒否(default-deny) —
//   明示的に許可したpublicパス以外は、有効なsessionが無ければ
//   (/api/*は401 JSON、それ以外は/loginへ302)。secretが未設定の場合は
//   fail closed(全リクエスト拒否)。

import { buildOverrideKey, mergeCleaningOverrides } from "./cleaningOverrides.js";
import {
  SESSION_COOKIE_NAME,
  DEFAULT_SESSION_MAX_AGE_SECONDS,
  createSessionToken,
  verifySessionToken,
  passwordsMatch,
  parseCookieHeader,
  buildSessionCookieHeader,
  buildLogoutCookieHeader,
  isSameOriginRequest,
  checkLoginRateLimit,
  recordLoginFailure,
  clearLoginRateLimit,
} from "./auth.js";

const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
const ALLOWED_OVERRIDE_FIELDS = ["room_number", "notes"];
const MAX_OVERRIDE_VALUE_LEN = 200;
const MAX_PASSWORD_LEN = 500;
const PUBLIC_STATIC_PATHS = new Set(["/login", "/login.js", "/styles.css"]);

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

  // The caller has already passed the global auth middleware (valid session
  // required for any non-public path) by the time we get here. This is an
  // EXTRA, mutation-specific check: reject if a cross-origin Origin header
  // is present (defense-in-depth against CSRF; SameSite=Strict on the
  // session cookie already blocks true cross-site delivery in modern
  // browsers, but we check explicitly anyway).
  if (!isSameOriginRequest(request)) {
    return jsonResponse({ error: "origin_not_allowed" }, 403);
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
  // Shared staff password = no per-user identity to attribute this to.
  // "staff_shared_login" is a constant marker, not a real username.
  nextRecord.updated_by = "staff_shared_login";
  nextRecord.updated_at = new Date().toISOString();
  overridesObj[roomKey] = nextRecord;

  await env.CLEANING_OVERRIDES.put(kvKey, JSON.stringify(overridesObj));
  return jsonResponse({ ok: true });
}

// --- Authentication (shared staff password + signed session cookie) ---

function getClientIp(request) {
  return request.headers.get("CF-Connecting-IP") || "unknown";
}

async function isAuthenticated(request, env) {
  if (!env.STAFF_OPS_SESSION_SECRET) return false; // fail closed if not configured
  const token = parseCookieHeader(request.headers.get("Cookie"), SESSION_COOKIE_NAME);
  if (!token) return false;
  const authVersion = env.AUTH_VERSION || "1";
  const result = await verifySessionToken(env.STAFF_OPS_SESSION_SECRET, token, authVersion);
  return result.valid;
}

async function handleLogin(request, env) {
  if (!env.STAFF_OPS_PASSWORD || !env.STAFF_OPS_SESSION_SECRET) {
    return jsonResponse({ error: "auth_not_configured" }, 503);
  }

  let body;
  try {
    body = await request.json();
  } catch (e) {
    return jsonResponse({ error: "invalid_json" }, 400);
  }
  const password = typeof body?.password === "string" ? body.password : "";
  if (!password || password.length > MAX_PASSWORD_LEN) {
    return jsonResponse({ error: "invalid_password" }, 400);
  }

  const clientIp = getClientIp(request);
  const rate = await checkLoginRateLimit(env.CLEANING_OVERRIDES, clientIp);
  if (!rate.allowed) {
    return jsonResponse({ error: "rate_limited" }, 429);
  }

  // Never log the submitted or configured password value anywhere.
  const ok = await passwordsMatch(password, env.STAFF_OPS_PASSWORD);
  if (!ok) {
    await recordLoginFailure(env.CLEANING_OVERRIDES, clientIp, rate.currentCount);
    return jsonResponse({ error: "invalid_password" }, 401);
  }

  await clearLoginRateLimit(env.CLEANING_OVERRIDES, clientIp);
  const authVersion = env.AUTH_VERSION || "1";
  const token = await createSessionToken(env.STAFF_OPS_SESSION_SECRET, authVersion);
  const res = jsonResponse({ ok: true });
  res.headers.append("Set-Cookie", buildSessionCookieHeader(token, DEFAULT_SESSION_MAX_AGE_SECONDS));
  return res;
}

function handleLogout() {
  const res = jsonResponse({ ok: true });
  res.headers.append("Set-Cookie", buildLogoutCookieHeader());
  return res;
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const path = url.pathname;

    // /health : R2/KVを読まない、認証も不要（監視用）
    if (path === "/health") {
      return jsonResponse({ status: "ok" });
    }

    if (path === "/api/auth/login" && request.method === "POST") {
      return handleLogin(request, env);
    }
    if (path === "/api/auth/logout" && request.method === "POST") {
      return handleLogout();
    }

    // デフォルト拒否: 上記の明示的public pathを除き、有効なsessionが無ければ
    // ここで止める。/api/* はPIIを一切返さずJSON 401、それ以外(ページ本体)は
    // /login へ302リダイレクト — 「HTMLだけ認証してAPI URLを直接叩けば取得
    // 可能」を防ぐため、判定はASSETS/APIどちらのルートよりも先に行う。
    if (!PUBLIC_STATIC_PATHS.has(path)) {
      const authed = await isAuthenticated(request, env);
      if (!authed) {
        if (path.startsWith("/api/")) {
          return jsonResponse({ error: "unauthorized" }, 401);
        }
        const next = encodeURIComponent(path + url.search);
        return Response.redirect(new URL(`/login?next=${next}`, url), 302);
      }
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
    // ここに到達するのは PUBLIC_STATIC_PATHS か、認証済みリクエストのみ。
    return env.ASSETS.fetch(request);
  },
};
