// 喜らく 速報BI - 画面描画（薄いレンダラ）。
// 責務: fetch / buildBiViewModel呼び出し / render / accordion open-close / error state のみ。
// DOM生成は components.js の関数に委譲する。生のbi_snapshotフィールドは直接参照しない。
import { buildBiViewModel } from "./biViewModel.js";
import {
  renderCommandCenter, renderInsightBanner, renderStatusChips, renderNotes,
  renderDetails, renderHeader, renderErrorState, renderSkeleton,
} from "./components.js";

const DATA = "/data/";

async function getJSON(name) {
  try {
    const r = await fetch(DATA + name + "?t=" + Date.now());
    if (!r.ok) return null;
    return await r.json();
  } catch (e) {
    return null;
  }
}

function showSkeleton() {
  const s = renderSkeleton();
  document.getElementById("header-meta").innerHTML = s.header;
  document.getElementById("command-center").innerHTML = s.cards;
  document.getElementById("details-grid").innerHTML = s.details;
}

function showError() {
  document.getElementById("command-center").innerHTML = renderErrorState();
  document.getElementById("pace-summary").innerHTML = "";
  document.getElementById("status-row").innerHTML = "";
  document.getElementById("notes-section").innerHTML = "";
  document.getElementById("details-grid").innerHTML = "";
  document.getElementById("header-meta").textContent = "データ未取得";
}

function render(vm) {
  const header = renderHeader(vm.header);
  document.getElementById("header-meta").textContent = header.metaLine;
  document.getElementById("header-right").innerHTML = header.pillHtml;

  document.getElementById("command-center").innerHTML = renderCommandCenter(vm.primaryCards);
  document.getElementById("pace-summary").innerHTML = renderInsightBanner(vm.paceComment);
  document.getElementById("status-row").innerHTML = renderStatusChips(vm.statusChips);
  document.getElementById("notes-section").innerHTML = renderNotes(vm.notes);
  document.getElementById("details-grid").innerHTML =
    renderDetails(vm.details, vm.validationSummary, vm.exceptionCount);
}

async function main() {
  showSkeleton();
  const [snapshot, manifest, validation, exception] = await Promise.all([
    getJSON("bi_snapshot.json"), getJSON("manifest.json"),
    getJSON("bi_validation_status.json"), getJSON("bi_exception_summary.json"),
  ]);
  if (!snapshot) {
    showError();
    return;
  }
  const vm = buildBiViewModel(snapshot, manifest, validation, exception);
  render(vm);
}

main();
setInterval(main, 5 * 60 * 1000); // 5分ごとに再読込
