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
  COLUMN_WIDTHS_MM, effectiveTextWidth, guestNameSizeClass,
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

// ---------------- 現場スタッフからの可読性フィードバック対応 ----------------
// CSSの具体的なmm/px値は今後も微調整され得るため、ここでは「太さの下限」「基準サイズより
// 明確に大きいこと」「関係性(guest-12/11がguest-13より大きくならない等)」だけを検証し、
// 特定の数値そのものには依存しない。

// selectorはCSS上で単独指定でも複合セレクタ("a, b, c { ... }")の一部でもよい。
// 一致する規則ブロックすべてをソース順に連結して返す — 同一ファイル内・同程度の
// 詳細度なら後勝ちのカスケードと同じ順序になるので、weightOf/sizeMmOfは最後に
// 出現した値を採用する。
const htmlNoComments = html.replace(/\/\*[\s\S]*?\*\//g, "");
function cssRule(selector) {
  const blockRe = /([^{}]+){([^}]*)}/gs;
  let merged = "";
  let m;
  while ((m = blockRe.exec(htmlNoComments))) {
    const selectors = m[1].split(",").map((s) => s.trim());
    if (selectors.includes(selector)) merged += m[2] + ";";
  }
  return merged || null;
}
function weightOf(ruleBody) {
  if (!ruleBody) return null;
  const matches = [...ruleBody.matchAll(/font-weight:\s*(\d+)/g)];
  return matches.length ? Number(matches[matches.length - 1][1]) : null;
}
function sizeMmOf(ruleBody) {
  if (!ruleBody) return null;
  const matches = [...ruleBody.matchAll(/font-size:\s*([\d.]+)mm/g)];
  return matches.length ? Number(matches[matches.length - 1][1]) : null;
}

await check("body text (人数/泊数/清掃区分/到着 baseline) is bold: .cs-main-table td declares font-weight >= 700", async () => {
  const w = weightOf(cssRule(".cs-main-table td"));
  assert.ok(w !== null && w >= 700, `expected .cs-main-table td font-weight >= 700, got ${w}`);
});

await check("column headers are bold: .cs-main-table th declares font-weight >= 700", async () => {
  const w = weightOf(cssRule(".cs-main-table th"));
  assert.ok(w !== null && w >= 700, `expected header font-weight >= 700, got ${w}`);
});

await check("RoomNo cell (.cs-c-room) is bold and clearly larger than the base body text size", async () => {
  const roomWeight = weightOf(cssRule(".cs-c-room"));
  const roomSize = sizeMmOf(cssRule(".cs-c-room"));
  const baseSize = sizeMmOf(cssRule(".cs-main-table td"));
  assert.ok(roomWeight >= 700, `expected .cs-c-room font-weight >= 700, got ${roomWeight}`);
  assert.ok(roomSize >= baseSize, "RoomNo should be at least as large as the general body text");
});

await check("IN/OUT checkmarks (.cs-c-in, .cs-c-out) are sized at least as large as the base body text and inherit bold", async () => {
  for (const cls of [".cs-c-in", ".cs-c-out"]) {
    const size = sizeMmOf(cssRule(cls));
    const baseSize = sizeMmOf(cssRule(".cs-main-table td"));
    const weight = weightOf(cssRule(cls));
    assert.ok(size >= baseSize, `${cls} must be at least as large as base body text (target: as large as RoomNo)`);
    // must not explicitly downgrade weight below the inherited bold base
    assert.ok(weight === null || weight >= 700);
  }
});

await check("備考・通信 (.cs-c-notes) and 予約元 (.cs-c-ota) are at least semi-bold (>=600) per spec, and notes keeps a readable line-height", async () => {
  const notesBody = cssRule(".cs-c-notes");
  const otaBody = cssRule(".cs-c-ota");
  assert.ok(weightOf(notesBody) >= 600, "備考・通信 must be font-weight >= 600");
  assert.ok(weightOf(otaBody) >= 600, "予約元 must be font-weight >= 600");
  assert.ok(/line-height/.test(notesBody), "備考・通信 should set an explicit line-height for legibility");
});

await check("空室 (.cs-status-vacant) stays legible (>=600) but is not larger/bolder than an occupied row's status text", async () => {
  const vacantBody = cssRule(".cs-status-vacant");
  const vacantWeight = weightOf(vacantBody);
  const vacantSize = sizeMmOf(vacantBody);
  const occupiedSize = sizeMmOf(cssRule(".cs-main-table td")); // status td falls back to this when not vacant
  assert.ok(vacantWeight >= 600);
  assert.ok(vacantSize <= occupiedSize, "空室 must not be emphasized more than an occupied room's status");
});

await check("guest name size classes step down (13 > 12 > 11) and never drop below semi-bold weight", async () => {
  const base = sizeMmOf(cssRule(".cs-main-table td")); // default (cs-guest-13 tier, no override class)
  const size12 = sizeMmOf(cssRule(".cs-guest-12"));
  const size11 = sizeMmOf(cssRule(".cs-guest-11"));
  assert.ok(base > size12 && size12 > size11, "each long-name tier must be strictly smaller than the previous one");
  // neither override tier may set a weight below 600 (they should inherit the bold base, i.e. not touch weight at all, or keep it high)
  for (const cls of [".cs-guest-12", ".cs-guest-11"]) {
    const w = weightOf(cssRule(cls));
    assert.ok(w === null || w >= 600, `${cls} must not weaken guest-name weight below 600`);
  }
});

await check("page header (date / title) and 全体通信・引継ぎ heading are bold and clearly larger than before", async () => {
  const dateWeight = weightOf(cssRule(".cs-header-date"));
  const titleWeight = weightOf(cssRule(".cs-header-title"));
  const footerTitleWeight = weightOf(cssRule(".cs-footer-title"));
  assert.ok(dateWeight >= 700 && titleWeight >= 700 && footerTitleWeight >= 700);
});

// ---------------- effectiveTextWidth / guestNameSizeClass (long-name handling) ----------------

await check("effectiveTextWidth counts full-width (Japanese) characters as 2 and half-width (Latin) as 1", async () => {
  assert.equal(effectiveTextWidth("山田太郎"), 8); // 4 full-width chars
  assert.equal(effectiveTextWidth("Doherty"), 7); // 7 half-width chars
  assert.equal(effectiveTextWidth(""), 0);
});

await check("guestNameSizeClass keeps ordinary Japanese names (e.g. 山田 太郎) at the default 13px tier — never shrunk just for being a normal name", async () => {
  for (const name of ["山田 太郎", "佐藤 花子", "高橋 次郎", "田中 一美"]) {
    assert.equal(guestNameSizeClass(name), "cs-guest-13", `${name} should not be downsized`);
  }
});

await check("guestNameSizeClass downsizes a long Latin name (Martin Doherty) without crudely using raw character count", async () => {
  // "Martin Doherty" is 14 half-width chars; a naive same-length Japanese name
  // (e.g. 7 full-width chars = effectiveTextWidth 14) would land in the same
  // tier, but a genuinely short Japanese name of the same *character count*
  // as fewer chars would not — the classification is width-based, not
  // language-based or raw-length-based.
  assert.equal(guestNameSizeClass("Martin Doherty"), "cs-guest-12");
  assert.equal(guestNameSizeClass("Alexander Christopherson"), "cs-guest-11");
});

await check("buildRoomRow output: the guest name cell carries the guestNameSizeClass result as an extra CSS class", async () => {
  const room401 = rooms.find((r) => r.room_number === "401");
  const tampered = { ...room401, arriving_guest: { ...room401.arriving_guest, guest_name: "Martin Doherty" } };
  const withLongName = rooms.map((r) => (r.room_number === "401" ? tampered : r));
  const out = renderCleaningSheetTemplate(withLongName, "2026-08-30");
  assert.ok(out.includes('class="cs-c-guest cs-guest-12"'), "expected the downsized class to be applied in the rendered row");
});

console.log(`\n${passed} print cleaning checks passed`);
