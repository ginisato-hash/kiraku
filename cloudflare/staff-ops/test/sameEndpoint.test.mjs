// 清掃データを消費する2つのページ（印刷ページ print-cleaning.js とモバイル今日ビュー
// today.js）が、まったく同じ /api/cleaning?date=... エンドポイントを叩き、
// クライアント側での分岐フィルタリングを行っていないことを確認する。
// これにより、KV上書き(room_number/notes)が両方のページで必ず一致して表示される。
import assert from "node:assert";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const dir = path.dirname(fileURLToPath(import.meta.url));
const printJs = readFileSync(path.join(dir, "../public/ops/print/print-cleaning.js"), "utf-8");
const mobileJs = readFileSync(path.join(dir, "../public/cleaning/today.js"), "utf-8");

let passed = 0;
async function check(name, fn) { await fn(); passed++; console.log("ok -", name); }

const FETCH_URL_RE = /fetch\(`\/api\/cleaning\?date=\$\{encodeURIComponent\(date\)\}`/;

await check("print-cleaning.js fetches the exact same /api/cleaning?date= URL construction", async () => {
  assert.ok(FETCH_URL_RE.test(printJs), "print-cleaning.js should fetch `/api/cleaning?date=${encodeURIComponent(date)}`");
});

await check("today.js (mobile) fetches the exact same /api/cleaning?date= URL construction", async () => {
  assert.ok(FETCH_URL_RE.test(mobileJs), "today.js should fetch `/api/cleaning?date=${encodeURIComponent(date)}`");
});

await check("neither page applies its own client-side room filtering after the fetch", async () => {
  assert.ok(!/\.filter\(/.test(printJs), "print-cleaning.js must not filter rooms client-side");
  assert.ok(!/\.filter\(/.test(mobileJs), "today.js must not filter rooms client-side");
});

await check("both pages read rooms the same way: cleaning.rooms with an Array.isArray guard, nothing else", async () => {
  const roomsAccessRe = /Array\.isArray\(cleaning\.rooms\)\)?\s*\?\s*cleaning\.rooms\s*:\s*\[\]/;
  assert.ok(roomsAccessRe.test(printJs), "print-cleaning.js should read rooms via an Array.isArray(cleaning.rooms) guard");
  assert.ok(roomsAccessRe.test(mobileJs), "today.js should read rooms via an Array.isArray(cleaning.rooms) guard");
});

await check("both pages pass the merged rooms straight into their render function with no intermediate transform", async () => {
  assert.ok(/renderCleaningSheetTemplate\(rooms, date\)/.test(printJs));
  assert.ok(/renderMobileCleaningRooms\(rooms\)/.test(mobileJs));
});

console.log(`\n${passed} same-endpoint checks passed`);
