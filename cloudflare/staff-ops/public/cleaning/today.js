// today.js — bootstrap for the mobile cleaning view (/cleaning/today), FINAL design.
// Read-only display for cleaning staff on a phone: today (JST) by default,
// accepts ?date=. No start/complete/inspected/assign status buttons in this
// version (explicitly out of scope — editing lives only in the Staff Ops
// desktop "Staff cleaning list", see cleaningStaffView.js). Uses the SAME
// /api/cleaning?date=... endpoint and merged room data as the print page and
// the Staff Ops cleaning list — see test/sameEndpoint.test.mjs, which
// asserts all pages fetch the identical URL with no divergent filtering.
// The actual block-rendering logic lives in cleaningSheetTemplate.js
// (renderMobileCleaningBody) so it stays pure/DOM-free and unit-testable.
//
// 2026-09追加: 在室確認(room_access_status)のリアルタイム反映。フロントが
// 「清掃可」に変更した数秒後に、このページ(閲覧専用)へ自動反映する。
// このファイル自体は一切mutationを行わない(POST/DELETE無し・button無し・
// click listener無し — test/mobileCleaning.test.mjsが確認する)。WebSocket
// はサーバからのpush専用で、ここからのsend()も行わない。
import { renderMobileCleaningBody, renderMobileRoomBlock } from "../cleaningSheetTemplate.js";
import { assertNoFinancialKeys, assertNoForbiddenCleaningKeys } from "../financialGuard.js";
import { todayJst, formatDateJp } from "../jst.js";
import { cleaningVisualAllowed, cleaningLiveAccessAllowed } from "../featureFlags.js";

const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
const RECONNECT_DELAYS_MS = [1000, 2000, 5000, 10000, 30000];
const POLL_INTERVAL_MS = 10000;

let rooms = [];
let liveAccessEnabled = false;
let ws = null;
let reconnectAttempt = 0;
let reconnectTimer = null;
let pollTimer = null;

// main()を先頭近くに置く(関数宣言はhoistされるため実行順に影響しない):
// test/featureFlags.test.mjsが「cleaningVisualAllowed()のチェックが最初の
// fetch(呼び出しよりソース上で先に現れること」をソーステキストで確認しており、
// 下のfetchCleaning()等のヘルパー定義よりも前にこの判定が現れる必要がある。
async function main() {
  const date = getDate();
  document.getElementById("mc-date").textContent = formatDateJp(date);
  const freshnessEl = document.getElementById("mc-freshness");

  // CLEANING_VISUAL_READY が false の間は、清掃担当者の通常導線からこの
  // 画面へ到達させない(内部QAは ?preview=1 を付けて直接アクセスすれば確認できる)。
  if (!cleaningVisualAllowed()) {
    freshnessEl.textContent = "";
    document.getElementById("mc-room-list").innerHTML = `<div class="mc-empty">清掃指示書：準備中</div>`;
    return;
  }

  liveAccessEnabled = cleaningLiveAccessAllowed();

  await fullResync(date);

  if (liveAccessEnabled) {
    attachVisibilityResync(date);
    startPollFallback(date);
    connectLive(date);
  }
}

function getDate() {
  const d = new URLSearchParams(window.location.search).get("date");
  return d && DATE_RE.test(d) ? d : todayJst();
}

function setConnStatus(state) {
  const el = document.getElementById("mc-conn-status");
  if (!el) return;
  el.classList.remove("mc-conn-connected", "mc-conn-reconnecting");
  if (state === "connected") {
    el.textContent = "● リアルタイム接続中";
    el.classList.add("mc-conn-connected");
  } else if (state === "reconnecting") {
    el.textContent = "更新を再接続中…";
    el.classList.add("mc-conn-reconnecting");
  } else {
    el.textContent = "";
  }
}

function renderFullBody() {
  const listEl = document.getElementById("mc-room-list");
  listEl.innerHTML = renderMobileCleaningBody(rooms, liveAccessEnabled);
}

// 該当room cardだけ更新する(要件29 — ページ全体再render不要)。
function patchRoomBlock(roomNumber) {
  const room = rooms.find((r) => r.room_number === roomNumber);
  if (!room) return;
  const el = document.querySelector(`[data-room-number="${cssEscape(roomNumber)}"]`);
  if (!el) return;
  const wrapper = document.createElement("div");
  wrapper.innerHTML = renderMobileRoomBlock(room, liveAccessEnabled);
  const replacement = wrapper.firstElementChild;
  if (replacement) el.replaceWith(replacement);
}

function cssEscape(value) {
  if (typeof CSS !== "undefined" && CSS.escape) return CSS.escape(value);
  return String(value).replace(/[^a-zA-Z0-9_-]/g, "\\$&");
}

async function fetchCleaning(date) {
  const res = await fetch(`/api/cleaning?date=${encodeURIComponent(date)}`, { cache: "no-store" });
  if (!res.ok) return null;
  const cleaning = await res.json();
  assertNoFinancialKeys(cleaning);
  assertNoForbiddenCleaningKeys(cleaning);
  return cleaning;
}

// 初回ロード、および再接続後のfull resync(要件30)で使う。
async function fullResync(date) {
  const freshnessEl = document.getElementById("mc-freshness");
  let cleaning = null;
  try {
    cleaning = await fetchCleaning(date);
  } catch (e) {
    cleaning = null;
  }
  if (!cleaning) {
    freshnessEl.textContent = "データを取得できませんでした";
    document.getElementById("mc-room-list").innerHTML = `<div class="mc-empty">対象日の清掃データがありません</div>`;
    rooms = [];
    return;
  }
  rooms = Array.isArray(cleaning.rooms) ? cleaning.rooms : [];
  freshnessEl.textContent = "";
  renderFullBody();
}

function handleLiveMessage(raw, date) {
  let msg;
  try {
    msg = JSON.parse(raw);
  } catch (e) {
    return;
  }
  if (!msg || msg.type !== "room_access_status" || msg.date !== date) return;
  const room = rooms.find((r) => r.room_number === msg.roomNumber);
  if (!room) return;
  room.roomAccessStatus = msg.status;
  room.roomAccessUpdatedAt = msg.updatedAt || null;
  patchRoomBlock(msg.roomNumber);
}

function wsUrlFor(date) {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${window.location.host}/api/cleaning/live?date=${encodeURIComponent(date)}`;
}

function clearReconnectTimer() {
  if (reconnectTimer) {
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }
}

function scheduleReconnect(date) {
  clearReconnectTimer();
  const idx = Math.min(reconnectAttempt, RECONNECT_DELAYS_MS.length - 1);
  const delay = RECONNECT_DELAYS_MS[idx];
  reconnectAttempt++;
  reconnectTimer = setTimeout(() => connectLive(date), delay);
}

function connectLive(date) {
  if (!liveAccessEnabled) return;
  setConnStatus("reconnecting");
  let socket;
  try {
    socket = new WebSocket(wsUrlFor(date));
  } catch (e) {
    scheduleReconnect(date);
    return;
  }
  ws = socket;
  socket.addEventListener("open", () => {
    reconnectAttempt = 0;
    setConnStatus("connected");
    // 取りこぼし補正のため、接続確立のたびに必ずfull resyncする(要件30)。
    fullResync(date);
  });
  socket.addEventListener("message", (event) => {
    handleLiveMessage(event.data, date);
  });
  socket.addEventListener("close", () => {
    if (ws === socket) ws = null;
    setConnStatus("reconnecting");
    scheduleReconnect(date);
  });
  socket.addEventListener("error", () => {
    try {
      socket.close();
    } catch (e) {
      // already closing
    }
  });
}

// WebSocketが利用できない/切断中でも業務停止させないためのpollフォールバック
// (要件31)。WebSocketが正常に開いている間は何もしない。
function startPollFallback(date) {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(() => {
    if (ws && ws.readyState === WebSocket.OPEN) return;
    fullResync(date);
  }, POLL_INTERVAL_MS);
}

function attachVisibilityResync(date) {
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") {
      fullResync(date);
    }
  });
}

main();
