// today.js — bootstrap for the mobile cleaning view (/cleaning/today).
// Read-only display for cleaning staff on a phone: today (JST) by default,
// accepts ?date=. No start/complete/inspected status buttons in this
// version (explicitly out of scope). Uses the SAME /api/cleaning?date=...
// endpoint and merged room data as the print/cleaning page — see
// test/sameEndpoint.test.mjs, which asserts both pages fetch the identical
// URL with no divergent client-side filtering.
import { renderMobileCleaningRooms } from "../cleaningSheetTemplate.js";
import { assertNoFinancialKeys } from "../financialGuard.js";
import { todayJst, formatDateJp } from "../jst.js";

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

  if (!cleaning) {
    freshnessEl.textContent = "データを取得できませんでした";
    listEl.innerHTML = `<div class="mc-empty">対象日の清掃データがありません</div>`;
    return;
  }

  const rooms = Array.isArray(cleaning.rooms) ? cleaning.rooms : [];
  listEl.innerHTML = renderMobileCleaningRooms(rooms);
}

main();
