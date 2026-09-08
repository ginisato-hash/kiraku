// printUtils.js のテスト：printField()のblanking logicを中心に純粋関数として検証。
import assert from "node:assert";
import { printField, escapeHtml, effectiveTextWidth } from "../public/printUtils.js";

let passed = 0;
async function check(name, fn) { await fn(); passed++; console.log("ok -", name); }

await check("printField: null/undefined -> empty string, never the word null/undefined", async () => {
  assert.equal(printField(null), "");
  assert.equal(printField(undefined), "");
});

await check("printField: placeholder literals ('None', 'null', 'undefined', 'N/A') -> empty string", async () => {
  assert.equal(printField("None"), "");
  assert.equal(printField("null"), "");
  assert.equal(printField("undefined"), "");
  assert.equal(printField("N/A"), "");
  assert.equal(printField("n/a"), "");
});

await check("printField: blank/whitespace-only string -> empty string", async () => {
  assert.equal(printField(""), "");
  assert.equal(printField("   "), "");
});

await check("printField: a real value passes through trimmed", async () => {
  assert.equal(printField("  090-1234-5678  "), "090-1234-5678");
  assert.equal(printField("山田 太郎"), "山田 太郎");
});

await check("printField: numbers/booleans are stringified", async () => {
  assert.equal(printField(12), "12");
  assert.equal(printField(0), "0");
});

await check("escapeHtml: escapes &, <, >, \", ' so raw markup can never be injected", async () => {
  assert.equal(escapeHtml(`<script>alert(1)</script>&"'`), "&lt;script&gt;alert(1)&lt;/script&gt;&amp;&quot;&#39;");
});

await check("escapeHtml: a plain string with no special characters passes through unchanged", async () => {
  assert.equal(escapeHtml("山田 太郎"), "山田 太郎");
});

// ---------------- effectiveTextWidth ----------------

await check("effectiveTextWidth: half-width ASCII counts as 1 unit per character", async () => {
  assert.equal(effectiveTextWidth("ABCDE"), 5);
});

await check("effectiveTextWidth: full-width Japanese counts as 2 units per character", async () => {
  assert.equal(effectiveTextWidth("山田太郎"), 8);
});

await check("effectiveTextWidth: mixed half/full-width sums correctly", async () => {
  assert.equal(effectiveTextWidth("山田 Taro"), 9); // 山田=2 full-width chars=4 units, " Taro"=5 half-width chars=5 units
});

await check("effectiveTextWidth: empty string -> 0", async () => {
  assert.equal(effectiveTextWidth(""), 0);
});

console.log(`\n${passed} printUtils checks passed`);
