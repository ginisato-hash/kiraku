// print-guest-register.js — bootstrap for /ops/print/guest-register.
// Fetches /api/daily-ops?date=..., renders one page per arrival via the pure
// buildGuestRegisterDocumentHtml() template, waits for fonts + the logo
// image to settle, then calls window.print(). Never navigates away or
// destroys content on afterprint, so the page stays usable if printing is
// cancelled.
import { buildGuestRegisterDocumentHtml } from "../../guestRegisterTemplate.js";
import { assertNoFinancialKeys } from "../../financialGuard.js";
import { waitForPrintReady } from "../../printUtils.js";
import { todayJst } from "../../jst.js";

const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

function getDate() {
  const d = new URLSearchParams(window.location.search).get("date");
  return d && DATE_RE.test(d) ? d : todayJst();
}

async function main() {
  const root = document.getElementById("grf-root");
  const date = getDate();

  let dayData = null;
  try {
    const res = await fetch(`/api/daily-ops?date=${encodeURIComponent(date)}`, { cache: "no-store" });
    if (res.ok) {
      dayData = await res.json();
      assertNoFinancialKeys(dayData);
    }
  } catch (e) {
    dayData = null;
  }

  const arrivals = (dayData && Array.isArray(dayData.arrivals)) ? dayData.arrivals : [];

  if (!arrivals.length) {
    root.innerHTML = `<p class="no-print">本日のチェックインはありません（印刷対象なし）。</p>`;
    return;
  }

  root.innerHTML = buildGuestRegisterDocumentHtml(arrivals);

  const logoImg = root.querySelector("img");
  await waitForPrintReady(logoImg);
  window.print();
}

// afterprint (fired whether the user prints or cancels) must never navigate
// away or blank the page — the guest register stays visible/usable so staff
// can retry printing if needed.
window.addEventListener("afterprint", () => {
  // Intentionally a no-op: keep the rendered document in place.
});

main();
