// financialGuard.js — runtime + test guard confirming NO financial data ever
// reaches this staff app. This worker's data contract intentionally excludes
// revenue/price/commission/ADR/RevPAR/payment/invoice fields; this module
// lets every page assert that at runtime (call right after fetch(), before
// rendering) rather than trusting the contract silently.

// Mirrors the forbidden-key list agreed with the Python side.
export const FORBIDDEN_FINANCIAL_KEYS = [
  "revenue",
  "gross_revenue",
  "net_revenue",
  "price",
  "ota_commission",
  "commission",
  "tax_amount",
  "adr",
  "revpar",
  "payment_status",
  "invoice_status",
  "invoiceItems",
];

const FORBIDDEN_SET = new Set(FORBIDDEN_FINANCIAL_KEYS.map((k) => k.toLowerCase()));

// Recursively walks obj/array and throws the first time it finds an object
// key (at any depth) that case-insensitively matches an entry in
// FORBIDDEN_FINANCIAL_KEYS. Returns normally (no return value) when clean.
export function assertNoFinancialKeys(value, path = "$") {
  if (value == null || typeof value !== "object") return;
  if (Array.isArray(value)) {
    value.forEach((item, i) => assertNoFinancialKeys(item, `${path}[${i}]`));
    return;
  }
  for (const key of Object.keys(value)) {
    if (FORBIDDEN_SET.has(key.toLowerCase())) {
      throw new Error(`assertNoFinancialKeys: forbidden financial key "${key}" found at ${path}.${key}`);
    }
    assertNoFinancialKeys(value[key], `${path}.${key}`);
  }
}

// Cleaning-specific forbidden-key list: mirrors the Python side's
// CLEANING_FORBIDDEN_KEYS (src/yuge_finance/ops/schema.py) — the financial
// keys above PLUS PII fields that have no business being in the Cleaning DTO
// (phone/address/email/passport/nationality/etc.). The Cleaning DTO is
// enforced by construction on the Python side to never carry these, but this
// lets every page that consumes /api/cleaning assert it at runtime too,
// exactly like assertNoFinancialKeys is already called after /api/daily-ops
// fetches.
//
// 2026-09 exception (explicitly approved): onsite_payment_required /
// onsite_payment_amount are the only financial fields allowed in the
// Cleaning DTO ("amount owed on-site" as operational data). They do not
// collide with any entry in this list (different vocabulary from
// revenue/ADR/RevPAR/commission/invoice detail etc.), so no change to the
// list itself was required — recorded here for auditability.
export const CLEANING_FORBIDDEN_KEYS = [
  ...FORBIDDEN_FINANCIAL_KEYS,
  "phone",
  "mobile",
  "address",
  "postcode",
  "prefecture",
  "city",
  "rest",
  "email",
  "passport",
  "nationality",
  "country",
  "notes",
  "rate",
  "amount",
];

const CLEANING_FORBIDDEN_SET = new Set(CLEANING_FORBIDDEN_KEYS.map((k) => k.toLowerCase()));

export function assertNoForbiddenCleaningKeys(value, path = "$") {
  if (value == null || typeof value !== "object") return;
  if (Array.isArray(value)) {
    value.forEach((item, i) => assertNoForbiddenCleaningKeys(item, `${path}[${i}]`));
    return;
  }
  for (const key of Object.keys(value)) {
    if (CLEANING_FORBIDDEN_SET.has(key.toLowerCase())) {
      throw new Error(`assertNoForbiddenCleaningKeys: forbidden key "${key}" found at ${path}.${key}`);
    }
    assertNoForbiddenCleaningKeys(value[key], `${path}.${key}`);
  }
}
