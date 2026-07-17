// 喜らく 速報BI - 画面描画（薄いレンダラ）。
// 責務: fetch / buildBiViewModel呼び出し / render / accordion open-close / error state / 月選択のみ。
// DOM生成は components.js の関数に委譲する。生のbi_snapshotフィールドは直接参照しない。
import { buildBiViewModel, formatFreshness } from "./biViewModel.js";
import {
  renderCommandCenter, renderInsightBanner, renderStatusChips, renderNotes,
  renderDetails, renderHeader, renderErrorState, renderSkeleton, renderDailySummarySection,
  renderRoomTypeOccupancyChart, renderRoomTypeRevenueMix, renderRefreshButton,
} from "./components.js";

const MONTH_RE = /^\d{4}-\d{2}$/;

let manifestCache = null;
let lastGoodVm = null;
let currentSelectedMonth = null;
let refreshState = "idle"; // "idle" | "loading" | "success" | "error"
let refreshResetTimer = null;
// 月切替の連打や5分ごとの自動再取得が重なった際、後発リクエストより先に古いリクエストが
// resolveして画面を上書きする race conditionを防ぐためのsequence番号。
let requestSeq = 0;

// 日付跨ぎ後もブラウザ/中間キャッシュに古いBIデータを見せない（重大不具合対応）。
// bust=true の場合のみ ?_=Date.now() を付与する（手動更新ボタン専用。通常のfetchは
// cache:"no-store" だけで十分なため付けない。付けすぎるとURLが汚れるだけで意味がない）。
async function getJSON(url, { bust } = {}) {
  const finalUrl = bust ? `${url}${url.includes("?") ? "&" : "?"}_=${Date.now()}` : url;
  try {
    const r = await fetch(finalUrl, { cache: "no-store" });
    if (!r.ok) return null;
    return await r.json();
  } catch (e) {
    return null;
  }
}

async function fetchManifest(bust) {
  return getJSON("/api/manifest", { bust });
}

async function fetchSnapshot(month, bust) {
  const url = month ? `/api/snapshot?month=${encodeURIComponent(month)}` : "/api/snapshot";
  return getJSON(url, { bust });
}

async function fetchValidation(month, bust) {
  const url = month
    ? `/data/months/${encodeURIComponent(month)}/bi_validation_status.json`
    : "/data/bi_validation_status.json";
  return getJSON(url, { bust });
}

async function fetchException(month, bust) {
  const url = month
    ? `/data/months/${encodeURIComponent(month)}/bi_exception_summary.json`
    : "/data/bi_exception_summary.json";
  return getJSON(url, { bust });
}

function getMonthFromUrl() {
  const m = new URLSearchParams(window.location.search).get("month");
  return m && MONTH_RE.test(m) ? m : null;
}

function updateUrlMonth(month) {
  const url = new URL(window.location.href);
  if (month) url.searchParams.set("month", month);
  else url.searchParams.delete("month");
  window.history.replaceState({}, "", url);
}

function showSkeleton() {
  const s = renderSkeleton();
  document.getElementById("header-meta").innerHTML = s.header;
  document.getElementById("command-center").innerHTML = s.cards;
  document.getElementById("details-grid").innerHTML = s.details;
}

function showFullError() {
  document.getElementById("daily-summary-section").innerHTML = "";
  document.getElementById("command-center").innerHTML = renderErrorState();
  document.getElementById("room-type-occupancy-chart").innerHTML = "";
  document.getElementById("room-type-revenue-mix").innerHTML = "";
  document.getElementById("pace-summary").innerHTML = "";
  document.getElementById("status-row").innerHTML = "";
  document.getElementById("notes-section").innerHTML = "";
  document.getElementById("details-grid").innerHTML = "";
  document.getElementById("header-meta").textContent = "データ未取得";
}

// fetch失敗時：前回表示があれば消さずにエラーbannerだけ出す。無ければフル error state。
function showError() {
  if (lastGoodVm) {
    document.getElementById("pace-summary").innerHTML = renderInsightBanner({
      text: "データの取得に失敗しました。前回表示分を表示しています。", tone: "red",
    });
    return;
  }
  showFullError();
}

function attachMonthSelectListener() {
  const sel = document.getElementById("month-select");
  if (sel) {
    sel.addEventListener("change", (e) => handleMonthChange(e.target.value));
  }
}

// 「最新情報に更新」ボタンは他のcard/月選択とは別のDOM(#refresh-button-wrap)として
// 独立更新する。全体render()を呼ばずに済むため、開いているdetails(本日新規予約一覧等)を
// 閉じずに状態(loading/success/error)だけ反映できる。
function renderRefreshButtonUi() {
  const wrap = document.getElementById("refresh-button-wrap");
  if (!wrap) return;
  wrap.outerHTML = renderRefreshButton(refreshState);
  attachRefreshButtonListener();
}

function attachRefreshButtonListener() {
  const btn = document.getElementById("refresh-button");
  if (btn) {
    btn.addEventListener("click", handleManualRefresh);
  }
}

function setRefreshState(state) {
  refreshState = state;
  renderRefreshButtonUi();
  if (refreshResetTimer) {
    clearTimeout(refreshResetTimer);
    refreshResetTimer = null;
  }
  if (state === "success" || state === "error") {
    refreshResetTimer = setTimeout(() => setRefreshState("idle"), 4000);
  }
}

function setLoading(loading) {
  document.body.classList.toggle("is-loading", loading);
  const sel = document.getElementById("month-select");
  if (sel) sel.disabled = loading;
}

// 明細drilldownの<details>toggleに合わせてaria-expandedを更新する（1回だけ委譲登録）。
function attachDailySummaryToggleListener() {
  document.querySelectorAll("#daily-summary-section details.daily-summary-details").forEach((el) => {
    el.addEventListener("toggle", () => {
      const summary = el.querySelector("summary");
      if (summary) summary.setAttribute("aria-expanded", el.open ? "true" : "false");
    });
  });
}

function render(vm) {
  lastGoodVm = vm;
  const header = renderHeader(vm.header);
  const freshness = formatFreshness(vm.header.generatedAtJst, Date.now());
  document.getElementById("header-meta").textContent =
    freshness.text ? `${header.metaLine} ｜ ${freshness.text}` : header.metaLine;
  document.getElementById("header-meta").classList.toggle("is-stale", freshness.stale);
  document.getElementById("header-right").innerHTML =
    header.monthSelectorHtml + header.pillHtml + renderRefreshButton(refreshState);
  attachMonthSelectListener();
  attachRefreshButtonListener();

  document.getElementById("daily-summary-section").innerHTML =
    renderDailySummarySection(vm.dailySummaryCards);
  attachDailySummaryToggleListener();
  document.getElementById("command-center").innerHTML = renderCommandCenter(vm.primaryCards);
  document.getElementById("room-type-occupancy-chart").innerHTML =
    renderRoomTypeOccupancyChart(vm.roomTypeOccupancyChart);
  document.getElementById("room-type-revenue-mix").innerHTML =
    renderRoomTypeRevenueMix(vm.roomTypeRevenueMix);
  document.getElementById("pace-summary").innerHTML = renderInsightBanner(vm.paceComment);
  document.getElementById("status-row").innerHTML = renderStatusChips(vm.statusChips);
  document.getElementById("notes-section").innerHTML = renderNotes(vm.notes);
  document.getElementById("details-grid").innerHTML =
    renderDetails(vm.details, vm.validationSummary, vm.exceptionCount);
}

async function loadAndRender(month, opts) {
  const bust = !!(opts && opts.bust);
  currentSelectedMonth = month || null;
  const seq = ++requestSeq;
  setLoading(true);
  const [snapshot, validation, exception] = await Promise.all([
    fetchSnapshot(month, bust), fetchValidation(month, bust), fetchException(month, bust),
  ]);
  if (seq !== requestSeq) {
    // より新しいリクエストが既に発行済み。このレスポンスは古いので画面には反映しない。
    return false;
  }
  setLoading(false);
  if (!snapshot) {
    showError();
    return false;
  }
  const vm = buildBiViewModel(snapshot, manifestCache, validation, exception, { selectedMonth: month });
  render(vm);
  return true;
}

async function handleMonthChange(month) {
  updateUrlMonth(month);
  await loadAndRender(month);
}

// 「最新情報に更新」クリック時のハンドラ。WorkerはBeds24 APIを叩かないため、これは
// R2に公開済みの最新snapshotをcache bypassで再取得するだけ(Beds24再取得の意味ではない)。
async function handleManualRefresh() {
  if (refreshState === "loading") return;
  setRefreshState("loading");
  manifestCache = await fetchManifest(true);
  const ok = await loadAndRender(currentSelectedMonth, { bust: true });
  setRefreshState(ok ? "success" : "error");
}

async function main() {
  showSkeleton();
  manifestCache = await fetchManifest();
  const urlMonth = getMonthFromUrl();
  const selectedMonth = urlMonth || (manifestCache && manifestCache.default_month) || null;
  await loadAndRender(selectedMonth);
}

main();
// 5分ごとに現在選択中の月を再取得する（skeletonへは戻さない）。
setInterval(() => loadAndRender(currentSelectedMonth), 5 * 60 * 1000);
