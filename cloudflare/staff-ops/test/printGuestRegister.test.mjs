// guestRegisterTemplate.js の純粋関数テスト + guest-register.html のソース内容テスト。
import assert from "node:assert";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { buildGuestRegisterPageHtml, buildGuestRegisterDocumentHtml } from "../public/guestRegisterTemplate.js";

const dir = path.dirname(fileURLToPath(import.meta.url));
const fixture = JSON.parse(readFileSync(path.join(dir, "fixtures/staff_ops_snapshot.sample.json"), "utf-8"));
const arrivals = fixture.dates["2026-08-30"].arrivals;
const htmlPath = path.join(dir, "../public/ops/print/guest-register.html");
const html = readFileSync(htmlPath, "utf-8");
const bootstrapJs = readFileSync(path.join(dir, "../public/ops/print/print-guest-register.js"), "utf-8");

let passed = 0;
async function check(name, fn) { await fn(); passed++; console.log("ok -", name); }

// ---------------- pure function: buildGuestRegisterPageHtml ----------------

await check("room with null room_number shows 'タイプ: ' + room type label, not a blank room number", async () => {
  const page = buildGuestRegisterPageHtml(arrivals[0], true);
  assert.ok(page.includes("タイプ: ツイン｜客室トイレ付"), "room type should be clearly labeled as a TYPE");
});

await check("phone/address are blanked via printField when absent, never literal null/undefined", async () => {
  const page = buildGuestRegisterPageHtml(arrivals[1], true); // 佐藤花子: phone=null, address=null
  assert.ok(!/null|undefined/i.test(page), `page must not contain the literal words null/undefined: ${page}`);
});

await check("guest count line renders 大人/子供/計 with actual numbers", async () => {
  const page = buildGuestRegisterPageHtml(arrivals[0], true);
  assert.ok(page.includes("大人 2名　子供 1名　計 3名"));
});

await check("companion table has at least 5 handwriting rows", async () => {
  const page = buildGuestRegisterPageHtml(arrivals[0], true);
  const rowCount = (page.match(/<tr><td class="grf-companion-no">/g) || []).length;
  assert.ok(rowCount >= 5, `expected >=5 companion rows, got ${rowCount}`);
});

await check("confirmation sentence and SIGNATURE section are present", async () => {
  const page = buildGuestRegisterPageHtml(arrivals[0], true);
  assert.ok(page.includes("上記の記載内容および印字された登録内容を確認し、相違ありません。"));
  assert.ok(page.includes("I confirm that the information above is complete and correct."));
  assert.ok(page.includes("ご署名 / SIGNATURE"));
});

await check("no nationality/passport fields anywhere in a rendered page", async () => {
  const page = buildGuestRegisterPageHtml(arrivals[0], true);
  assert.ok(!/国籍|パスポート|nationality|passport/i.test(page));
});

await check("buildGuestRegisterDocumentHtml: break class applied to all pages except the last (no trailing blank page)", async () => {
  const doc = buildGuestRegisterDocumentHtml(arrivals); // 2 arrivals
  const breakCount = (doc.match(/grf-page-break/g) || []).length;
  assert.equal(breakCount, arrivals.length - 1);
});

await check("buildGuestRegisterDocumentHtml: 0 arrivals -> empty string, no crash", async () => {
  assert.equal(buildGuestRegisterDocumentHtml([]), "");
});

await check("buildGuestRegisterDocumentHtml: 1 arrival -> no break class (it is both first and last)", async () => {
  const doc = buildGuestRegisterDocumentHtml([arrivals[0]]);
  assert.ok(!doc.includes("grf-page-break"));
});

// ---------------- source-text assertions on guest-register.html ----------------

await check("guest-register.html declares @page size: A4 portrait", async () => {
  assert.ok(/@page\s*{[^}]*size:\s*A4\s+portrait/s.test(html));
});

await check("guest-register.html declares break-after: page for non-last pages", async () => {
  assert.ok(html.includes("break-after: page"));
});

await check("guest-register.html hides no-print controls under @media print", async () => {
  assert.ok(/@media print\s*{[^}]*\.no-print,\s*nav,\s*button\s*{\s*display:\s*none/s.test(html));
});

await check("guest-register.html has no nationality/passport fields", async () => {
  assert.ok(!/国籍|パスポート|nationality|passport/i.test(html));
});

await check("print bootstrap waits for print-ready (fonts+logo) before calling window.print()", async () => {
  const mainBody = bootstrapJs.slice(bootstrapJs.indexOf("async function main"));
  const printIdx = mainBody.indexOf("window.print()");
  const waitIdx = mainBody.indexOf("await waitForPrintReady");
  assert.ok(waitIdx >= 0, "main() should call waitForPrintReady");
  assert.ok(printIdx >= 0, "main() should call window.print()");
  assert.ok(waitIdx < printIdx, "waitForPrintReady must be awaited before window.print()");
});

await check("print bootstrap does not navigate away or blank content on afterprint", async () => {
  assert.ok(bootstrapJs.includes('addEventListener("afterprint"'));
  const body = bootstrapJs.slice(bootstrapJs.indexOf('addEventListener("afterprint"'));
  assert.ok(!body.includes("location.href"), "afterprint handler must not navigate away");
  assert.ok(!body.includes("innerHTML = \"\""), "afterprint handler must not blank the page");
});

console.log(`\n${passed} print guest-register checks passed`);
