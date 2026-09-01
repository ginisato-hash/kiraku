// モバイル清掃ビュー(/cleaning/today)のテスト：
// renderMobileRoomBlock/renderMobileCleaningBody(cleaningSheetTemplate.js、DOM非依存の
// 純粋関数) + today.html/today.jsのソース内容チェック（viewport meta / font-size /
// today-JST default / ボタン無し）。
//
// 2026-09改訂: 印刷側と合わせOUT表示を削除、statusはIN/連泊のみ(printStatusLabel
// を共有)。guest_notice/大人子供内訳は表示するが、現地決済金額は今回モバイルへ
// 追加しない(spec項目30: 清掃担当者に金額情報を見せる必要は今回指定されていない)。
//
// today.js自体はmain()をトップレベルで即時実行し window/document を参照するため、
// Node環境で直接importするとクラッシュする — 実データを扱う描画ロジックは
// cleaningSheetTemplate.js側の純粋関数としてテストし、today.js/today.htmlは
// ソーステキストの確認に留める(このリポジトリの他の印刷/モバイルbootstrapファイルと
// 同じテスト方針)。
import assert from "node:assert";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { renderMobileRoomBlock, renderMobileCleaningBody } from "../public/cleaningSheetTemplate.js";
import { mergeCleaningOverrides } from "../src/cleaningOverrides.js";

const dir = path.dirname(fileURLToPath(import.meta.url));
const fixture = JSON.parse(readFileSync(path.join(dir, "fixtures/staff_ops_snapshot.sample.json"), "utf-8"));
const rawRooms = fixture.dates["2026-08-30"].cleaning.rooms;
const rooms = mergeCleaningOverrides(rawRooms, {});
const html = readFileSync(path.join(dir, "../public/cleaning/today.html"), "utf-8");
const js = readFileSync(path.join(dir, "../public/cleaning/today.js"), "utf-8");

let passed = 0;
async function check(name, fn) { await fn(); passed++; console.log("ok -", name); }

await check("renderMobileCleaningBody renders exactly 18 blocks, in canonical KIRAKU_ROOM_ORDER", async () => {
  const out = renderMobileCleaningBody(rooms);
  const matches = [...out.matchAll(/class="mc-room-block[^"]*"[\s\S]*?<div class="mc-room-number">(\d+)/g)];
  assert.equal(matches.length, 18);
  assert.equal(matches[0][1], "401");
  assert.equal(matches[17][1], "607");
});

await check("renderMobileCleaningBody never includes an UNASSIGNED block (room_number null never rendered here)", async () => {
  const out = renderMobileCleaningBody(rooms);
  assert.ok(!out.includes("中村 光"));
});

await check("occupied room block (TURNOVER, 401) shows the guest name and an IN status badge (not 入替/OUT)", async () => {
  const room = rooms.find((r) => r.room_number === "401");
  const out = renderMobileRoomBlock(room);
  assert.ok(out.includes("mc-room-block"));
  assert.ok(out.includes("山田 太郎"));
  const badgeMatch = out.match(/<div class="mc-status-badge[^"]*">([^<]*)<\/div>/);
  assert.ok(badgeMatch, "expected a status badge");
  assert.equal(badgeMatch[1], "IN", "TURNOVER must show as IN on mobile, matching the print sheet");
  assert.ok(!out.includes(">OUT<"));
  assert.ok(!/status-badge[^>]*>入替/.test(out));
});

await check("STAYOVER room (404) shows a 連泊 status badge", async () => {
  const room = rooms.find((r) => r.room_number === "404");
  const out = renderMobileRoomBlock(room);
  const badgeMatch = out.match(/<div class="mc-status-badge[^"]*">([^<]*)<\/div>/);
  assert.equal(badgeMatch[1], "連泊");
});

await check("CHECKOUT room (403) shows no status badge at all (printStatusLabel is blank for CHECKOUT)", async () => {
  const room = rooms.find((r) => r.room_number === "403");
  const out = renderMobileRoomBlock(room);
  assert.ok(!out.includes("mc-status-badge"));
});

await check("vacant room block (405) renders compactly: room number + 空室 only, no guest/detail lines", async () => {
  const room = rooms.find((r) => r.room_number === "405");
  const out = renderMobileRoomBlock(room);
  assert.ok(out.includes("mc-room-vacant"));
  assert.ok(out.includes("空室"));
  assert.ok(!out.includes("mc-guest-name"));
  assert.ok(!out.includes("mc-detail-line"));
});

await check("renderMobileRoomBlock omits the instruction line entirely when effectiveInstruction is empty (not a blank line)", async () => {
  const room = rooms.find((r) => r.room_number === "404"); // STAYOVER, source_instruction ""
  const out = renderMobileRoomBlock(room);
  assert.ok(!out.includes("指:"));
});

await check("a VACANT room with an override instruction still shows it (never silently hidden just because the room is empty)", async () => {
  const vacantRoom = rooms.find((r) => r.room_number === "405");
  const withOverride = { ...vacantRoom, effectiveInstruction: "電球交換予定", hasOverride: true };
  const out = renderMobileRoomBlock(withOverride);
  assert.ok(out.includes("mc-room-vacant"));
  assert.ok(out.includes("空室"));
  assert.ok(out.includes("電球交換予定"));
});

await check("occupied room block shows guest_notice as a '客:' line when present", async () => {
  const room = rooms.find((r) => r.room_number === "401"); // has guest_notice in the fixture
  const out = renderMobileRoomBlock(room);
  assert.ok(out.includes("客: 到着が少し遅れます"));
});

await check("occupied room block shows the effectiveInstruction as a '指:' line when present (manual override — TURNOVER no longer auto-generates one)", async () => {
  const room = rooms.find((r) => r.room_number === "401");
  const withOverride = { ...room, effectiveInstruction: "ベッド分け", hasOverride: true };
  const out = renderMobileRoomBlock(withOverride);
  assert.ok(out.includes("指: ベッド分け"));
});

await check("TURNOVER room (401) shows no '指:' line at all when there is no manual override (2026-09撤回: 自動生成の「入替」廃止)", async () => {
  const room = rooms.find((r) => r.room_number === "401"); // effectiveInstruction="" in the updated fixture
  const out = renderMobileRoomBlock(room);
  assert.ok(!out.includes("指:"));
  assert.ok(!out.includes("入替"));
});

await check("occupied room block never shows onsite payment amount (out of scope for mobile per spec)", async () => {
  const room = rooms.find((r) => r.room_number === "401"); // onsite_payment_amount=18000 in the fixture
  const out = renderMobileRoomBlock(room);
  assert.ok(!out.includes("18,000"));
  assert.ok(!out.includes("¥"));
  assert.ok(!out.includes("現地"));
});

await check("occupied room block includes the 大人/子供 breakdown alongside the guest count", async () => {
  const room = rooms.find((r) => r.room_number === "401"); // adults=2, children=1
  const out = renderMobileRoomBlock(room);
  assert.ok(out.includes("大人2 子供1"));
});

await check("today.html declares a viewport meta tag", async () => {
  assert.ok(/<meta name="viewport" content="width=device-width, initial-scale=1\.0"/.test(html));
});

await check("today.html base font-size is not tiny (>= 16px)", async () => {
  const m = html.match(/body\s*{\s*font-size:\s*(\d+)px/);
  assert.ok(m, "expected an explicit body font-size in px");
  assert.ok(Number(m[1]) >= 16, `font-size ${m[1]}px is too small for mobile cleaning staff`);
});

await check("today.html room number is the visually largest element within a room block", async () => {
  // Scoped to the elements that actually appear inside one room block
  // (.mc-date is a page-level heading outside any block, not a competitor here).
  const blockClasses = ["mc-guest-name", "mc-status-badge", "mc-detail-line", "mc-instruction", "mc-vacant-label"];
  const sizeFor = (cls) => {
    const m = html.match(new RegExp(`\\.${cls}\\s*{\\s*font-size:\\s*([\\d.]+)rem`));
    return m ? Number(m[1]) : 0;
  };
  const roomNumberMatch = html.match(/\.mc-room-number\s*{\s*font-size:\s*([\d.]+)rem/);
  assert.ok(roomNumberMatch, "expected an explicit .mc-room-number font-size in rem");
  const roomNumberSize = Number(roomNumberMatch[1]);
  assert.ok(roomNumberSize > 1, "room label should be larger than the 1rem base");
  for (const cls of blockClasses) {
    assert.ok(roomNumberSize > sizeFor(cls), `.mc-room-number (${roomNumberSize}rem) must be larger than .${cls} (${sizeFor(cls)}rem)`);
  }
});

await check("today.js defaults to todayJst() when no ?date= param is present", async () => {
  assert.ok(js.includes("todayJst()"));
  assert.ok(js.includes("DATE_RE.test(d) ? d : todayJst()"));
});

await check("today.js fetches /api/cleaning?date=... (same endpoint as the print page) with no divergent filtering", async () => {
  assert.ok(js.includes("/api/cleaning?date="));
  assert.ok(!/\.filter\(/.test(js), "today.js must not apply its own client-side room filtering");
});

await check("today.js has no start/complete/inspected/assign status buttons (out of scope for this version)", async () => {
  assert.ok(!/<button/i.test(js));
  assert.ok(!/addEventListener\(\s*["']click["']/.test(js));
  assert.ok(!/method:\s*["']POST["']|method:\s*["']DELETE["']/.test(js), "today.js must not mutate anything");
});

console.log(`\n${passed} mobile cleaning checks passed`);
