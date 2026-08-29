// モバイル清掃ビュー(/cleaning/today)のテスト：renderMobileCleaningRooms純粋関数 +
// today.html/today.jsのソース内容チェック（viewport meta / font-size / today-JST default）。
import assert from "node:assert";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { renderMobileCleaningRooms } from "../public/cleaningSheetTemplate.js";

const dir = path.dirname(fileURLToPath(import.meta.url));
const fixture = JSON.parse(readFileSync(path.join(dir, "fixtures/staff_ops_snapshot.sample.json"), "utf-8"));
const rooms = fixture.dates["2026-08-30"].cleaning.rooms;
const html = readFileSync(path.join(dir, "../public/cleaning/today.html"), "utf-8");
const js = readFileSync(path.join(dir, "../public/cleaning/today.js"), "utf-8");

let passed = 0;
async function check(name, fn) { await fn(); passed++; console.log("ok -", name); }

await check("renderMobileCleaningRooms shows room type/state prominently for each room", async () => {
  const out = renderMobileCleaningRooms(rooms);
  assert.ok(out.includes("mc-room-card"));
  assert.ok(out.includes("引継ぎ清掃")); // TURNOVER label for the first room
});

await check("renderMobileCleaningRooms on empty list shows a readable empty state", async () => {
  const out = renderMobileCleaningRooms([]);
  assert.ok(out.includes("対象日の清掃データがありません"));
});

await check("renderMobileCleaningRooms omits notes line entirely when notes is null (not a blank 備考: line)", async () => {
  const out = renderMobileCleaningRooms(rooms);
  // none of the fixture rooms have notes set, so no "備考:" text should appear at all
  assert.ok(!out.includes("備考:"));
});

await check("today.html declares a viewport meta tag", async () => {
  assert.ok(/<meta name="viewport" content="width=device-width, initial-scale=1\.0"/.test(html));
});

await check("today.html base font-size is not tiny (>= 16px)", async () => {
  const m = html.match(/body\s*{\s*font-size:\s*(\d+)px/);
  assert.ok(m, "expected an explicit body font-size in px");
  assert.ok(Number(m[1]) >= 16, `font-size ${m[1]}px is too small for mobile cleaning staff`);
});

await check("today.html room label font-size is larger than the base (most prominent element per row)", async () => {
  const m = html.match(/\.mc-room-label\s*{\s*font-size:\s*([\d.]+)rem/);
  assert.ok(m);
  assert.ok(Number(m[1]) > 1, "room label should be larger than 1rem base");
});

await check("today.js defaults to todayJst() when no ?date= param is present", async () => {
  assert.ok(js.includes("todayJst()"));
  assert.ok(js.includes("DATE_RE.test(d) ? d : todayJst()"));
});

await check("today.js fetches /api/cleaning?date=... (same endpoint as the print page) with no divergent filtering", async () => {
  assert.ok(js.includes("/api/cleaning?date="));
  // no extra query params or client-side room filtering beyond the merged list
  assert.ok(!/\.filter\(/.test(js), "today.js must not apply its own client-side room filtering");
});

await check("today.js has no start/complete/inspected status buttons (out of scope for this version)", async () => {
  // Read-only display only: no button elements and no status-mutation calls
  // (a comment noting this is out of scope is fine; actual UI wiring is not).
  assert.ok(!/<button/i.test(js));
  assert.ok(!/addEventListener\(\s*["']click["']/.test(js));
  assert.ok(!/POST/.test(js), "today.js must not POST any status change");
});

console.log(`\n${passed} mobile cleaning checks passed`);
