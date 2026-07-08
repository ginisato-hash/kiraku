// 喜らく 速報BI - 画面描画（薄いレンダラ）。
// 責務: fetch / buildBiViewModel呼び出し / render / accordion open-close / error state / 月選択のみ。
// DOM生成は components.js の関数に委譲する。生のbi_snapshotフィールドは直接参照しない。
import { buildBiViewModel } from "./biViewModel.js";
import {
  renderCommandCenter, renderInsightBanner, renderStatusChips, renderNotes,
  renderDetails, renderHeader, renderErrorState, renderSkeleton, renderDailyNewBookings,
} from "./components.js";

const MONTH_RE = /^\d{4}-\d{2}$/;

let manifestCache = null;
let lastGoodVm = null;
let currentSelectedMonth = null;

async function getJSON(url) {
  try {
    const sep = url.includes("?") ? "&" : "?";
    const r = await fetch(url + sep + "t=" + Date.now());
    if (!r.ok) return null;
    return await r.json();
  } catch (e) {
    return null;
  }
}

async function fetchManifest() {
  return getJSON("/api/manifest");
}

async function fetchSnapshot(month) {
  const url = month ? `/api/snapshot?month=${encodeURIComponent(month)}` : "/api/snapshot";
  return getJSON(url);
}

async function fetchValidation(month) {
  const url = month
    ? `/data/months/${encodeURIComponent(month)}/bi_validation_status.json`
    : "/data/bi_validation_status.json";
  return getJSON(url);
}

async function fetchException(month) {
  const url = month
    ? `/data/months/${encodeURIComponent(month)}/bi_exception_summary.json`
    : "/data/bi_exception_summary.json";
  return getJSON(url);
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

function setLoading(loading) {
  document.body.classList.toggle("is-loading", loading);
  const sel = document.getElementById("month-select");
  if (sel) sel.disabled = loading;
}

function render(vm) {
  lastGoodVm = vm;
  const header = renderHeader(vm.header);
  document.getElementById("header-meta").textContent = header.metaLine;
  document.getElementById("header-right").innerHTML = header.monthSelectorHtml + header.pillHtml;
  attachMonthSelectListener();

  document.getElementById("daily-summary-section").innerHTML = renderDailyNewBookings(vm.dailyNewBookings);
  document.getElementById("command-center").innerHTML = renderCommandCenter(vm.primaryCards);
  document.getElementById("pace-summary").innerHTML = renderInsightBanner(vm.paceComment);
  document.getElementById("status-row").innerHTML = renderStatusChips(vm.statusChips);
  document.getElementById("notes-section").innerHTML = renderNotes(vm.notes);
  document.getElementById("details-grid").innerHTML =
    renderDetails(vm.details, vm.validationSummary, vm.exceptionCount);
}

async function loadAndRender(month) {
  currentSelectedMonth = month || null;
  setLoading(true);
  const [snapshot, validation, exception] = await Promise.all([
    fetchSnapshot(month), fetchValidation(month), fetchException(month),
  ]);
  setLoading(false);
  if (!snapshot) {
    showError();
    return;
  }
  const vm = buildBiViewModel(snapshot, manifestCache, validation, exception, { selectedMonth: month });
  render(vm);
}

async function handleMonthChange(month) {
  updateUrlMonth(month);
  await loadAndRender(month);
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
