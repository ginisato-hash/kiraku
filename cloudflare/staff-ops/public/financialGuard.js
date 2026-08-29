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
