// print-cleaning.js — bootstrap for /ops/print/cleaning.
// PLACEHOLDER TEMPLATE wiring — pending source photo of the original paper
// cleaning sheet. Fetches the SAME /api/cleaning?date=... endpoint the
// mobile view (/cleaning/today) uses, with no divergent client-side
// filtering, so overrides show consistently in both places.
import { renderCleaningSheetTemplate } from "../../cleaningSheetTemplate.js";
import { assertNoFinancialKeys } from "../../financialGuard.js";
import { waitForPrintReady } from "../../printUtils.js";
import { todayJst, formatDateJp } from "../../jst.js";
import { cleaningVisualAllowed } from "../../featureFlags.js";

const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

function getDate() {
  const d = new URLSearchParams(window.location.search).get("date");
  return d && DATE_RE.test(d) ? d : todayJst();
}

async function main() {
  const date = getDate();
  document.getElementById("cs-date-label").textContent = formatDateJp(date);
  const tableRoot = document.getElementById("cs-table-root");

  // 原本写真確認前は、一般スタッフの通常導線からこの仮visualへ到達させない。
  // 内部QAは ?preview=1 を付けてこのURLへ直接アクセスすれば確認できる。
  // データ自体もfetchしない（未使用のPIIをネットワークに流さないため）し、
  // window.print()も呼ばない（仮の帳票を実際に印刷させないため）。
  if (!cleaningVisualAllowed()) {
    tableRoot.innerHTML = `<p class="no-print">清掃指示書：準備中（原本写真確認後に有効化されます）</p>`;
    return;
  }

  let cleaning = null;
  try {
    const res = await fetch(`/api/cleaning?date=${encodeURIComponent(date)}`, { cache: "no-store" });
    if (res.ok) {
      cleaning = await res.json();
      assertNoFinancialKeys(cleaning);
    }
  } catch (e) {
    cleaning = null;
  }

  const rooms = (cleaning && Array.isArray(cleaning.rooms)) ? cleaning.rooms : [];
  tableRoot.innerHTML = renderCleaningSheetTemplate(rooms, date);

  await waitForPrintReady(null);
  window.print();
}

window.addEventListener("afterprint", () => {
  // Intentionally a no-op: keep the rendered sheet in place.
});

main();
