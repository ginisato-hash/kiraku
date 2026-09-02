// cleaningSheetTemplate.js の純粋関数テスト + cleaning.html（印刷ページ）の
// ソース内容確認。renderCleaningSheetTemplateへ渡すroomsは、実運用と同じく
// mergeCleaningOverrides()を通した後の形(effectiveInstruction等が付与済み)を使う。
//
// 2026-09改訂(2回目、前回のrow間引きは撤回): 18室固定row表示を維持したまま、
// CHECKIN/TURNOVER/STAYOVER以外はRoomNo以外を完全空欄にする。階層太線は
// 501/601固定へ戻す。人数の大きい数字はbedding_guest_count(布団人数)へ変更、
// 下段の実人数内訳は常に「大人N 子供M」(年齢調整なし)。TURNOVERの自動
// instruction「入替」は生成しない。
import assert from "node:assert";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import {
  renderCleaningSheetTemplate, statusLabel, printStatusLabel, otaPrintShortName, cleanValue,
  guestNameFor, guestBreakdownFor, guestNoticeFor, onsiteInfoFor, beddingCountFor,
  nightProgressFor, arrivalTimeFor, countUnassigned,
  isPrintableRoomStatus, isFloorStartRoom,
  COLUMN_WIDTHS_MM, COLUMN_LABELS, effectiveTextWidth, guestNameSizeClass, arrivalSizeClass,
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

await check("renders all 18 rooms as room rows, always, in canonical order (18室固定表示は撤回されない)", async () => {
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

await check("the print document shows ONLY IN/連泊/blank status text — never OUT/入替/空室 (blank for non-operational rows, not a dropped row)", async () => {
  // 401=TURNOVER->IN, 402=CHECKIN->IN, 403=CHECKOUT->blank, 404=STAYOVER->連泊, 405-607=VACANT->blank
  const statusCells = [...documentHtml.matchAll(/<td class="cs-c-status">([^<]*)<\/td>/g)].map((m) => m[1]);
  assert.equal(statusCells.length, 18);
  for (const text of statusCells) {
    assert.ok(text === "IN" || text === "連泊" || text === "", `unexpected status text: "${text}"`);
  }
  assert.equal(statusCells.filter((t) => t === "IN").length, 2);
  assert.equal(statusCells.filter((t) => t === "連泊").length, 1);
  assert.equal(statusCells.filter((t) => t === "").length, 15);
});

await check("isPrintableRoomStatus is true only for CHECKIN/TURNOVER/STAYOVER", async () => {
  assert.equal(isPrintableRoomStatus("CHECKIN"), true);
  assert.equal(isPrintableRoomStatus("TURNOVER"), true);
  assert.equal(isPrintableRoomStatus("STAYOVER"), true);
  assert.equal(isPrintableRoomStatus("CHECKOUT"), false);
  assert.equal(isPrintableRoomStatus("VACANT"), false);
  assert.equal(isPrintableRoomStatus("CANCELLED"), false);
  assert.equal(isPrintableRoomStatus("UNASSIGNED"), false);
});

await check("CHECKOUT room (403): row exists (RoomNo shown) but every other cell is blank — guest name never appears", async () => {
  const row403 = documentHtml.match(/<tr data-room-number="403">[\s\S]*?<\/tr>/)[0];
  assert.ok(!row403.includes("田中 一美"), "CHECKOUT guest name must not leak into the print sheet");
  const cellTexts = [...row403.matchAll(/<td[^>]*>([\s\S]*?)<\/td>/g)].map((m) => m[1].replace(/<[^>]*>/g, "").trim());
  // first cell is RoomNo ("403"), all the rest must be empty
  assert.equal(cellTexts[0], "403");
  for (const text of cellTexts.slice(1)) {
    assert.equal(text, "", `expected every non-RoomNo cell to be blank for CHECKOUT, got "${text}"`);
  }
});

await check("VACANT room (405): row exists (RoomNo shown) but every other cell is blank", async () => {
  const row405 = documentHtml.match(/<tr data-room-number="405">[\s\S]*?<\/tr>/)[0];
  const cellTexts = [...row405.matchAll(/<td[^>]*>([\s\S]*?)<\/td>/g)].map((m) => m[1].replace(/<[^>]*>/g, "").trim());
  assert.equal(cellTexts[0], "405");
  for (const text of cellTexts.slice(1)) {
    assert.equal(text, "", `expected every non-RoomNo cell to be blank for VACANT, got "${text}"`);
  }
});

await check("CHECKOUT/VACANT rows carry the correct empty-cell classes for every column (guest/count/nights/status/arrival/ota/onsite/notes)", async () => {
  const row403 = documentHtml.match(/<tr data-room-number="403">[\s\S]*?<\/tr>/)[0];
  for (const cls of ["cs-c-guest", "cs-c-count", "cs-c-nights", "cs-c-status", "cs-c-arrival", "cs-c-ota", "cs-c-onsite", "cs-c-notes"]) {
    assert.ok(new RegExp(`<td class="${cls}"></td>`).test(row403), `expected an empty <td class="${cls}"></td> in the CHECKOUT row`);
  }
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

await check("cleaning.html declares the exact .cleaning-sheet dimensions (196mm x 281mm)", async () => {
  // 高さは283mm(=A4 297mm - margin 7mm*2 の使用可能高さちょうど)から281mmへ変更。
  // 外枠は内容量に依らない固定高さなので、283mmだと用紙下端に密着し余白0となり、
  // 印刷側の丸め差で2ページ目/下端切れが起き得た(実ブラウザ計測: 1070px/1070px)。
  assert.ok(/\.cleaning-sheet\s*{[^}]*width:\s*196mm/s.test(html));
  assert.ok(/\.cleaning-sheet\s*{[^}]*height:\s*281mm/s.test(html));
  assert.ok(!/\.cleaning-sheet\s*{[^}]*height:\s*283mm/s.test(html));
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

// ---------------- 4F/5F/6Fの階境界（太罫線、18室固定rowベースの物理room番号判定）----------------
// 2026-09改訂(2回目): 18室固定表示へ戻したため、501/601の物理room番号で固定判定
// する(前回試みた「表示された最初のrow」dynamic版は不要になった)。

await check("isFloorStartRoom is true only for 501 and 601 (fixed physical room number)", async () => {
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

// ---------------- 大人/子供 実人数内訳（常にBeds24のnumAdult/numChildそのまま。
// 年齢調整はbedding countのみに反映し、実人数表示からは絶対に消さない）----------------

await check("guestBreakdownFor: children=0 shows only 大人N (never 子供0)", async () => {
  const room = rooms.find((r) => r.room_number === "403"); // 田中一美, adults=1, children=0
  assert.equal(guestBreakdownFor(room), "大人1");
});

await check("guestBreakdownFor: always shows the real numAdult/numChild — never age-adjusted (no more 子7+N notation)", async () => {
  const room = rooms.find((r) => r.room_number === "401"); // 山田太郎 (arriving), adults=2, children=1
  assert.equal(guestBreakdownFor(room), "大人2 子供1");
  // even when age data IS available and some children are excluded from bedding,
  // the actual breakdown must still show the full real child count.
  const withAge = {
    ...room,
    arriving_guest: { ...room.arriving_guest, children: 2, children_age_data_available: true, children_age_7plus_count: 1 },
  };
  assert.equal(guestBreakdownFor(withAge), "大人2 子供2");
});

await check("guestBreakdownFor: VACANT room (no priority guest) is blank", async () => {
  const room = rooms.find((r) => r.room_number === "405");
  assert.equal(guestBreakdownFor(room), "");
});

// ---------------- 布団人数 (beddingCountFor、印刷帳票の人数セル最上段のみ) ----------------

await check("beddingCountFor reads bedding_guest_count from the priority guest (not total_guests)", async () => {
  const room401 = rooms.find((r) => r.room_number === "401"); // arriving: adults=2, children=1, bedding=3
  assert.equal(beddingCountFor(room401), "3");
});

await check("beddingCountFor is blank for a VACANT room (no priority guest)", async () => {
  const room405 = rooms.find((r) => r.room_number === "405");
  assert.equal(beddingCountFor(room405), "");
});

await check("beddingCountFor: Booking.com with confirmed age data excludes under-7 children from the big number, while guestBreakdownFor keeps the real count", async () => {
  const room401 = rooms.find((r) => r.room_number === "401");
  const tampered = {
    ...room401,
    arriving_guest: {
      ...room401.arriving_guest, source: "Booking.com", adults: 2, children: 1,
      children_age_data_available: true, children_age_7plus_count: 0, bedding_guest_count: 2,
    },
  };
  assert.equal(beddingCountFor(tampered), "2"); // under-7 child excluded from bedding
  assert.equal(guestBreakdownFor(tampered), "大人2 子供1"); // but still shown in the real headcount
});

await check("印刷ドキュメント: 人数セル上段は布団人数(bedding_guest_count)、下段は常に実人数「大人N 子供M」", async () => {
  const row401 = documentHtml.match(/<tr data-room-number="401">[\s\S]*?<\/tr>/)[0];
  assert.ok(row401.includes('<div class="cs-total-guests">3</div>'));
  assert.ok(row401.includes('<div class="cs-guest-breakdown">大人2 子供1</div>'));
});

await check("実データ anchor booking 91673623 相当(Booking.com adults=2/children=1/age=10、OTA自動生成行のみのcomments)の印刷表示", async () => {
  // Beds24 UI「ゲストからのコメント」= "1 child aged 10" + Booking.com自動生成行のみ。
  // Python側(extract.extract_cleaning_extra)がbedding_guest_count=3 / guest_notice=null
  // を返すことは tests/test_ops_cleaning_extra.py で実データ由来のcommentsに対して検証済み。
  // ここではそのDTO値が印刷帳票へ正しく出ることだけを確認する。
  const room401 = rooms.find((r) => r.room_number === "401");
  const anchorRoom = {
    ...room401,
    status: "CHECKIN",
    departing_guest: null,
    arriving_guest: {
      ...room401.arriving_guest,
      booking_id: "91673623", source: "Booking.com", adults: 2, children: 1, total_guests: 3,
      children_age_data_available: true, children_age_7plus_count: 1,
      children_age_known_count: 1, bedding_guest_count: 3, guest_notice: null,
    },
  };
  assert.equal(beddingCountFor(anchorRoom), "3");        // 布団3人分(10歳は7歳以上なので加算)
  assert.equal(guestBreakdownFor(anchorRoom), "大人2 子供1");  // 実人数は常にそのまま
  assert.equal(guestNoticeFor(anchorRoom), "");          // child-age/system行だけなので「客:」は出さない
});

// ---------------- 到着列: commentsからの時間帯fallback表示 (要件7-9・14・19) ----------------

await check("arrivalSizeClass: 明示値は既定サイズ、時間帯rangeだけ段階的に縮小(1行表示維持)", async () => {
  assert.equal(arrivalSizeClass("15:00"), "");            // Beds24明示値
  assert.equal(arrivalSizeClass("17:00-18:00"), "cs-arrival-range");
  assert.equal(arrivalSizeClass("15:00:00"), "cs-arrival-9");
  assert.equal(arrivalSizeClass(""), "");
  assert.equal(arrivalSizeClass(null), "");
});

// 実データ相当: Booking.com、adults2/children1/age10、comments には child-age +
// arrival window + Guest name + 実要望が同居 -> Python側が解決した後のDTO値。
const rangeRooms = rooms.map((r) => (r.room_number !== "401" ? r : ({
  ...r,
  status: "CHECKIN",
  departing_guest: null,
  arriving_guest: {
    ...r.arriving_guest,
    booking_id: "91673623", source: "Booking.com", adults: 2, children: 1, total_guests: 3,
    children_age_data_available: true, children_age_7plus_count: 1,
    children_age_known_count: 1, bedding_guest_count: 3,
    arrival_time: "17:00-18:00",
    guest_notice: "Please prepare two pillows",
  },
})));
const rangeDocumentHtml = renderCleaningSheetTemplate(rangeRooms, "2026-09-03");
const rangeRow401 = rangeDocumentHtml.match(/<tr data-room-number="401">[\s\S]*?<\/tr>/)[0];

await check("到着セル: '17:00-18:00' が1行でそのまま出力され、縮小classが付く", async () => {
  assert.ok(rangeRow401.includes('<td class="cs-c-arrival cs-arrival-range">17:00-18:00</td>'),
    rangeRow401);
});

await check("到着セル: 明示値(5文字)には縮小classを付けない", async () => {
  const row402 = documentHtml.match(/<tr data-room-number="402">[\s\S]*?<\/tr>/)[0];
  assert.ok(row402.includes('<td class="cs-c-arrival">15:00</td>'), row402);
});

await check("備考セル: 実際のゲスト要望は「客: …」として残る", async () => {
  assert.ok(rangeRow401.includes("客: Please prepare two pillows"), rangeRow401);
});

await check("印刷HTMLにOTA/system metadata文字列が一切出ない", async () => {
  for (const forbidden of ["Guest name:", "Approximate time of arrival:", "1 child aged",
                           "THIS RESERVATION HAS BEEN PRE-PAID", "BOOKING NOTE",
                           "NonSmoke", "Non Smoking Requested"]) {
    assert.ok(!rangeDocumentHtml.includes(forbidden), `leaked: ${forbidden}`);
    assert.ok(!documentHtml.includes(forbidden), `leaked in base fixture: ${forbidden}`);
  }
});

await check("到着列の縮小CSSがcleaning.htmlに存在し、基本ルールより後ろに置かれている", async () => {
  assert.ok(html.includes("td.cs-arrival-range"));
  assert.ok(html.includes(".cs-c-arrival { white-space: nowrap; }"));
  // .cs-main-table td(同specificity)より後 = ソース順で後勝ちすること
  assert.ok(html.indexOf("td.cs-arrival-range") > html.indexOf(".cs-main-table td"));
});

await check("到着列は3行折返しにしない(nowrap指定)", async () => {
  assert.ok(html.includes(".cs-c-arrival { white-space: nowrap; }"));
});

// ---------------- お客様からのお知らせ (guest_notice) ----------------

await check("guestNoticeFor reads guest_notice from the priority guest, cleaned via cleanValue", async () => {
  const room = rooms.find((r) => r.room_number === "401");
  assert.equal(guestNoticeFor(room), "到着が少し遅れます");
});

await check("備考・通信 cell shows '客: ...' for guest_notice; TURNOVER no longer auto-generates '指: 入替' (2026-09撤回)", async () => {
  assert.ok(documentHtml.includes('客: 到着が少し遅れます'));
  assert.ok(!documentHtml.includes('指: 入替'), "the automatic TURNOVER instruction must be gone — status column already shows IN");
});

await check("備考・通信 cell still shows a manual staff override ('指: ...') when one is present — only the automatic 入替 was removed", async () => {
  const rawRooms401Overridden = rawRooms.map((r) => (r.room_number === "401" ? { ...r, source_instruction: "" } : r));
  const roomsWithOverride = mergeCleaningOverrides(rawRooms401Overridden, { "401": { instruction: "ベッド分け", updatedAt: "2026-08-30T00:00:00+09:00" } });
  const out = renderCleaningSheetTemplate(roomsWithOverride, "2026-08-30");
  const row401 = out.match(/<tr data-room-number="401">[\s\S]*?<\/tr>/)[0];
  assert.ok(row401.includes("指: ベッド分け"));
});

await check("TURNOVER regression: status column shows IN, and no automatic instruction text appears anywhere for that room", async () => {
  const row401 = documentHtml.match(/<tr data-room-number="401">[\s\S]*?<\/tr>/)[0];
  assert.ok(row401.includes('<td class="cs-c-status">IN</td>'));
  assert.ok(!row401.includes("入替"));
});

await check("備考・通信 cell omits the '客:' line entirely when guest_notice is empty (not a blank line)", async () => {
  const room402Match = documentHtml.match(/data-room-number="402"[\s\S]*?<\/tr>/);
  assert.ok(room402Match, "expected to find room 402's row");
  assert.ok(!room402Match[0].includes("客:"), "402 has no guest_notice in the fixture, so no 客: line should render");
});

// ---------------- 現地決済 (payment_due_at_property / amount_due_at_property、
// Beds24公式Invoice Balanceベース。extract.py側の詳細な数値検証は
// tests/test_ops_cleaning_extra.py参照 — ここはJS側のonsiteInfoFor()の
// 表示判定ロジックのみを検証する) ----------------

await check("onsiteInfoFor shows amount only when payment_due_at_property AND a positive finite amount are present", async () => {
  const room401 = rooms.find((r) => r.room_number === "401");
  assert.deepEqual(onsiteInfoFor(room401), { show: true, amountText: "¥18,000" });

  const room402 = rooms.find((r) => r.room_number === "402"); // no amount-due fields set
  assert.equal(onsiteInfoFor(room402).show, false);
});

await check("onsiteInfoFor never shows for a negative, zero, or non-finite amount even if payment_due_at_property is true", async () => {
  const room = rooms.find((r) => r.room_number === "402");
  for (const bad of [0, -100, NaN, Infinity]) {
    const tampered = { ...room, arriving_guest: { ...room.arriving_guest, payment_due_at_property: true, amount_due_at_property: bad } };
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

// selector文字列は完全一致ではなく「末尾が.className」で照合する — 実際の規則が
// "td.cs-c-room"のようにtype selectorを前置していても、クエリ".cs-c-room"で
// 見つけられるようにするため(このファイル自体の過去のバグ: 単なる.cs-c-room
// (specificity 0,1,0)は基本ルール.cs-main-table td(0,1,1)に実ブラウザの
// カスケードで負けており、宣言したfont-sizeが完全に無効化されていた。
// 2026-09、getComputedStyleでの実測により発覚。CSS文字列の存在チェックだけの
// このテストは当時それを検出できなかった — 以後は下のspecificity回帰テストで防ぐ)。
function selectorMatchesClass(selectorToken, className) {
  return selectorToken === className || selectorToken.endsWith(`.${className.slice(1)}`);
}
function cssRule(selector) {
  const blockRe = /([^{}]+){([^}]*)}/gs;
  let merged = "";
  let m;
  while ((m = blockRe.exec(htmlNoComments))) {
    const selectors = m[1].split(",").map((s) => s.trim());
    if (selectors.some((s) => selectorMatchesClass(s, selector))) merged += m[2] + ";";
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

// 簡易CSS specificity計算(id, class/attr/pseudo-class, type selectorの3タプル)。
// combinator(space/>)は無視して個々のcompound selectorのトークンだけ数える —
// このファイルで実際に使われている単純なセレクタ(class/type/クラス複合)には十分。
function specificityOf(selectorToken) {
  const ids = (selectorToken.match(/#[\w-]+/g) || []).length;
  const classes = (selectorToken.match(/\.[\w-]+/g) || []).length;
  const types = (selectorToken.match(/(^|[\s>+~])[a-z]+\b/gi) || []).length;
  return [ids, classes, types];
}
function cmpSpecificity(a, b) {
  for (let i = 0; i < 3; i++) { if (a[i] !== b[i]) return a[i] - b[i]; }
  return 0;
}
// selectorのうち、実際に.cs-main-table tdをspecificityで上回る(あるいは同点で
// ソース順が後にあるため有効に上書きできる)トークンを探す。
function findOverridingSelectorFor(className) {
  const baseSpec = specificityOf(".cs-main-table td");
  const blockRe = /([^{}]+){([^}]*)}/gs;
  let m;
  let winner = null; // 最後に見つかった、baseと同点以上のtoken
  while ((m = blockRe.exec(htmlNoComments))) {
    const tokens = m[1].split(",").map((s) => s.trim());
    for (const t of tokens) {
      if (selectorMatchesClass(t, className) && cmpSpecificity(specificityOf(t), baseSpec) >= 0) {
        winner = t; // 後方のマッチほど実際に勝つ(同点はソース順で後勝ち)
      }
    }
  }
  return winner;
}

await check("regression: every per-column override that sets font-size/padding on a <td> has enough CSS specificity to actually beat .cs-main-table td in the real cascade", async () => {
  // 2026-09に発覚した実害の再発防止: 単なる.cs-c-room(0,1,0)のような宣言は、
  // .cs-main-table td(0,1,1)にspecificityで負け、ブラウザ上で完全に無視される。
  // td.cs-c-room(0,1,1)のように揃えてソース順で後勝ちさせる必要がある。
  for (const cls of [".cs-c-room", ".cs-c-nights", ".cs-c-status", ".cs-c-ota", ".cs-guest-12", ".cs-guest-11"]) {
    const winner = findOverridingSelectorFor(cls);
    assert.ok(winner, `${cls}: no selector variant found that can beat .cs-main-table td's specificity (0,1,1) — the declared font-size/weight would be silently ignored by the browser`);
  }
  // th側も同様(.cs-main-table th vs .cs-h-room)
  const thWinner = findOverridingSelectorFor(".cs-h-room");
  assert.ok(thWinner, ".cs-h-room must beat .cs-main-table th's specificity");
});

await check("body text baseline is bold: .cs-main-table td declares font-weight >= 700", async () => {
  const w = weightOf(cssRule(".cs-main-table td"));
  assert.ok(w !== null && w >= 700, `expected .cs-main-table td font-weight >= 700, got ${w}`);
});

await check("column headers are bold: .cs-main-table th declares font-weight >= 700", async () => {
  const w = weightOf(cssRule(".cs-main-table th"));
  assert.ok(w !== null && w >= 700, `expected header font-weight >= 700, got ${w}`);
});

await check("RoomNo (.cs-c-room) meets the hard floor: font-size >= 6mm, font-weight >= 800, padding <= 0.3mm (現場から2回目の指摘 — 数値で固定)", async () => {
  const body = cssRule(".cs-c-room");
  const roomWeight = weightOf(body);
  const roomSize = sizeMmOf(body);
  const baseSize = sizeMmOf(cssRule(".cs-main-table td"));
  assert.ok(roomWeight >= 800, `expected .cs-c-room font-weight >= 800, got ${roomWeight}`);
  assert.ok(roomSize >= 6, `expected .cs-c-room font-size >= 6mm, got ${roomSize}mm`);
  assert.ok(roomSize > baseSize, "RoomNo should be clearly larger than the general body text");
  const paddingBody = cssRule(".cs-c-count"); // shared padding rule also targets .cs-c-room/.cs-c-nights
  assert.ok(/padding:\s*0\.[0-3]mm/.test(cssRule(".cs-c-room")) || /padding:\s*0\.[0-3]mm/.test(body),
    "RoomNo's own padding override must be present and <= 0.3mm");
});

await check("人数上段(.cs-total-guests)は現場再指摘に基づく数値フロア: font-size >= 6.5mm、font-weight >= 800、下段より明確に大きい", async () => {
  const totalWeight = weightOf(cssRule(".cs-total-guests"));
  const totalSize = sizeMmOf(cssRule(".cs-total-guests"));
  const breakdownSize = sizeMmOf(cssRule(".cs-guest-breakdown"));
  assert.ok(totalWeight >= 800);
  assert.ok(totalSize >= 6.5, `expected .cs-total-guests font-size >= 6.5mm, got ${totalSize}mm`);
  assert.ok(totalSize > breakdownSize);
});

await check("泊数(.cs-c-nights)は数値フロア: font-size >= 5.5mm、font-weight >= 800（旧サイズへ戻すこと禁止）", async () => {
  const nightsBody = cssRule(".cs-c-nights");
  const nightsWeight = weightOf(nightsBody);
  const nightsSize = sizeMmOf(nightsBody);
  const statusWeight = weightOf(cssRule(".cs-c-status"));
  const baseSize = sizeMmOf(cssRule(".cs-main-table td"));
  assert.ok(nightsWeight >= 800, `expected .cs-c-nights font-weight >= 800, got ${nightsWeight}`);
  assert.ok(nightsSize >= 5.5, `expected .cs-c-nights font-size >= 5.5mm, got ${nightsSize}mm`);
  assert.ok(nightsSize > baseSize);
  assert.ok(statusWeight >= 700);
});

await check("RoomNo/人数/泊数のtd paddingは0.3mm以下まで詰められている(共通tdの1mm 1.2mmのままでは拡大が無意味になるため)", async () => {
  const m = html.replace(/\/\*[\s\S]*?\*\//g, "").match(/td\.cs-c-room,\s*td\.cs-c-count,\s*td\.cs-c-nights\s*{([^}]*)}/s);
  assert.ok(m, "expected a shared low-padding rule for td.cs-c-room/td.cs-c-count/td.cs-c-nights");
  assert.ok(/padding:\s*0\.[0-3]mm/.test(m[1]), `expected padding <= 0.3mm, got: ${m[1]}`);
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

await check("A4下端の安全余白: 用紙外枠281mm + 通信欄48mm(実ブラウザ計測で残り約2.1mm)", async () => {
  // 実ブラウザ計測(2026-09-03): contentBottom=1062px / A4使用可能=1070px /
  // remaining=8px=2.12mm。.cleaning-sheetは内容量に関係ない固定高さで、以前は
  // 283mm=使用可能高さちょうど＝余白0だったため、印刷側の丸め差で2ページ目に
  // なり得た。数値を戻すと余白が消えるのでmm値そのものを固定する。
  const sheet = cssRule(".cleaning-sheet");
  assert.ok(/height:\s*281mm/.test(sheet), sheet);
  const footer = cssRule(".cs-footer-box");
  assert.ok(/height:\s*48mm/.test(footer), footer);
  // 通信欄は手書きできる高さを維持(padding 2mm*2 + 見出し約4.6mmを引いて約37mm)
  assert.ok(48 - 4 - 5 >= 30);
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
