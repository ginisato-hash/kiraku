// uiRelativePaths.test.mjs — public/ の取得先がページURL相対であること。
// 管理ページ側が前置パス（例: /admin/bi/）配下でこのUIを配信しても、ルート直下（/）
// で従来どおり開いても、同じコードで正しい取得先になることを確認する。
// app.js を最小のDOM/fetch stubで実際に実行し、発行されたfetch URLを記録して検証する
// （ソース文字列の静的チェックではなく、実際に呼ばれるURLを見る）。
import assert from "node:assert";
import { readFileSync, readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const publicDir = path.join(path.dirname(fileURLToPath(import.meta.url)), "../public");
const ORIGIN = "https://host.example";
const PAGE_BASES = [`${ORIGIN}/`, `${ORIGIN}/admin/bi/`, `${ORIGIN}/a/b/c/`];

let passed = 0;
async function check(name, fn) { await fn(); passed++; console.log("ok -", name); }

function makeElement() {
  const el = {
    innerHTML: "", textContent: "", outerHTML: "", disabled: false, open: false,
    classList: { toggle() {}, add() {}, remove() {}, contains() { return false; } },
    addEventListener() {}, setAttribute() {}, getAttribute() { return null; },
    querySelector() { return null; }, querySelectorAll() { return []; },
  };
  return el;
}

// app.js をstub環境で実行し、発行された fetch の URL（ページ相対のまま）を返す。
async function recordFetchUrls({ search, instance }) {
  const urls = [];
  const saved = {};
  const stubs = {
    document: {
      getElementById: () => makeElement(),
      querySelectorAll: () => [],
      body: makeElement(),
    },
    window: {
      location: { search, href: `${ORIGIN}/${search}` },
      history: { replaceState() {} },
    },
    fetch: async (url) => { urls.push(String(url)); return { ok: false, json: async () => null }; },
    setInterval: () => 0, // 5分ごとの自動再取得でプロセスが終わらなくなるのを防ぐ
  };
  for (const [k, v] of Object.entries(stubs)) {
    saved[k] = Object.getOwnPropertyDescriptor(globalThis, k);
    Object.defineProperty(globalThis, k, { value: v, configurable: true, writable: true });
  }
  try {
    await import(`../public/app.js?instance=${instance}`);
    for (let i = 0; i < 100 && urls.length < 4; i++) {
      await new Promise((r) => setTimeout(r, 10));
    }
    await new Promise((r) => setTimeout(r, 30)); // 余分な呼び出しが無いことも見るための短い猶予
  } finally {
    for (const [k, d] of Object.entries(saved)) {
      if (d) Object.defineProperty(globalThis, k, d); else delete globalThis[k];
    }
  }
  return urls;
}

const resolveAll = (urls, base) => urls.map((u) => new URL(u, base));

await check("app.js fetches relative URLs only (no leading slash, no scheme)", async () => {
  const urls = await recordFetchUrls({ search: "", instance: "no-month" });
  assert.ok(urls.length >= 4, `expected manifest/snapshot/validation/exception fetches, got ${JSON.stringify(urls)}`);
  for (const u of urls) {
    assert.ok(!u.startsWith("/"), `absolute path fetch is not allowed under a path prefix: ${u}`);
    assert.ok(!/^[a-z][a-z0-9+.-]*:/i.test(u), `absolute URL fetch is not allowed: ${u}`);
  }
});

await check("default (no ?month=) fetch targets resolve under the page base, at root and under a prefix", async () => {
  const urls = await recordFetchUrls({ search: "", instance: "no-month-2" });
  const wanted = ["api/manifest", "api/snapshot", "data/bi_validation_status.json", "data/bi_exception_summary.json"];
  assert.deepEqual([...urls].sort(), [...wanted].sort());
  for (const base of PAGE_BASES) {
    const prefix = new URL(base).pathname;
    const paths = resolveAll(urls, base).map((u) => u.pathname).sort();
    assert.deepEqual(paths, wanted.map((w) => prefix + w).sort(), `base ${base}`);
    for (const u of resolveAll(urls, base)) assert.equal(u.origin, ORIGIN);
  }
});

await check("month-specific fetch targets resolve under the page base, at root and under a prefix", async () => {
  const urls = await recordFetchUrls({ search: "?month=2026-07", instance: "month" });
  const wanted = [
    "api/manifest",
    "api/snapshot?month=2026-07",
    "data/months/2026-07/bi_validation_status.json",
    "data/months/2026-07/bi_exception_summary.json",
  ];
  assert.deepEqual([...urls].sort(), [...wanted].sort());
  for (const base of PAGE_BASES) {
    const prefix = new URL(base).pathname;
    const resolved = resolveAll(urls, base).map((u) => u.pathname + u.search).sort();
    assert.deepEqual(resolved, wanted.map((w) => prefix + w).sort(), `base ${base}`);
  }
  // 従来どおりルート直下で開いた場合は、これまでの絶対パスと同じ場所を指す。
  const atRoot = resolveAll(urls, `${ORIGIN}/`).map((u) => u.pathname + u.search).sort();
  assert.deepEqual(atRoot, [
    "/api/manifest", "/api/snapshot?month=2026-07",
    "/data/months/2026-07/bi_exception_summary.json", "/data/months/2026-07/bi_validation_status.json",
  ].sort());
});

await check("index.html loads css/js by relative path (works under a prefix and at root)", async () => {
  const html = readFileSync(path.join(publicDir, "index.html"), "utf-8");
  const refs = [...html.matchAll(/\b(?:src|href)="([^"]+)"/g)].map((m) => m[1]);
  assert.ok(refs.includes("./app.js") && refs.includes("./styles.css"), JSON.stringify(refs));
  for (const ref of refs) {
    assert.ok(!ref.startsWith("/") && !/^[a-z][a-z0-9+.-]*:/i.test(ref), `absolute reference in index.html: ${ref}`);
    for (const base of PAGE_BASES) {
      const resolved = new URL(ref, base);
      assert.ok(resolved.pathname.startsWith(new URL(base).pathname), `${ref} escapes ${base}`);
    }
  }
});

await check("public/*.js import siblings relatively and never reference root-absolute asset/API paths", async () => {
  for (const file of readdirSync(publicDir).filter((f) => f.endsWith(".js"))) {
    const src = readFileSync(path.join(publicDir, file), "utf-8");
    for (const m of src.matchAll(/\bfrom\s+"([^"]+)"/g)) {
      assert.ok(m[1].startsWith("./"), `${file}: non-relative import ${m[1]}`);
    }
    for (const m of src.matchAll(/\bfetch\(\s*["'`]\//g)) {
      assert.fail(`${file}: root-absolute fetch URL`);
    }
  }
  const css = readFileSync(path.join(publicDir, "styles.css"), "utf-8");
  assert.ok(!/url\(\s*["']?\//.test(css), "styles.css must not reference root-absolute url()");
});

console.log(`\n${passed} uiRelativePaths checks passed`);
