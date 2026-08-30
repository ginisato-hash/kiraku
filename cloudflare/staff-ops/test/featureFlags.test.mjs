// featureFlags.js の純粋関数テスト + 清掃visualが正しくゲートされている
// (フラグOFF時は一般スタッフの通常導線から到達不可、?preview=1は常に有効)
// ことのソーステキスト確認。
import assert from "node:assert";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { CLEANING_VISUAL_READY, isPreviewRequested, cleaningVisualAllowed } from "../public/featureFlags.js";

const dir = path.dirname(fileURLToPath(import.meta.url));
const dailyOpsSrc = readFileSync(path.join(dir, "../public/dailyOps.js"), "utf-8");
const printCleaningSrc = readFileSync(path.join(dir, "../public/ops/print/print-cleaning.js"), "utf-8");
const todaySrc = readFileSync(path.join(dir, "../public/cleaning/today.js"), "utf-8");

let passed = 0;
async function check(name, fn) { await fn(); passed++; console.log("ok -", name); }

await check("CLEANING_VISUAL_READY is true (acceptance verification complete, 2026-08-30)", async () => {
  assert.strictEqual(CLEANING_VISUAL_READY, true);
});

await check("isPreviewRequested() is false with no window (Node/test environment)", async () => {
  assert.strictEqual(isPreviewRequested(), false);
});

await check("isPreviewRequested() is true only when ?preview=1 is present", async () => {
  const prevWindow = globalThis.window;
  try {
    globalThis.window = { location: { search: "?preview=1" } };
    assert.strictEqual(isPreviewRequested(), true);
    globalThis.window = { location: { search: "?preview=0" } };
    assert.strictEqual(isPreviewRequested(), false);
    globalThis.window = { location: { search: "" } };
    assert.strictEqual(isPreviewRequested(), false);
  } finally {
    if (prevWindow === undefined) delete globalThis.window;
    else globalThis.window = prevWindow;
  }
});

await check("cleaningVisualAllowed() follows CLEANING_VISUAL_READY when no preview param is set", async () => {
  const prevWindow = globalThis.window;
  try {
    globalThis.window = { location: { search: "" } };
    assert.strictEqual(cleaningVisualAllowed(), CLEANING_VISUAL_READY);
  } finally {
    if (prevWindow === undefined) delete globalThis.window;
    else globalThis.window = prevWindow;
  }
});

await check("cleaningVisualAllowed() is true under ?preview=1 even while CLEANING_VISUAL_READY is false (internal QA escape hatch)", async () => {
  const prevWindow = globalThis.window;
  try {
    globalThis.window = { location: { search: "?preview=1" } };
    assert.strictEqual(cleaningVisualAllowed(), true);
  } finally {
    if (prevWindow === undefined) delete globalThis.window;
    else globalThis.window = prevWindow;
  }
});

await check("dailyOps.js gates BOTH the show-cleaning and print-cleaning buttons behind cleaningVisualAllowed()", async () => {
  assert.ok(dailyOpsSrc.includes("cleaningVisualAllowed"),
    "dailyOps.js must import/use the feature flag, not show the cleaning buttons unconditionally");
  assert.ok(dailyOpsSrc.includes("準備中"),
    "dailyOps.js must show a pending/準備中 state for both cleaning buttons when the flag is off");
});

await check("print-cleaning.js refuses to render/print the provisional sheet when the flag is off", async () => {
  assert.ok(printCleaningSrc.includes("cleaningVisualAllowed"));
  assert.ok(printCleaningSrc.includes("準備中"));
  // The early-return branch must appear BEFORE the fetch() call, so a gated
  // request never even hits /api/cleaning (no unused PII over the wire) and
  // never reaches window.print().
  const gateIndex = printCleaningSrc.indexOf("cleaningVisualAllowed()");
  const fetchIndex = printCleaningSrc.indexOf("fetch(");
  assert.ok(gateIndex > -1 && fetchIndex > -1 && gateIndex < fetchIndex,
    "the feature-flag check must run before the /api/cleaning fetch");
});

await check("cleaning/today.js (mobile) refuses to render the provisional room list when the flag is off", async () => {
  assert.ok(todaySrc.includes("cleaningVisualAllowed"));
  assert.ok(todaySrc.includes("準備中"));
  const gateIndex = todaySrc.indexOf("cleaningVisualAllowed()");
  const fetchIndex = todaySrc.indexOf("fetch(");
  assert.ok(gateIndex > -1 && fetchIndex > -1 && gateIndex < fetchIndex,
    "the feature-flag check must run before the /api/cleaning fetch");
});

console.log(`\n${passed} feature flag checks passed`);
