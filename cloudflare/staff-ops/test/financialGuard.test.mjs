// financialGuard.js のテスト：fixtureに対する合格ケースと、意図的にrevenue keyを
// 混入させた失敗ケース。
import assert from "node:assert";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import {
  assertNoFinancialKeys, FORBIDDEN_FINANCIAL_KEYS,
  assertNoForbiddenCleaningKeys, CLEANING_FORBIDDEN_KEYS,
} from "../public/financialGuard.js";
import { mergeCleaningOverrides } from "../src/cleaningOverrides.js";

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

// ---------------- assertNoForbiddenCleaningKeys (cleaning-specific guard) ----------------

await check("passing case: the real merged cleaning payload (GET /api/cleaning response shape) is clean", async () => {
  const rawRooms = fixture.dates["2026-08-30"].cleaning.rooms;
  const rooms = mergeCleaningOverrides(rawRooms, {});
  assert.doesNotThrow(() => assertNoForbiddenCleaningKeys({ date: "2026-08-30", rooms }));
});

await check("failing case: a cleaning row deliberately augmented with a forbidden PII key (phone) is rejected", async () => {
  const rawRooms = fixture.dates["2026-08-30"].cleaning.rooms;
  const rooms = mergeCleaningOverrides(rawRooms, {});
  const tampered = { date: "2026-08-30", rooms: [{ ...rooms[0], phone: "090-1234-5678" }, ...rooms.slice(1)] };
  assert.throws(() => assertNoForbiddenCleaningKeys(tampered), /phone/);
});

await check("failing case: a financial key (price) inside a cleaning row is also rejected by the cleaning guard", async () => {
  const rawRooms = fixture.dates["2026-08-30"].cleaning.rooms;
  const rooms = mergeCleaningOverrides(rawRooms, {});
  const tampered = { date: "2026-08-30", rooms: [{ ...rooms[0], price: 9800 }, ...rooms.slice(1)] };
  assert.throws(() => assertNoForbiddenCleaningKeys(tampered), /price/);
});

await check("CLEANING_FORBIDDEN_KEYS is FORBIDDEN_FINANCIAL_KEYS plus the agreed cleaning-specific PII list", async () => {
  const expectedExtra = [
    "phone", "mobile", "address", "postcode", "prefecture", "city", "rest",
    "email", "passport", "nationality", "country", "notes", "rate", "amount",
  ];
  for (const key of FORBIDDEN_FINANCIAL_KEYS) {
    assert.ok(CLEANING_FORBIDDEN_KEYS.includes(key), `expected ${key} to be included`);
  }
  for (const key of expectedExtra) {
    assert.ok(CLEANING_FORBIDDEN_KEYS.includes(key), `expected ${key} to be included`);
  }
});

// ---------------- 2026-09: payment_due_at_property/amount_due_at_property だけを
// 明示的に許可した例外(旧onsite_payment_required/onsite_payment_amountから改名) ----------------

await check("payment_due_at_property/amount_due_at_property are explicitly NOT rejected (the one approved financial exception)", async () => {
  const rawRooms = fixture.dates["2026-08-30"].cleaning.rooms;
  const rooms = mergeCleaningOverrides(rawRooms, {});
  // the real fixture (room 401) already carries payment_due_at_property=true /
  // amount_due_at_property=18000 nested inside arriving_guest — confirm both guards
  // pass without throwing.
  const room401 = rooms.find((r) => r.room_number === "401");
  assert.equal(room401.arriving_guest.payment_due_at_property, true);
  assert.equal(room401.arriving_guest.amount_due_at_property, 18000);
  assert.doesNotThrow(() => assertNoFinancialKeys({ date: "2026-08-30", rooms }));
  assert.doesNotThrow(() => assertNoForbiddenCleaningKeys({ date: "2026-08-30", rooms }));
});

await check("every OTHER financial key is still rejected even inside a guest object that also carries amount_due_at_property", async () => {
  const rawRooms = fixture.dates["2026-08-30"].cleaning.rooms;
  const rooms = mergeCleaningOverrides(rawRooms, {});
  const room401 = rooms.find((r) => r.room_number === "401");
  for (const forbidden of ["gross_revenue", "net_revenue", "commission", "adr", "revpar", "invoiceItems"]) {
    const tampered = {
      date: "2026-08-30",
      rooms: rooms.map((r) => (r.room_number === "401"
        ? { ...r, arriving_guest: { ...room401.arriving_guest, [forbidden]: 1 } }
        : r)),
    };
    assert.throws(() => assertNoForbiddenCleaningKeys(tampered), new RegExp(forbidden, "i"),
      `${forbidden} must still be rejected`);
  }
});

console.log(`\n${passed} financialGuard checks passed`);
