// cleaningSheetTemplate.js の純粋関数テスト + cleaning.html（印刷ページ）の
// ソース内容確認。renderCleaningSheetTemplateへ渡すroomsは、実運用と同じく
// mergeCleaningOverrides()を通した後の形(effectiveInstruction等が付与済み)を使う。
//
// 2026-09改訂: OUT列削除・ステータス1列化(IN/連泊のみ)・人数の大人/子供内訳・
// お客様からのお知らせ・現地決済列を追加した新9列レイアウトのテスト。
import assert from "node:assert";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import {
  renderCleaningSheetTemplate, statusLabel, printStatusLabel, otaPrintShortName, cleanValue,
  guestNameFor, guestBreakdownFor, guestNoticeFor, onsiteInfoFor,
  nightProgressFor, arrivalTimeFor, countUnassigned, isFloorStartRoom,
  COLUMN_WIDTHS_MM, COLUMN_LABELS, effectiveTextWidth, guestNameSizeClass,
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

await check("statusLabel (Staff cleaning list専用のフル表示) is unchanged: IN/OUT/連泊/入替/空室", async () => {
  assert.equal(statusLabel("CHECKIN"), "IN");
  assert.equal(statusLabel("CHECKOUT"), "OUT");
  assert.equal(statusLabel("STAYOVER"), "連泊");
  assert.equal(statusLabel("TURNOVER"), "入替");
  assert.equal(statusLabel("VACANT"), "空室");
  assert.equal(statusLabel("CANCELLED"), "空室");
});

await check("printStatusLabel (印刷/モバイル専用) collapses to only IN/連泊/blank per spec", async () => {
  assert.equal(printStatusLabel("CHECKIN"), "IN");
  assert.equal(printStatusLabel("TURNOVER"), "IN", "TURNOVER counts as IN (next guest arriving today matters)");
  assert.equal(printStatusLabel("STAYOVER"), "連泊");
  assert.equal(printStatusLabel("CHECKOUT"), "");
  assert.equal(printStatusLabel("VACANT"), "");
  assert.equal(printStatusLabel("CANCELLED"), "");
});

await check("the print document never shows 'OUT'/'入替'/'空室' as status text (only IN/連泊/blank)", async () => {
  // 401=TURNOVER->IN, 402=CHECKIN->IN, 403=CHECKOUT->blank, 404=STAYOVER->連泊
  const statusCells = [...documentHtml.matchAll(/<td class="cs-c-status">([^<]*)<\/td>/g)].map((m) => m[1]);
  assert.equal(statusCells.length, 18);
  for (const text of statusCells) {
    assert.ok(text === "IN" || text === "連泊" || text === "", `unexpected status text: "${text}"`);
  }
  assert.ok(statusCells.includes("IN"));
  assert.ok(statusCells.includes("連泊"));
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

// ---------------- 新9列レイアウト（OUT削除・ステータス1列・現地決済追加）----------------

await check("9 columns, OUT column fully removed (header and body), RoomNo/人数/泊数 widened, sums to 196mm", async () => {
  assert.equal(COLUMN_WIDTHS_MM.length, 9);
  assert.equal(COLUMN_LABELS.length, 9);
  assert.deepEqual(COLUMN_LABELS, [
    "RoomNo", "お客様名", "人数", "泊数", "ステータス", "到着", "予約元", "現地決済", "備考・通信",
  ]);
  assert.ok(!COLUMN_LABELS.includes("IN"));
  assert.ok(!COLUMN_LABELS.includes("OUT"));
  assert.equal(COLUMN_WIDTHS_MM.reduce((a, b) => a + b, 0), 196);
  assert.ok(!documentHtml.includes(">OUT<"), "OUT header text must not appear anywhere");
  assert.ok(!documentHtml.includes('class="cs-c-out"'), "no OUT body cell must be rendered");
  assert.ok(!documentHtml.includes('class="cs-c-in"'), "no separate IN body cell either (folded into cs-c-status)");
});

await check("RoomNo/人数/泊数 column widths were not shrunk relative to the previous readability-pass values (16/21/14mm)", async () => {
  const idx = { room: 0, guest: 1, count: 2, nights: 3 };
  assert.ok(COLUMN_WIDTHS_MM[idx.room] >= 16, "RoomNo column must not be narrowed");
  assert.ok(COLUMN_WIDTHS_MM[idx.count] >= 21, "人数 column must not be narrowed");
  assert.ok(COLUMN_WIDTHS_MM[idx.nights] >= 14, "泊数 column must not be narrowed");
});

await check("cleaning.html header cells force white-space: nowrap (RoomNo must never wrap/clip)", async () => {
  assert.ok(/\.cs-main-table th\s*{[^}]*white-space:\s*nowrap/s.test(html));
});

await check("cleaning.html's .cs-c-notes still does not override display away from table-cell", async () => {
  const m = html.match(/\.cs-c-notes\s*{([^}]*)}/s);
  assert.ok(m, "expected a .cs-c-notes rule");
  const body = m[1];
  assert.ok(!/display\s*:/.test(body), "must not set display on .cs-c-notes (needs the UA's table-cell)");
});

// ---------------- 4F/5F/6Fの階境界（太罫線）----------------

await check("isFloorStartRoom is true only for 501 and 601 (room-number based, not index-based)", async () => {
  const trueRooms = KIRAKU_ROOM_ORDER.filter(isFloorStartRoom);
  assert.deepEqual(trueRooms, ["501", "601"]);
});

await check("rows for 501 and 601 carry the cs-floor-start class; no other row does", async () => {
  const floorStartRows = [...documentHtml.matchAll(/<tr data-room-number="(\d+)" class="cs-floor-start">/g)].map((m) => m[1]);
  assert.deepEqual(floorStartRows, ["501", "601"]);
});

await check("cleaning.html declares a thicker top border specifically for .cs-floor-start rows", async () => {
  const m = html.match(/tr\.cs-floor-start\s*>\s*td\s*{([^}]*)}/s);
  assert.ok(m, "expected a tr.cs-floor-start > td rule");
  assert.ok(/border-top:\s*0\.\d+mm/.test(m[1]), "expected a border-top in fractional mm, clearly thicker than the default 1px table border");
});

// ---------------- 大人/子供 内訳 ----------------

await check("guestBreakdownFor: children=0 shows only 大N (never 子0)", async () => {
  const room = rooms.find((r) => r.room_number === "403"); // 田中一美, adults=1, children=0
  assert.equal(guestBreakdownFor(room), "大1");
});

await check("guestBreakdownFor: children>0 with no age data falls back to plain 子N", async () => {
  const room = rooms.find((r) => r.room_number === "401"); // 山田太郎 (arriving), adults=2, children=1
  assert.equal(guestBreakdownFor(room), "大2 子1");
});

await check("guestBreakdownFor: children>0 WITH children_age_data_available+count uses 子7+N form", async () => {
  const room = rooms.find((r) => r.room_number === "401");
  const withAge = {
    ...room,
    arriving_guest: { ...room.arriving_guest, children: 3, children_age_data_available: true, children_age_7plus_count: 2 },
  };
  assert.equal(guestBreakdownFor(withAge), "大2 子7+2");
});

await check("guestBreakdownFor: VACANT room (no priority guest) is blank", async () => {
  const room = rooms.find((r) => r.room_number === "405");
  assert.equal(guestBreakdownFor(room), "");
});

// ---------------- お客様からのお知らせ (guest_notice) ----------------

await check("guestNoticeFor reads guest_notice from the priority guest, cleaned via cleanValue", async () => {
  const room = rooms.find((r) => r.room_number === "401");
  assert.equal(guestNoticeFor(room), "到着が少し遅れます");
});

await check("備考・通信 cell shows '客: ...' for guest_notice and '指: ...' for the override/instruction, each on its own line", async () => {
  assert.ok(documentHtml.includes('客: 到着が少し遅れます'));
  assert.ok(documentHtml.includes('指: 入替')); // 401's source_instruction
});

await check("備考・通信 cell omits the '客:' line entirely when guest_notice is empty (not a blank line)", async () => {
  const room402Match = documentHtml.match(/data-room-number="402"[\s\S]*?<\/tr>/);
  assert.ok(room402Match, "expected to find room 402's row");
  assert.ok(!room402Match[0].includes("客:"), "402 has no guest_notice in the fixture, so no 客: line should render");
});

// ---------------- 現地決済 ----------------

await check("onsiteInfoFor shows amount only when onsite_payment_required AND a positive finite amount are present", async () => {
  const room401 = rooms.find((r) => r.room_number === "401");
  assert.deepEqual(onsiteInfoFor(room401), { show: true, amountText: "¥18,000" });

  const room402 = rooms.find((r) => r.room_number === "402"); // no onsite fields set
  assert.equal(onsiteInfoFor(room402).show, false);
});

await check("onsiteInfoFor never shows for a negative, zero, or non-finite amount even if onsite_payment_required is true", async () => {
  const room = rooms.find((r) => r.room_number === "402");
  for (const bad of [0, -100, NaN, Infinity]) {
    const tampered = { ...room, arriving_guest: { ...room.arriving_guest, onsite_payment_required: true, onsite_payment_amount: bad } };
    assert.equal(onsiteInfoFor(tampered).show, false, `amount ${bad} must not be shown`);
  }
});

await check("印刷ドキュメント: 現地決済セルは対象予約(401)だけ金額を表示し、対象外(402)は空セル", async () => {
  const room401Match = documentHtml.match(/data-room-number="401"[\s\S]*?<\/tr>/)[0];
  const room402Match = documentHtml.match(/data-room-number="402"[\s\S]*?<\/tr>/)[0];
  assert.ok(room401Match.includes("¥18,000"));
  assert.ok(room401Match.includes("現地"));
  assert.ok(!room402Match.includes("現地"));
});

await check("cleaning.html declares bold styling for the onsite label/amount (見落とし防止)", async () => {
  const labelM = html.match(/\.cs-onsite-label\s*{([^}]*)}/s);
  const amountM = html.match(/\.cs-onsite-amount\s*{([^}]*)}/s);
  assert.ok(labelM && /font-weight:\s*[7-9]\d\d/.test(labelM[1]));
  assert.ok(amountM && /font-weight:\s*[7-9]\d\d/.test(amountM[1]));
});

// ---------------- 現場フィードバック対応の可読性（既存分の回帰確認）----------------
// CSSの具体的なmm/px値は今後も微調整され得るため、ここでは「太さの下限」「基準サイズより
// 明確に大きいこと」「関係性(guest-12/11がguest-13より大きくならない等)」だけを検証し、
// 特定の数値そのものには依存しない。

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

await check("body text baseline is bold: .cs-main-table td declares font-weight >= 700", async () => {
  const w = weightOf(cssRule(".cs-main-table td"));
  assert.ok(w !== null && w >= 700, `expected .cs-main-table td font-weight >= 700, got ${w}`);
});

await check("column headers are bold: .cs-main-table th declares font-weight >= 700", async () => {
  const w = weightOf(cssRule(".cs-main-table th"));
  assert.ok(w !== null && w >= 700, `expected header font-weight >= 700, got ${w}`);
});

await check("RoomNo (.cs-c-room) is very bold (>=800) and clearly larger than the base body text size", async () => {
  const roomWeight = weightOf(cssRule(".cs-c-room"));
  const roomSize = sizeMmOf(cssRule(".cs-c-room"));
  const baseSize = sizeMmOf(cssRule(".cs-main-table td"));
  assert.ok(roomWeight >= 800, `expected .cs-c-room font-weight >= 800, got ${roomWeight}`);
  assert.ok(roomSize > baseSize, "RoomNo should be clearly larger than the general body text");
});

await check("人数上段(.cs-total-guests)は非常に大きく(>=800)、下段(.cs-guest-breakdown)より明確に大きい", async () => {
  const totalWeight = weightOf(cssRule(".cs-total-guests"));
  const totalSize = sizeMmOf(cssRule(".cs-total-guests"));
  const breakdownSize = sizeMmOf(cssRule(".cs-guest-breakdown"));
  assert.ok(totalWeight >= 800);
  assert.ok(totalSize > breakdownSize);
});

await check("泊数(.cs-c-nights)とステータス(.cs-c-status)は太く(>=700)拡大されている", async () => {
  const nightsWeight = weightOf(cssRule(".cs-c-nights"));
  const nightsSize = sizeMmOf(cssRule(".cs-c-nights"));
  const statusWeight = weightOf(cssRule(".cs-c-status"));
  const baseSize = sizeMmOf(cssRule(".cs-main-table td"));
  assert.ok(nightsWeight >= 700 && nightsSize > baseSize);
  assert.ok(statusWeight >= 700);
});

await check("備考・通信 (.cs-notice-line/.cs-instruction-line) と 予約元 (.cs-c-ota) は少なくとも semi-bold(>=600)", async () => {
  const noticeWeight = weightOf(cssRule(".cs-notice-line"));
  const otaWeight = weightOf(cssRule(".cs-c-ota"));
  assert.ok(noticeWeight >= 600);
  assert.ok(otaWeight >= 600);
});

await check("guest name size classes step down (13 > 12 > 11) and never drop below semi-bold weight", async () => {
  const base = sizeMmOf(cssRule(".cs-main-table td")); // default (cs-guest-13 tier, no override class)
  const size12 = sizeMmOf(cssRule(".cs-guest-12"));
  const size11 = sizeMmOf(cssRule(".cs-guest-11"));
  assert.ok(base > size12 && size12 > size11, "each long-name tier must be strictly smaller than the previous one");
  for (const cls of [".cs-guest-12", ".cs-guest-11"]) {
    const w = weightOf(cssRule(cls));
    assert.ok(w === null || w >= 600, `${cls} must not weaken guest-name weight below 600`);
  }
});

await check("page header (date / title) and 全体通信・引継ぎ heading are bold", async () => {
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
