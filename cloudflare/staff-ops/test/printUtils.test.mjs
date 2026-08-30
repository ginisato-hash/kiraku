// printUtils.js のテスト：printField()のblanking logicを中心に純粋関数として検証。
import assert from "node:assert";
import { printField, escapeHtml } from "../public/printUtils.js";

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

console.log(`\n${passed} printUtils checks passed`);
