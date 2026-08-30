// 喜らく スタッフ Daily Ops Worker（表示 + 清掃指示の上書きAPIのみ）
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
//
// 清掃指示のoverrideキー体系(NEW): `${date}:${roomNumber}` (詳細はsrc/cleaningOverrides.js)。
// room_numberは常にKIRAKU_ROOM_ORDER(src/roomMaster.js)の18室いずれかで、この
// Worker自身がoverride検証にも使う。ブラウザ側の静的資産(public/配下)は
// src/roomMaster.jsへ直接importできない(ASSETS bindingはpublic/配下のみを配信する
// ため)ので、認証済みリクエストに対してこのファイルの内容から `/src/roomMaster.js` を
// 動的に生成して配信する — 実体はsrc/roomMaster.js 1箇所のみで二重管理にはならない。

import { KIRAKU_ROOM_ORDER } from "./roomMaster.js";
import { buildOverrideKey, mergeCleaningOverrides, readOverridesForDate } from "./cleaningOverrides.js";
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
const MAX_INSTRUCTION_LEN = 200;
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
  const overridesByRoom = await readOverridesForDate(env.CLEANING_OVERRIDES, date);
  const rooms = mergeCleaningOverrides(baseRooms, overridesByRoom);
  return jsonResponse({ date, rooms }, 200);
}

// POST/DELETE の両方が共有する date/roomNumber検証 + 許可body-key検証。
// allowedKeys: POSTは {date, roomNumber, instruction}、DELETEは {date, roomNumber} のみ許可。
function validateDateAndRoomBody(body, allowedKeys) {
  if (!body || typeof body !== "object" || Array.isArray(body)) {
    return { error: "invalid_payload" };
  }
  for (const key of Object.keys(body)) {
    if (!allowedKeys.has(key)) return { error: "invalid_payload" };
  }
  const { date, roomNumber } = body;
  if (!date || !DATE_RE.test(date)) return { error: "invalid_date" };
  if (typeof roomNumber !== "string" || !KIRAKU_ROOM_ORDER.includes(roomNumber)) {
    return { error: "invalid_room" };
  }
  return { ok: true, date, roomNumber };
}

async function handleCleaningOverridePost(request, env) {
  let body;
  try {
    body = await request.json();
  } catch (e) {
    return jsonResponse({ error: "invalid_json" }, 400);
  }

  const validation = validateDateAndRoomBody(body, new Set(["date", "roomNumber", "instruction"]));
  if (!validation.ok) return jsonResponse({ error: validation.error }, 400);
  const { date, roomNumber } = validation;

  const instruction = body.instruction;
  if (typeof instruction !== "string") {
    return jsonResponse({ error: "invalid_instruction" }, 400);
  }
  const trimmed = instruction.trim();
  // 空文字を永久overrideとして保存する曖昧な実装は禁止 — 空欄は明示的に拒否し、
  // 上書きを消す操作はDELETEでのみ行う。
  if (!trimmed || trimmed.length > MAX_INSTRUCTION_LEN) {
    return jsonResponse({ error: "invalid_instruction" }, 400);
  }

  // 既にグローバル認証ミドルウェアを通過済み(有効なsessionが必須)。ここは
  // mutation専用の追加チェック: クロスオリジンのOriginヘッダを拒否する
  // (CSRF対策の多層防御。SameSite=Strictでも既に大半を防げているが明示的に確認)。
  if (!isSameOriginRequest(request)) {
    return jsonResponse({ error: "origin_not_allowed" }, 403);
  }

  if (!env.CLEANING_OVERRIDES) {
    return jsonResponse({ error: "kv_unavailable" }, 500);
  }

  await env.CLEANING_OVERRIDES.put(
    buildOverrideKey(date, roomNumber),
    JSON.stringify({ instruction: trimmed, updatedAt: new Date().toISOString() }),
  );
  return jsonResponse({ ok: true });
}

async function handleCleaningOverrideDelete(request, env) {
  let body;
  try {
    body = await request.json();
  } catch (e) {
    return jsonResponse({ error: "invalid_json" }, 400);
  }

  const validation = validateDateAndRoomBody(body, new Set(["date", "roomNumber"]));
  if (!validation.ok) return jsonResponse({ error: validation.error }, 400);
  const { date, roomNumber } = validation;

  if (!isSameOriginRequest(request)) {
    return jsonResponse({ error: "origin_not_allowed" }, 403);
  }

  if (!env.CLEANING_OVERRIDES) {
    return jsonResponse({ error: "kv_unavailable" }, 500);
  }

  // 存在しないキーの削除も含めて常に成功(冪等) — クリック連打やダブルリクエストで
  // エラーにしないため。
  await env.CLEANING_OVERRIDES.delete(buildOverrideKey(date, roomNumber));
  return jsonResponse({ ok: true });
}

// public/配下の静的資産からsrc/roomMaster.jsへ直接importできないため、この
// Workerが唯一の実体(src/roomMaster.js)からJSモジュールを動的に生成して配信する。
// public/cleaningSheetTemplate.jsの `import ... from "../src/roomMaster.js"` は、
// ブラウザ上ではそのファイルのURL("/cleaningSheetTemplate.js")からの相対URL解決に
// より "/src/roomMaster.js" として要求される — この関数はまさにそのパスへ応答する。
// 認証済みリクエストのみ到達する(PUBLIC_STATIC_PATHSに含めていないため、下の
// デフォルト拒否ブロックを必ず通過する)。
function handleRoomMasterJs() {
  const body = `// Auto-served by worker.js from src/roomMaster.js (single source of truth).\n`
    + `export const KIRAKU_ROOM_ORDER = ${JSON.stringify(KIRAKU_ROOM_ORDER)};\n`;
  return new Response(body, {
    status: 200,
    headers: { "content-type": "application/javascript; charset=utf-8", ...NO_STORE_HEADERS },
  });
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

    if (path === "/api/cleaning/override" && request.method === "DELETE") {
      return handleCleaningOverrideDelete(request, env);
    }

    if (path === "/src/roomMaster.js" && request.method === "GET") {
      return handleRoomMasterJs();
    }

    // それ以外 : 静的アセット（index.html / print pages / mobile pages 等）
    // ここに到達するのは PUBLIC_STATIC_PATHS か、認証済みリクエストのみ。
    return env.ASSETS.fetch(request);
  },
};
