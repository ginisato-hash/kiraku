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
  assert.ok(/fetch\(finalUrl,\s*\{\s*cache:\s*"no-store"\s*\}\)/.test(text),
    "fetch呼び出しに cache: \"no-store\" が指定されていること");
});

await check("fetchManifest and fetchSnapshot both route through getJSON (no-store)", async () => {
  assert.ok(text.includes("async function fetchManifest(bust)"));
  assert.ok(text.includes("return getJSON(\"/api/manifest\", { bust })"));
  assert.ok(text.includes("async function fetchSnapshot(month, bust)"));
  const fnBody = text.slice(text.indexOf("async function fetchSnapshot"),
    text.indexOf("async function fetchSnapshot") + 300);
  assert.ok(fnBody.includes("getJSON(url, { bust })"));
});

// 通常のfetch(月切替・自動5分更新)はcache:"no-store"だけで十分なため、Date.now()の
// query cache-bustingは付けない。手動更新ボタン(bust=true)の時だけ明示的に付与する
// (中間キャッシュ層を確実に回避するための追加保険。既定のfetchには付けない)。
await check("Date.now() cache-busting is opt-in via bust, not applied to every fetch", async () => {
  assert.ok(text.includes("Date.now()"), "手動更新ボタンではcache-busting query paramを使う");
  const getJsonBody = text.slice(text.indexOf("async function getJSON"),
    text.indexOf("async function fetchManifest"));
  assert.ok(/bust\s*\?\s*`\$\{url\}/.test(getJsonBody),
    "Date.now()はbustフラグがtrueの時だけ付与されること");
});

await check("handleManualRefresh fetches with bust:true and updates refresh state", async () => {
  const fnBody = text.slice(text.indexOf("async function handleManualRefresh"),
    text.indexOf("async function main"));
  assert.ok(fnBody.includes("setRefreshState(\"loading\")"));
  assert.ok(fnBody.includes("{ bust: true }"));
  assert.ok(fnBody.includes("setRefreshState(ok ? \"success\" : \"error\")"));
});

await check("refresh button state auto-resets to idle after success/error", async () => {
  const fnBody = text.slice(text.indexOf("function setRefreshState"),
    text.indexOf("function setLoading"));
  assert.ok(fnBody.includes("setTimeout"));
  assert.ok(fnBody.includes("\"idle\""));
});

console.log(`\n${passed} app.js checks passed`);
