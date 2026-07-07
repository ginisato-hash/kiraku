// 喜らく 速報BI - 画面描画（薄いレンダラ）。
// 生のbi_snapshotフィールドは直接参照せず、biViewModel.js の buildBiViewModel() を介して描画する。
// BIデータは Worker のルート /data/*（R2 bucket kiraku-bi-data の latest/）から取得する。
import { buildBiViewModel } from "./biViewModel.js";

const DATA = "/data/";

async function getJSON(name) {
  try {
    const r = await fetch(DATA + name + "?t=" + Date.now());
    if (!r.ok) return null;
    return await r.json();
  } catch (e) { return null; }
}

function cardHtml(c) {
  const badge = c.badge ? `<div class="badge ${c.badgeTone || "neutral"}">${c.badge}</div>` : "";
  return `<div class="card"><div class="label">${c.label}</div>
          <div class="value ${c.tone || ""}">${c.value}</div>${badge}</div>`;
}

function chipHtml(c) {
  return `<span class="chip ${c.tone}"><span class="chip-label">${c.label}</span>${c.value}</span>`;
}

function detailRowsHtml(rows) {
  return `<table class="detail-table">${rows.map(([k, v]) =>
    `<tr><td class="k">${k}</td><td class="v">${v}</td></tr>`).join("")}</table>`;
}

function accordionHtml(title, rowsHtml) {
  return `<details class="accordion"><summary>${title}</summary><div class="accordion-body">${rowsHtml}</div></details>`;
}

function render(vm) {
  document.getElementById("updated").textContent =
    `最終更新: ${vm.header.generatedAtJst || "—"} ｜ 対象月: ${vm.header.targetMonth}`;

  const commentEl = document.getElementById("pace-comment");
  commentEl.textContent = vm.paceComment.text;
  commentEl.className = "pace-comment " + vm.paceComment.severity;

  document.getElementById("cards").innerHTML = vm.primaryCards.map(cardHtml).join("");
  document.getElementById("chips").innerHTML = vm.statusChips.map(chipHtml).join("");
  document.getElementById("notes").innerHTML = vm.notes.map(
    (n) => `<div class="note ${n.severity}">${n.text}</div>`).join("");

  const d = vm.details;
  document.getElementById("accordions").innerHTML = [
    accordionHtml("損益分岐の詳細", detailRowsHtml(d.breakeven)),
    accordionHtml("予約ペースの詳細", detailRowsHtml(d.pace)),
    accordionHtml("人件費の詳細", detailRowsHtml(d.labor)),
    accordionHtml("変動費率の詳細", detailRowsHtml(d.variableCost)),
    accordionHtml("MC/GOPの詳細", detailRowsHtml(d.mc)),
    accordionHtml("財務状態・返済の詳細", detailRowsHtml(d.finance)),
    accordionHtml("検証・例外", detailRowsHtml(d.validation) +
      (vm.validationSummary
        ? `<p class="detail-note">validation: ${vm.validationSummary.ok ? "OK" : "要確認 " + vm.validationSummary.criticalCount + "件"} / warn ${vm.validationSummary.warningCount} ｜ exception件数: ${vm.exceptionCount ?? "—"}</p>`
        : "")),
  ].join("");
}

async function main() {
  const [snapshot, manifest, validation, exception] = await Promise.all([
    getJSON("bi_snapshot.json"), getJSON("manifest.json"),
    getJSON("bi_validation_status.json"), getJSON("bi_exception_summary.json"),
  ]);
  if (!snapshot) {
    document.getElementById("app").innerHTML =
      `<div class="note warn">BIデータが見つかりません。R2にlatest/が投入されているか確認してください（refresh-beds24-bi → 公開）。</div>`;
    return;
  }
  const vm = buildBiViewModel(snapshot, manifest, validation, exception);
  render(vm);
}

main();
setInterval(main, 5 * 60 * 1000); // 5分ごとに再読込
