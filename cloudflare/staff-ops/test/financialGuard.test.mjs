// financialGuard.js のテスト：fixtureに対する合格ケースと、意図的にrevenue keyを
// 混入させた失敗ケース。
import assert from "node:assert";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { assertNoFinancialKeys, FORBIDDEN_FINANCIAL_KEYS } from "../public/financialGuard.js";

const dir = path.dirname(fileURLToPath(import.meta.url));
const fixture = JSON.parse(readFileSync(path.join(dir, "fixtures/staff_ops_snapshot.sample.json"), "utf-8"));

let passed = 0;
async function check(name, fn) { await fn(); passed++; console.log("ok -", name); }

await check("passing case: the real fixture contains no financial keys", async () => {
  assert.doesNotThrow(() => assertNoFinancialKeys(fixture));
});

await check("passing case: a single day's daily-ops payload is clean", async () => {
  assert.doesNotThrow(() => assertNoFinancialKeys(fixture.dates["2026-08-30"]));
});

await check("passing case: a cleaning payload is clean", async () => {
  assert.doesNotThrow(() => assertNoFinancialKeys(fixture.dates["2026-08-30"].cleaning));
});

await check("failing case: injecting a top-level revenue key throws", async () => {
  const tampered = { ...fixture.dates["2026-08-30"], revenue: 12345 };
  assert.throws(() => assertNoFinancialKeys(tampered), /revenue/);
});

await check("failing case: injecting a nested financial key (inside an arrival) throws", async () => {
  const tampered = JSON.parse(JSON.stringify(fixture));
  tampered.dates["2026-08-30"].arrivals[0].price = 9800;
  assert.throws(() => assertNoFinancialKeys(tampered), /price/);
});

await check("failing case: key match is case-insensitive (e.g. ADR)", async () => {
  assert.throws(() => assertNoFinancialKeys({ ADR: 12000 }), /ADR|adr/i);
});

await check("FORBIDDEN_FINANCIAL_KEYS mirrors the agreed list from the Python side", async () => {
  const expected = [
    "revenue", "gross_revenue", "net_revenue", "price", "ota_commission",
    "commission", "tax_amount", "adr", "revpar", "payment_status",
    "invoice_status", "invoiceItems",
  ];
  assert.deepEqual(FORBIDDEN_FINANCIAL_KEYS, expected);
});

console.log(`\n${passed} financialGuard checks passed`);
