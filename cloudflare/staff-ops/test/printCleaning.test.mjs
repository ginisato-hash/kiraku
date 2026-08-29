// cleaningSheetTemplate.js の純粋関数テスト + cleaning.html（印刷ページ）の
// provisional banner存在確認。
import assert from "node:assert";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { renderCleaningSheetTemplate, stateLabel } from "../public/cleaningSheetTemplate.js";

const dir = path.dirname(fileURLToPath(import.meta.url));
const fixture = JSON.parse(readFileSync(path.join(dir, "fixtures/staff_ops_snapshot.sample.json"), "utf-8"));
const rooms = fixture.dates["2026-08-30"].cleaning.rooms;
const html = readFileSync(path.join(dir, "../public/ops/print/cleaning.html"), "utf-8");
const jsSrc = readFileSync(path.join(dir, "../public/cleaningSheetTemplate.js"), "utf-8");

let passed = 0;
async function check(name, fn) { await fn(); passed++; console.log("ok -", name); }

await check("renderCleaningSheetTemplate shows room TYPE label when room_number is null", async () => {
  const out = renderCleaningSheetTemplate(rooms, "2026-08-30");
  assert.ok(out.includes("ツイン｜客室トイレ付"));
});

await check("stateLabel maps all contract states to Japanese labels", async () => {
  assert.equal(stateLabel("TURNOVER"), "引継ぎ清掃");
  assert.equal(stateLabel("STAYOVER"), "連泊");
  assert.equal(stateLabel("CHECKIN"), "チェックイン");
  assert.equal(stateLabel("CHECKOUT"), "チェックアウト");
  assert.equal(stateLabel("VACANT"), "空室");
  assert.equal(stateLabel("CANCELLED"), "キャンセル");
  assert.equal(stateLabel("UNASSIGNED"), "未分類");
  assert.equal(stateLabel("something-unexpected"), "未分類");
});

await check("renderCleaningSheetTemplate on empty rooms list shows a readable empty message, not a crash", async () => {
  const out = renderCleaningSheetTemplate([], "2026-08-31");
  assert.ok(out.includes("対象日の清掃データがありません"));
});

await check("cleaningSheetTemplate.js top-of-file comment marks it as an explicit placeholder pending the source photo", async () => {
  assert.ok(jsSrc.includes("PLACEHOLDER TEMPLATE"));
  assert.ok(jsSrc.includes("pending source photo"));
});

await check("cleaning.html shows the provisional banner text (visible on-screen, never mistaken for final)", async () => {
  assert.ok(html.includes("⚠ 仮レイアウト — 原本写真確認後に本番デザインへ差し替え予定"));
});

await check("cleaning.html's provisional banner lives under .no-print (never prints)", async () => {
  const bannerBlockMatch = html.match(/<div class="no-print">[\s\S]*?⚠ 仮レイアウト[\s\S]*?<\/div>\s*<\/div>/);
  assert.ok(bannerBlockMatch, "banner should be nested inside a .no-print container");
});

await check("cleaning.html declares @page size A4 portrait", async () => {
  assert.ok(/@page\s*{[^}]*size:\s*A4\s+portrait/s.test(html));
});

console.log(`\n${passed} print cleaning checks passed`);
