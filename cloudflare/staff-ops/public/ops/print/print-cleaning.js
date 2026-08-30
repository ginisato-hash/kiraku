// print-cleaning.js — bootstrap for /ops/print/cleaning (FINAL design).
// Fetches the SAME /api/cleaning?date=... endpoint the mobile view
// (/cleaning/today) and the Staff Ops "Staff cleaning list" use, with no
// divergent client-side filtering, so overrides show consistently
// everywhere — see test/sameEndpoint.test.mjs.
import { renderCleaningSheetTemplate } from "../../cleaningSheetTemplate.js";
import { assertNoFinancialKeys, assertNoForbiddenCleaningKeys } from "../../financialGuard.js";
import { waitForPrintReady } from "../../printUtils.js";
import { todayJst } from "../../jst.js";
import { cleaningVisualAllowed } from "../../featureFlags.js";

const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

function getDate() {
  const d = new URLSearchParams(window.location.search).get("date");
  return d && DATE_RE.test(d) ? d : todayJst();
}

async function main() {
  const date = getDate();
  const contentRoot = document.getElementById("cs-print-content");

  // CLEANING_VISUAL_READY が false の間は、一般スタッフの通常導線からこの
  // 帳票へ到達させない(内部QAは ?preview=1 を付けて直接アクセスすれば確認できる)。
  // データ自体もfetchしない（未使用のPIIをネットワークに流さないため）し、
  // window.print()も呼ばない。
  if (!cleaningVisualAllowed()) {
    contentRoot.innerHTML = `<p class="no-print">清掃指示書：準備中</p>`;
    return;
  }

  let cleaning = null;
  try {
    const res = await fetch(`/api/cleaning?date=${encodeURIComponent(date)}`, { cache: "no-store" });
    if (res.ok) {
      cleaning = await res.json();
      assertNoFinancialKeys(cleaning);
      assertNoForbiddenCleaningKeys(cleaning);
    }
  } catch (e) {
    cleaning = null;
  }

  const rooms = (cleaning && Array.isArray(cleaning.rooms)) ? cleaning.rooms : [];
  contentRoot.innerHTML = renderCleaningSheetTemplate(rooms, date);

  await waitForPrintReady(null);
  window.print();
}

window.addEventListener("afterprint", () => {
  // Intentionally a no-op: keep the rendered sheet in place.
});

main();
