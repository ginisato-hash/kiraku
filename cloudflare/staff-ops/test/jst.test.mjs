// jst.js のテスト：UTC日付境界を跨ぐ時刻でもJST基準の「今日」が正しく出ること。
// wall-clockに依存せず、明示的な参照時刻(refDate)を渡してテストする
// (bi-webのformatFreshnessがnowMsを明示的に受け取るのと同じ設計方針)。
import assert from "node:assert";
import { todayJst, addDaysToDateString, formatDateJp, formatJapaneseDateWithWeekday } from "../public/jst.js";

let passed = 0;
async function check(name, fn) { await fn(); passed++; console.log("ok -", name); }

await check("todayJst: UTC 20:00 on Aug 30 is already Aug 31 in JST (UTC+9 crosses midnight)", async () => {
  const ref = new Date("2026-08-30T20:00:00Z");
  assert.equal(todayJst(ref), "2026-08-31");
});

await check("todayJst: UTC 10:00 on Aug 30 is still Aug 30 in JST (19:00 JST, same day)", async () => {
  const ref = new Date("2026-08-30T10:00:00Z");
  assert.equal(todayJst(ref), "2026-08-30");
});

await check("todayJst: accepts an epoch-ms number as well as a Date", async () => {
  const ref = new Date("2026-08-30T20:00:00Z");
  assert.equal(todayJst(ref.getTime()), "2026-08-31");
});

await check("todayJst: defaults to the real current time when called with no argument", async () => {
  const result = todayJst();
  assert.match(result, /^\d{4}-\d{2}-\d{2}$/);
});

await check("addDaysToDateString: adds/subtracts days without timezone drift", async () => {
  assert.equal(addDaysToDateString("2026-08-30", 1), "2026-08-31");
  assert.equal(addDaysToDateString("2026-08-30", -1), "2026-08-29");
  assert.equal(addDaysToDateString("2026-08-31", 1), "2026-09-01"); // month rollover
});

await check("formatDateJp renders Japanese date and handles bad input", async () => {
  assert.equal(formatDateJp("2026-08-30"), "2026年8月30日");
  assert.equal(formatDateJp("not-a-date"), "—");
});

await check("formatJapaneseDateWithWeekday renders '2026年8月29日, 土曜日' (guest register stay-date format)", async () => {
  assert.equal(formatJapaneseDateWithWeekday("2026-08-29"), "2026年8月29日, 土曜日");
  assert.equal(formatJapaneseDateWithWeekday("2026-08-30"), "2026年8月30日, 日曜日");
  assert.equal(formatJapaneseDateWithWeekday("2026-09-01"), "2026年9月1日, 火曜日");
});

await check("formatJapaneseDateWithWeekday handles bad input without throwing", async () => {
  assert.equal(formatJapaneseDateWithWeekday("not-a-date"), "");
  assert.equal(formatJapaneseDateWithWeekday(null), "");
});

console.log(`\n${passed} jst checks passed`);
