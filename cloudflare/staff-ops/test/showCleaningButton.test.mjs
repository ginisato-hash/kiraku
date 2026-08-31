// 「清掃指示書を表示」ボタン（Daily Ops）が /cleaning/today へ正しく遷移することの
// ソーステキスト確認。dailyOps.js はmain()を即時実行しwindow/documentを参照するため、
// 他のbootstrapファイル（today.js/print-cleaning.js）と同じ方針でソーステキストを確認する。
import assert from "node:assert";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const dir = path.dirname(fileURLToPath(import.meta.url));
const html = readFileSync(path.join(dir, "../public/index.html"), "utf-8");
const js = readFileSync(path.join(dir, "../public/dailyOps.js"), "utf-8");

let passed = 0;
async function check(name, fn) { await fn(); passed++; console.log("ok -", name); }

await check("index.html declares the show-cleaning-btn control", async () => {
  assert.ok(/id="show-cleaning-btn"/.test(html));
});

await check("show-cleaning-btn's click handler navigates via window.location.href, not a complex mount call", async () => {
  const m = js.match(/document\.getElementById\("show-cleaning-btn"\)\.addEventListener\("click", \(\) => \{([\s\S]*?)\}\);/);
  assert.ok(m, "expected a click listener on show-cleaning-btn");
  const body = m[1];
  assert.ok(/window\.location\.href\s*=/.test(body), "must assign window.location.href to navigate");
});

await check("show-cleaning-btn navigates to /cleaning/today (never /ops/print/cleaning)", async () => {
  const m = js.match(/document\.getElementById\("show-cleaning-btn"\)\.addEventListener\("click", \(\) => \{([\s\S]*?)\}\);/);
  const body = m[1];
  assert.ok(body.includes("/cleaning/today"));
  assert.ok(!body.includes("/ops/print/cleaning"), "must not confuse the view route with the print route");
});

await check("show-cleaning-btn's target URL carries the currently selected date (currentDate), not a hard-coded date", async () => {
  const m = js.match(/document\.getElementById\("show-cleaning-btn"\)\.addEventListener\("click", \(\) => \{([\s\S]*?)\}\);/);
  const body = m[1];
  assert.ok(/date=\$\{encodeURIComponent\(currentDate\)\}/.test(body),
    "must interpolate the module-level currentDate (updated by prev/next/today navigation), not a fixed string");
});

await check("loadCleaningStaffView (the editable Staff cleaning list) is still invoked unconditionally from loadAndRender, on both the success and no-data paths", async () => {
  const occurrences = (js.match(/loadCleaningStaffView\(\);/g) || []).length;
  assert.equal(occurrences, 2,
    "override editing must remain reachable automatically now that the button no longer mounts it");
});

console.log(`\n${passed} show-cleaning-button checks passed`);
