// dailyOps.js — 喜らく DAILY OPS 画面の薄いレンダラ。
// 責務: fetch / buildDailyOpsViewModel呼び出し / render / 日付ナビゲーションのみ。
// 生のAPIフィールドは直接参照せず、dailyOpsViewModel.js経由で描画する
// (bi-webのapp.js -> biViewModel.js -> components.js と同じ3層パターン)。
//
// セキュリティ上重要: このページのfetchは /api/daily-ops と /api/cleaning のみを
// 叩く。財務/BIエンドポイントには一切アクセスしない。どちらのJSONもレンダリング前に
// assertNoFinancialKeys() を通し、revenue/price/commission等の財務系keyが
// 万一混入していないかを実行時にも確認する。
import { buildDailyOpsViewModel, formatFreshness } from "./dailyOpsViewModel.js";
import { mountCleaningStaffView } from "./cleaningStaffView.js";
import { assertNoFinancialKeys } from "./financialGuard.js";
import { todayJst, addDaysToDateString, formatDateJp } from "./jst.js";
import { cleaningVisualAllowed } from "./featureFlags.js";

const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

let currentDate = null;

async function getJSON(url) {
  try {
    const r = await fetch(url, { cache: "no-store" });
    if (!r.ok) return null;
    const data = await r.json();
    assertNoFinancialKeys(data);
    return data;
  } catch (e) {
    return null;
  }
}

function fetchDailyOps(date) {
  return getJSON(`/api/daily-ops?date=${encodeURIComponent(date)}`);
}

function getDateFromUrl() {
  const d = new URLSearchParams(window.location.search).get("date");
  return d && DATE_RE.test(d) ? d : null;
}

function updateUrlDate(date) {
  const url = new URL(window.location.href);
  url.searchParams.set("date", date);
  window.history.replaceState({}, "", url);
}

function renderSummaryCards(cards) {
  return cards.map((c) => `<div class="summary-card">
    <div class="summary-label">${c.label}</div>
    <div class="summary-value">${c.roomCount}室 / ${c.totalGuests}名</div>
    <div class="summary-breakdown">大人 ${c.adults}名　子供 ${c.children}名</div>
  </div>`).join("");
}

function addressLines(address) {
  if (!address || typeof address !== "object") return "住所: 未登録";
  const { postcode, prefecture, city, rest } = address;
  const line1 = postcode ? `〒${postcode}` : null;
  const line2 = [prefecture, city, rest].filter(Boolean).join("");
  const parts = [line1, line2].filter(Boolean);
  return parts.length ? `住所: ${parts.join(" ")}` : "住所: 未登録";
}

function renderBookingRow(row) {
  const roomLabel = row.hasRoomNumber ? `${row.roomTypeLabel}（${row.roomNumber}）` : `${row.roomTypeLabel}（客室番号未確定）`;
  const arrivalTime = row.arrivalTime ? `到着予定 ${row.arrivalTime}` : "到着時刻未定";
  const phone = row.phone ? `電話: ${row.phone}` : "電話: 未登録";
  const notes = row.notes ? `備考: ${row.notes}` : "備考: なし";
  return `<div class="booking-row">
    <div class="booking-main">
      <div class="booking-guest">${row.guestName}<span class="booking-ota">${row.otaName}</span></div>
      <div class="booking-room">${roomLabel}</div>
      <div class="booking-dates">CI ${row.checkinDate} → CO ${row.checkoutDate} ｜ ${arrivalTime}</div>
    </div>
    <div class="booking-side">
      <div class="booking-guests">大人 ${row.adults}名　子供 ${row.children}名　計 ${row.totalGuests}名</div>
      <details class="booking-detail-toggle">
        <summary>詳細</summary>
        <div class="detail-body">
          ${phone}<br>${addressLines(row.address)}<br>${notes}
        </div>
      </details>
    </div>
  </div>`;
}

function renderBookingSection(title, rows, emptyText) {
  if (!rows.length) {
    return `<h2>${title}</h2><div class="empty-state">${emptyText}</div>`;
  }
  return `<h2>${title}</h2>${rows.map(renderBookingRow).join("")}`;
}

function setCleaningButtons(date) {
  const showBtn = document.getElementById("show-cleaning-btn");
  const printBtn = document.getElementById("print-cleaning-btn");
  if (cleaningVisualAllowed()) {
    showBtn.disabled = false;
    showBtn.removeAttribute("aria-disabled");
    showBtn.textContent = "清掃指示書を表示";
    printBtn.href = `/ops/print/cleaning?date=${encodeURIComponent(date)}`;
    printBtn.classList.remove("is-disabled");
    printBtn.removeAttribute("aria-disabled");
    printBtn.textContent = "本日の清掃指示書を印刷";
    return;
  }
  // CLEANING_VISUAL_READY が false の間は、一般スタッフの通常導線から到達できないように
  // 両ボタンを無効化する（内部route/data model自体は温存。QAは ?preview=1 で確認可能）。
  showBtn.disabled = true;
  showBtn.setAttribute("aria-disabled", "true");
  showBtn.textContent = "清掃指示書：準備中";
  printBtn.removeAttribute("href");
  printBtn.classList.add("is-disabled");
  printBtn.setAttribute("aria-disabled", "true");
  printBtn.textContent = "清掃指示書：準備中";
}

function setPrintLinks(date, hasArrivals) {
  const guestBtn = document.getElementById("print-guest-register-btn");
  setCleaningButtons(date);
  if (hasArrivals) {
    guestBtn.href = `/ops/print/guest-register?date=${encodeURIComponent(date)}`;
    guestBtn.classList.remove("is-disabled");
    guestBtn.removeAttribute("aria-disabled");
    guestBtn.textContent = "本日の宿泊者名簿を一括印刷";
  } else {
    guestBtn.removeAttribute("href");
    guestBtn.classList.add("is-disabled");
    guestBtn.setAttribute("aria-disabled", "true");
    guestBtn.textContent = "本日のチェックインはありません";
    guestBtn.addEventListener("click", (e) => e.preventDefault());
  }
}

async function loadAndRender(date) {
  currentDate = date;
  updateUrlDate(date);
  document.getElementById("current-date-label").textContent = formatDateJp(date);
  document.getElementById("header-meta").textContent = "読み込み中…";

  const dayData = await fetchDailyOps(date);
  if (!dayData) {
    document.getElementById("header-meta").textContent = "データが見つかりません";
    document.getElementById("summary-grid").innerHTML = "";
    document.getElementById("arrivals-section").innerHTML = renderBookingSection("本日の到着", [], "データを取得できませんでした");
    document.getElementById("departures-section").innerHTML = "";
    document.getElementById("stayovers-section").innerHTML = "";
    setPrintLinks(date, false);
    loadCleaningStaffView();
    return;
  }

  const vm = buildDailyOpsViewModel(dayData, date);
  document.getElementById("summary-grid").innerHTML = renderSummaryCards(vm.summaryCards);
  document.getElementById("arrivals-section").innerHTML =
    renderBookingSection("本日の到着", vm.arrivals, "本日のチェックインはありません");
  document.getElementById("departures-section").innerHTML =
    renderBookingSection("本日の出発", vm.departures, "本日のチェックアウトはありません");
  document.getElementById("stayovers-section").innerHTML =
    renderBookingSection("連泊", vm.stayovers, "連泊はありません");
  setPrintLinks(date, vm.hasArrivals);
  loadCleaningStaffView();

  const freshness = formatFreshness(null, Date.now());
  document.getElementById("header-meta").textContent = freshness.text || "";
  document.getElementById("header-meta").classList.toggle("is-stale", freshness.stale);
}

// 編集可能なStaff cleaning list（override保存/reset）はこの画面上のみに存在する
// 唯一の導線であるため、日付が変わるたびloadAndRender()から必ず自動描画する
// （"清掃指示書を表示"ボタンは/cleaning/todayへのページ遷移専任になったため、
// クリック待ちにするとoverride編集機能自体に到達できなくなる）。
function loadCleaningStaffView() {
  const section = document.getElementById("cleaning-preview-section");
  if (!cleaningVisualAllowed()) {
    section.innerHTML = `<div class="empty-state">清掃指示書：準備中</div>`;
    return;
  }
  section.innerHTML = `<h2>清掃指示（Staff cleaning list）</h2><div id="cleaning-staff-view-root"></div>`;
  const root = document.getElementById("cleaning-staff-view-root");
  mountCleaningStaffView(root, currentDate);
}

function attachListeners() {
  document.getElementById("prev-day-btn").addEventListener("click", () => {
    loadAndRender(addDaysToDateString(currentDate, -1));
  });
  document.getElementById("next-day-btn").addEventListener("click", () => {
    loadAndRender(addDaysToDateString(currentDate, 1));
  });
  document.getElementById("today-btn").addEventListener("click", () => {
    loadAndRender(todayJst());
  });
  document.getElementById("show-cleaning-btn").addEventListener("click", () => {
    window.location.href = `/cleaning/today?date=${encodeURIComponent(currentDate)}`;
  });
  document.getElementById("logout-btn").addEventListener("click", async () => {
    try {
      await fetch("/api/auth/logout", { method: "POST" });
    } finally {
      window.location.href = "/login";
    }
  });
}

async function main() {
  attachListeners();
  const urlDate = getDateFromUrl();
  await loadAndRender(urlDate || todayJst());
}

main();
