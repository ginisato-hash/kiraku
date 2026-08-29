// Cloudflare Cron Trigger -> GitHub Actions workflow_dispatch のテスト。
// 確認: 正しいエンドポイント/header/bodyを叩く、204のみ成功、失敗時もtoken値を
// 一切露出しない、ctx.waitUntilでdispatchが待たれる。
import assert from "node:assert";
import worker, { dispatchBiRefreshWorkflow } from "../src/worker.js";

let passed = 0;
async function check(name, fn) { await fn(); passed++; console.log("ok -", name); }

function withMockFetch(impl, fn) {
  const original = globalThis.fetch;
  globalThis.fetch = impl;
  return fn().finally(() => { globalThis.fetch = original; });
}

await check("dispatches to the exact refresh-bi-r2.yml workflow_dispatch endpoint with required headers", async () => {
  let captured;
  await withMockFetch(async (url, init) => {
    captured = { url, init };
    return new Response(null, { status: 204 });
  }, async () => {
    const result = await dispatchBiRefreshWorkflow({ GITHUB_ACTIONS_DISPATCH_TOKEN: "secret-token-value" });
    assert.equal(result.ok, true);
    assert.equal(result.status, 204);
  });
  assert.equal(captured.url, "https://api.github.com/repos/ginisato-hash/kiraku/actions/workflows/refresh-bi-r2.yml/dispatches");
  assert.equal(captured.init.method, "POST");
  assert.equal(captured.init.headers["Authorization"], "Bearer secret-token-value");
  assert.equal(captured.init.headers["Accept"], "application/vnd.github+json");
  assert.equal(captured.init.headers["X-GitHub-Api-Version"], "2022-11-28");
  assert.ok(captured.init.headers["User-Agent"], "GitHub API requires a User-Agent header");
  assert.deepEqual(JSON.parse(captured.init.body), { ref: "main" });
});

await check("only HTTP 204 counts as dispatch success", async () => {
  await withMockFetch(async () => new Response("Not Found", { status: 404 }), async () => {
    const result = await dispatchBiRefreshWorkflow({ GITHUB_ACTIONS_DISPATCH_TOKEN: "x" });
    assert.equal(result.ok, false);
    assert.equal(result.status, 404);
  });
  await withMockFetch(async () => new Response("", { status: 200 }), async () => {
    const result = await dispatchBiRefreshWorkflow({ GITHUB_ACTIONS_DISPATCH_TOKEN: "x" });
    assert.equal(result.ok, false, "200 must NOT be treated as success — GitHub's dispatch endpoint returns 204");
  });
});

await check("missing GITHUB_ACTIONS_DISPATCH_TOKEN secret fails closed without ever calling fetch", async () => {
  let fetchCalled = false;
  await withMockFetch(async () => { fetchCalled = true; return new Response(null, { status: 204 }); }, async () => {
    const result = await dispatchBiRefreshWorkflow({});
    assert.equal(result.ok, false);
    assert.match(result.error, /GITHUB_ACTIONS_DISPATCH_TOKEN/);
  });
  assert.equal(fetchCalled, false);
});

await check("a network error is reported as a failure, not thrown", async () => {
  await withMockFetch(async () => { throw new Error("network down"); }, async () => {
    const result = await dispatchBiRefreshWorkflow({ GITHUB_ACTIONS_DISPATCH_TOKEN: "x" });
    assert.equal(result.ok, false);
    assert.match(result.error, /network down/);
  });
});

await check("the token value never appears in the returned result, even on failure", async () => {
  const TOKEN = "ghp_super_secret_value_do_not_leak";
  await withMockFetch(async () => new Response("Bad credentials", { status: 401 }), async () => {
    const result = await dispatchBiRefreshWorkflow({ GITHUB_ACTIONS_DISPATCH_TOKEN: TOKEN });
    const serialized = JSON.stringify(result);
    assert.ok(!serialized.includes(TOKEN), "dispatch result must never contain the raw token");
  });
});

await check("scheduled() defers to ctx.waitUntil so the dispatch isn't cancelled early", async () => {
  let waited = null;
  const ctx = { waitUntil: (p) => { waited = p; } };
  await withMockFetch(async () => new Response(null, { status: 204 }), async () => {
    await worker.scheduled({ cron: "3,18,33,48 * * * *", scheduledTime: Date.parse("2026-08-29T10:03:00Z") }, { GITHUB_ACTIONS_DISPATCH_TOKEN: "x" }, ctx);
  });
  assert.ok(waited, "scheduled() must call ctx.waitUntil with the dispatch promise");
  await waited; // should resolve without throwing
});

console.log(`\n${passed} scheduled/dispatch checks passed`);
