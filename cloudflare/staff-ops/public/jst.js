// jst.js — the ONE JST "today" definition for the whole Daily Ops app.
// Do not use bare `new Date()` local-timezone math anywhere else in this
// app (index/dailyOps.js, print pages, mobile page) — always go through
// todayJst() so a device/browser in any timezone still agrees on what
// "today" means for this hotel (Asia/Tokyo).

const JST_DATE_FORMATTER = new Intl.DateTimeFormat("sv-SE", { timeZone: "Asia/Tokyo" });

// refDate is an optional Date object or epoch-ms number, purely for
// testability (mirrors the nowMs-parameter pattern used by bi-web's
// formatFreshness) — defaults to the real current instant.
// sv-SE locale formats a plain date as "YYYY-MM-DD", which is exactly the
// ISO date string this app uses everywhere.
export function todayJst(refDate) {
  const d = refDate == null ? new Date() : (refDate instanceof Date ? refDate : new Date(refDate));
  return JST_DATE_FORMATTER.format(d);
}

// Adds `days` (can be negative) to a "YYYY-MM-DD" date string, returning a
// new "YYYY-MM-DD" string. Pure date arithmetic done at UTC noon so no
// timezone/DST edge case can shift the calendar date.
export function addDaysToDateString(dateStr, days) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(dateStr);
  if (!m) return dateStr;
  const [, y, mo, d] = m;
  const dt = new Date(Date.UTC(Number(y), Number(mo) - 1, Number(d), 12, 0, 0));
  dt.setUTCDate(dt.getUTCDate() + days);
  const yy = dt.getUTCFullYear();
  const mm = String(dt.getUTCMonth() + 1).padStart(2, "0");
  const dd = String(dt.getUTCDate()).padStart(2, "0");
  return `${yy}-${mm}-${dd}`;
}

// "2026-08-30" -> "2026年8月30日"
export function formatDateJp(dateStr) {
  const m = typeof dateStr === "string" ? /^(\d{4})-(\d{2})-(\d{2})/.exec(dateStr) : null;
  if (!m) return "—";
  return `${Number(m[1])}年${Number(m[2])}月${Number(m[3])}日`;
}

const WEEKDAY_JA = ["日", "月", "火", "水", "木", "金", "土"];

// "2026-08-29" -> "2026年8月29日, 土曜日". Same UTC-noon-anchor date-string
// parsing as addDaysToDateString/todayJst above (these are plain calendar
// date strings, not instants — weekday is derived deterministically from
// the string itself, no additional timezone conversion involved).
export function formatJapaneseDateWithWeekday(dateStr) {
  const m = typeof dateStr === "string" ? /^(\d{4})-(\d{2})-(\d{2})/.exec(dateStr) : null;
  if (!m) return "";
  const [, y, mo, d] = m;
  const dt = new Date(Date.UTC(Number(y), Number(mo) - 1, Number(d), 12, 0, 0));
  const weekday = WEEKDAY_JA[dt.getUTCDay()];
  return `${Number(y)}年${Number(mo)}月${Number(d)}日, ${weekday}曜日`;
}
