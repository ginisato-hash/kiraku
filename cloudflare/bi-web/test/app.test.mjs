// app.js の軽量テスト（DOM無し・ソース内容の静的チェック）。
// app.js自体はdocument/windowに依存するためnode実行では直接importせず、
// 「日付跨ぎ後に古いBIデータをキャッシュさせない」という不変条件をソースから検証する。
import assert from "node:assert";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const appJsPath = path.join(path.dirname(fileURLToPath(import.meta.url)), "../public/app.js");
const text = readFileSync(appJsPath, "utf-8");

let passed = 0;
async function check(name, fn) { await fn(); passed++; console.log("ok -", name); }

await check("getJSON fetches with cache: no-store", async () => {
  assert.ok(/fetch\(url,\s*\{\s*cache:\s*"no-store"\s*\}\)/.test(text),
    "fetch呼び出しに cache: \"no-store\" が指定されていること");
});

await check("fetchManifest and fetchSnapshot both route through getJSON (no-store)", async () => {
  assert.ok(text.includes("async function fetchManifest()"));
  assert.ok(text.includes("return getJSON(\"/api/manifest\")"));
  assert.ok(text.includes("async function fetchSnapshot(month)"));
  const fnBody = text.slice(text.indexOf("async function fetchSnapshot"),
    text.indexOf("async function fetchSnapshot") + 300);
  assert.ok(fnBody.includes("getJSON(url)"));
});

await check("app.js does not use ?t=Date.now() query cache-busting", async () => {
  assert.ok(!text.includes("Date.now()"), "cache-bustingのquery paramは使わずcache:no-storeへ統一する");
});

console.log(`\n${passed} app.js checks passed`);
