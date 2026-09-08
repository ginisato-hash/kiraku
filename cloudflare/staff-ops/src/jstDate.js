// jstDate.js — server-side "today in JST" helper for worker.js only.
//
// This intentionally duplicates public/jst.js's todayJst() rather than
// importing it: src/ is bundled into the Worker at deploy time and public/
// is served as a static asset to the browser — the two have never shared an
// import (see roomMaster.js/cleaningSheetTemplate.js comments on this
// boundary), and this one small pure function is not worth crossing it for.
// Any change here must be mirrored in public/jst.js's todayJst() by hand.

const JST_DATE_FORMATTER = new Intl.DateTimeFormat("sv-SE", { timeZone: "Asia/Tokyo" });

export function todayJst(refDate) {
  const d = refDate == null ? new Date() : (refDate instanceof Date ? refDate : new Date(refDate));
  return JST_DATE_FORMATTER.format(d);
}
