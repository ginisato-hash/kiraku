// printUtils.js のテスト：printField()のblanking logicを中心に純粋関数として検証。
import assert from "node:assert";
import { printField } from "../public/printUtils.js";

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

console.log(`\n${passed} printUtils checks passed`);
