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
import { renderMobileCleaningBody } from "../cleaningSheetTemplate.js";
import { assertNoFinancialKeys, assertNoForbiddenCleaningKeys } from "../financialGuard.js";
import { todayJst, formatDateJp } from "../jst.js";
import { cleaningVisualAllowed } from "../featureFlags.js";

const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

function getDate() {
  const d = new URLSearchParams(window.location.search).get("date");
  return d && DATE_RE.test(d) ? d : todayJst();
}

async function main() {
  const date = getDate();
  document.getElementById("mc-date").textContent = formatDateJp(date);
  const listEl = document.getElementById("mc-room-list");
  const freshnessEl = document.getElementById("mc-freshness");

  // CLEANING_VISUAL_READY が false の間は、清掃担当者の通常導線からこの
  // 画面へ到達させない（内部QAは ?preview=1 を付けて直接アクセスすれば確認できる）。
  if (!cleaningVisualAllowed()) {
    freshnessEl.textContent = "";
    listEl.innerHTML = `<div class="mc-empty">清掃指示書：準備中</div>`;
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

  if (!cleaning) {
    freshnessEl.textContent = "データを取得できませんでした";
    listEl.innerHTML = `<div class="mc-empty">対象日の清掃データがありません</div>`;
    return;
  }

  const rooms = Array.isArray(cleaning.rooms) ? cleaning.rooms : [];
  freshnessEl.textContent = "";
  listEl.innerHTML = renderMobileCleaningBody(rooms);
}

main();
