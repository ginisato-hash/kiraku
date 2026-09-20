// jstDate.js — server-side "today in JST" helper for the BI refresh coordinator.
//
// Mirrors cloudflare/staff-ops/src/jstDate.js exactly (same small pure
// function, intentionally duplicated rather than shared across the two
// independent Worker projects — see that file's comment for why).

const JST_DATE_FORMATTER = new Intl.DateTimeFormat("sv-SE", { timeZone: "Asia/Tokyo" });

export function todayJst(refDate) {
  const d = refDate == null ? new Date() : (refDate instanceof Date ? refDate : new Date(refDate));
  return JST_DATE_FORMATTER.format(d);
}
