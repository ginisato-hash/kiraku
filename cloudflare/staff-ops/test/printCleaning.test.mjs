// cleaningSheetTemplate.js の純粋関数テスト + cleaning.html（印刷ページ、FINAL設計）の
// ソース内容確認。renderCleaningSheetTemplateへ渡すroomsは、実運用と同じく
// mergeCleaningOverrides()を通した後の形(effectiveInstruction等が付与済み)を使う。
import assert from "node:assert";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import {
  renderCleaningSheetTemplate, statusLabel, otaPrintShortName, cleanValue,
  guestNameFor, nightProgressFor, arrivalTimeFor, inMark, outMark, countUnassigned,
  COLUMN_WIDTHS_MM,
} from "../public/cleaningSheetTemplate.js";
import { mergeCleaningOverrides } from "../src/cleaningOverrides.js";

const dir = path.dirname(fileURLToPath(import.meta.url));
const fixture = JSON.parse(readFileSync(path.join(dir, "fixtures/staff_ops_snapshot.sample.json"), "utf-8"));
const rawRooms = fixture.dates["2026-08-30"].cleaning.rooms;
const rooms = mergeCleaningOverrides(rawRooms, {}); // no overrides -> effectiveInstruction=source_instruction
const html = readFileSync(path.join(dir, "../public/ops/print/cleaning.html"), "utf-8");

const KIRAKU_ROOM_ORDER = [
  "401", "402", "403", "404", "405", "406",
  "501", "502", "503", "504", "505", "507",
  "601", "602", "603", "604", "605", "607",
];

let passed = 0;
async function check(name, fn) { await fn(); passed++; console.log("ok -", name); }

const documentHtml = renderCleaningSheetTemplate(rooms, "2026-08-30");

await check("renders all 18 rooms in canonical order with data-room-number attributes", async () => {
  const matches = [...documentHtml.matchAll(/data-room-number="(\d+)"/g)].map((m) => m[1]);
  assert.deepEqual(matches, KIRAKU_ROOM_ORDER);
});

await check("TURNOVER row (401) shows the arriving guest's name only, never 'A → B'", async () => {
  assert.ok(documentHtml.includes("山田 太郎"));
  assert.ok(!documentHtml.includes("鈴木 一郎"), "departing guest name must not appear on a TURNOVER row");
  assert.ok(!/山田 太郎\s*(→|->)\s*鈴木|鈴木 一郎\s*(→|->)\s*山田/.test(documentHtml));
});

await check("CHECKIN (402) and TURNOVER (401) rows show an IN checkmark; STAYOVER (404) does not", async () => {
  const checkinRoom = rooms.find((r) => r.room_number === "402");
  const turnoverRoom = rooms.find((r) => r.room_number === "401");
  const stayoverRoom = rooms.find((r) => r.room_number === "404");
  assert.equal(inMark(checkinRoom), "✓");
  assert.equal(inMark(turnoverRoom), "✓");
  assert.equal(inMark(stayoverRoom), "");
});

await check("CHECKOUT (403) and TURNOVER (401) rows show an OUT checkmark; STAYOVER (404) does not", async () => {
  const checkoutRoom = rooms.find((r) => r.room_number === "403");
  const turnoverRoom = rooms.find((r) => r.room_number === "401");
  const stayoverRoom = rooms.find((r) => r.room_number === "404");
  assert.equal(outMark(checkoutRoom), "✓");
  assert.equal(outMark(turnoverRoom), "✓");
  assert.equal(outMark(stayoverRoom), "");
});

await check("nightProgressFor formats 'idx/total' straight from the DTO (no recomputation)", async () => {
  const turnoverRoom = rooms.find((r) => r.room_number === "401");
  assert.equal(nightProgressFor(turnoverRoom), "1/2");
  const stayoverRoom = rooms.find((r) => r.room_number === "404");
  assert.equal(nightProgressFor(stayoverRoom), "2/4");
});

await check("arrivalTimeFor is blank (not 'None'/'null') when arrival_time is absent, shown when present", async () => {
  const turnoverRoom = rooms.find((r) => r.room_number === "401"); // arrival_time: null
  assert.equal(arrivalTimeFor(turnoverRoom), "");
  const checkinRoom = rooms.find((r) => r.room_number === "402"); // arrival_time: "15:00"
  assert.equal(arrivalTimeFor(checkinRoom), "15:00");
  assert.ok(!documentHtml.includes(">None<"));
  assert.ok(!/\bnull\b|\bundefined\b/i.test(documentHtml));
});

await check("otaPrintShortName maps Booking.com/楽天トラベル/じゃらん to their print-short forms", async () => {
  assert.equal(otaPrintShortName("Booking.com"), "Booking");
  assert.equal(otaPrintShortName("楽天トラベル"), "楽天");
  assert.equal(otaPrintShortName("じゃらん"), "じゃらん");
});

await check("guestNameFor never returns a guest name for a VACANT room", async () => {
  const vacantRoom = rooms.find((r) => r.room_number === "405");
  assert.equal(guestNameFor(vacantRoom), "");
});

await check("statusLabel maps all contract statuses to their Japanese labels", async () => {
  assert.equal(statusLabel("CHECKIN"), "IN");
  assert.equal(statusLabel("CHECKOUT"), "OUT");
  assert.equal(statusLabel("STAYOVER"), "連泊");
  assert.equal(statusLabel("TURNOVER"), "入替");
  assert.equal(statusLabel("VACANT"), "空室");
  assert.equal(statusLabel("CANCELLED"), "空室");
});

await check("no メニュー/ランク/料金 column or '本日のメニュー' text anywhere in the rendered HTML", async () => {
  assert.ok(!/メニュー|ランク|料金/.test(documentHtml));
});

await check("no メニュー/ランク/料金/本日のメニュー text in the static cleaning.html either", async () => {
  assert.ok(!/メニュー|ランク|料金/.test(html));
});

await check("全体通信・引継ぎ box is present", async () => {
  assert.ok(documentHtml.includes("全体通信・引継ぎ"));
});

await check("UNASSIGNED count warning appears with a count only (no guest name) when count > 0", async () => {
  assert.equal(countUnassigned(rooms), 1);
  assert.ok(documentHtml.includes("未割当予約あり：1件"));
  assert.ok(!documentHtml.includes("中村 光"), "UNASSIGNED guest name must never appear in the warning line");
});

await check("UNASSIGNED warning is absent entirely when count is 0", async () => {
  const noUnassigned = rooms.filter((r) => r.room_number !== null);
  const out = renderCleaningSheetTemplate(noUnassigned, "2026-08-30");
  assert.ok(!out.includes("未割当予約あり"));
});

await check("cleaning.html no longer contains the old '仮レイアウト' provisional banner", async () => {
  assert.ok(!html.includes("仮レイアウト"));
});

await check("cleaning.html declares @page size A4 portrait margin 7mm", async () => {
  assert.ok(/@page\s*{[^}]*size:\s*A4\s+portrait/s.test(html));
  assert.ok(/@page\s*{[^}]*margin:\s*7mm/s.test(html));
});

await check("cleaning.html declares the exact .cleaning-sheet dimensions (196mm x 283mm)", async () => {
  assert.ok(/\.cleaning-sheet\s*{[^}]*width:\s*196mm/s.test(html));
  assert.ok(/\.cleaning-sheet\s*{[^}]*height:\s*283mm/s.test(html));
});

// ---------------- RoomNo header fit + 備考・通信 inner-border removal ----------------

await check("COLUMN_WIDTHS_MM still sums to 196mm (A4 one-page layout unchanged) with the RoomNo column widened", async () => {
  assert.equal(COLUMN_WIDTHS_MM.reduce((a, b) => a + b, 0), 196);
  assert.equal(COLUMN_WIDTHS_MM[0], 15, "RoomNo column must be wide enough for the header label to fit");
  assert.equal(COLUMN_WIDTHS_MM[9], 54, "the 2mm added to RoomNo comes out of 備考・通信, which stays the widest column");
});

await check("cleaning.html header cells force white-space: nowrap (RoomNo must never wrap/clip)", async () => {
  assert.ok(/\.cs-main-table th\s*{[^}]*white-space:\s*nowrap/s.test(html));
});

await check("cleaning.html's .cs-c-notes no longer overrides display away from table-cell (this broke the cell's row-height fill and looked like a stray inner border)", async () => {
  const m = html.match(/\.cs-c-notes\s*{([^}]*)}/s);
  assert.ok(m, "expected a .cs-c-notes rule");
  const body = m[1];
  assert.ok(!/display\s*:/.test(body), "must not set display on .cs-c-notes (needs the UA's table-cell)");
  assert.ok(!/-webkit-line-clamp/.test(body));
  assert.ok(!/-webkit-box-orient/.test(body));
});

console.log(`\n${passed} print cleaning checks passed`);
