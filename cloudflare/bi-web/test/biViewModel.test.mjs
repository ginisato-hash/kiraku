// biViewModel.js の軽量テスト（node実行・wrangler不要）。
import assert from "node:assert";
import { buildBiViewModel, DEPRECATED_FIELDS, _internal } from "../public/biViewModel.js";

let passed = 0;
async function check(name, fn) { await fn(); passed++; console.log("ok -", name); }

const baseSnapshot = {
  month: "2026-07",
  beds24_stay_month_revenue_excluding_cancelled: 1057943,
  cash_operating_breakeven_revenue: 2009657,
  cash_operating_breakeven_achievement_rate: 0.2536, // 大幅未達水準
  cash_revenue_gap_to_breakeven: 951714,
  required_remaining_revenue_per_day: 41379,
  gop_after_mc: -695941,
  month_elapsed_rate: 0.276,
  projected_month_end_bep_achievement_rate: 0.842,
  booking_pace_status: "green",
  booking_pace_label: "グリーン",
  booking_pace_reason: "現時点の予約済み売上がキャッシュBEP以上です。",
  revenue_data_status: "速報",
  breakeven_model_status: "大幅未達",
  pace_model_status: "推計",
  labor_model_status: "推計",
  debt_service_status: "予定表未投入",
  opening_balance_status: "会計士確定",
  // deprecated（primary表示には使わない。JSONには残る）
  breakeven_revenue_current_structure: 999999,
  revenue_reconciliation_difference: 123456,
};

await check("buildBiViewModel returns primaryCards", async () => {
  const vm = buildBiViewModel(baseSnapshot, { generated_at_jst: "2026-07-08T00:00:00+09:00" });
  assert.ok(Array.isArray(vm.primaryCards));
  assert.ok(vm.primaryCards.length >= 7);
});

await check("booking pace card has size=hero", async () => {
  const vm = buildBiViewModel(baseSnapshot, {});
  const paceCard = vm.primaryCards.find((c) => c.id === "booking-pace");
  assert.equal(paceCard.size, "hero");
});

await check("primaryCards do not surface deprecated field values", async () => {
  const vm = buildBiViewModel(baseSnapshot, {});
  const json = JSON.stringify(vm.primaryCards);
  assert.ok(!json.includes("999999"), "must not surface breakeven_revenue_current_structure value");
  assert.ok(!json.includes("123456"), "must not surface revenue_reconciliation_difference value");
});

await check("DEPRECATED_FIELDS list matches spec", async () => {
  assert.deepEqual(DEPRECATED_FIELDS,
    ["breakeven_revenue_current_structure", "revenue_reconciliation_difference"]);
});

await check("status chip labels are Japanese, not raw field names", async () => {
  const vm = buildBiViewModel(baseSnapshot, {});
  const labels = vm.statusChips.map((c) => c.label);
  assert.deepEqual(labels, ["売上", "損益", "ペース", "人件費", "返済", "期初BS"]);
  for (const raw of ["revenue_data_status", "breakeven_model_status", "pace_model_status",
    "labor_model_status", "debt_service_status", "opening_balance_status"]) {
    assert.ok(!labels.includes(raw), `chip label must not be raw field name: ${raw}`);
  }
});

await check("missing values render as —", async () => {
  const sparse = { month: "2026-08" };
  const vm = buildBiViewModel(sparse, {});
  const revCard = vm.primaryCards.find((c) => c.id === "beds24-revenue");
  assert.equal(revCard.value, "—");
  const paceCard = vm.primaryCards.find((c) => c.id === "booking-pace");
  assert.equal(paceCard.value, "判定不可");
});

await check("details is a structured array with id/title/summary/rows", async () => {
  const vm = buildBiViewModel(baseSnapshot, {});
  assert.ok(Array.isArray(vm.details));
  for (const section of vm.details) {
    assert.ok("id" in section);
    assert.ok("title" in section);
    assert.ok("rows" in section);
    assert.ok(Array.isArray(section.rows));
  }
  const ids = vm.details.map((d) => d.id);
  assert.deepEqual(ids, ["breakeven", "pace", "labor", "variable-cost", "mc-gop", "finance", "validation"]);
});

await check("debt_service_status 予定表未投入 produces a note", async () => {
  const vm = buildBiViewModel(baseSnapshot, {});
  const hasNote = vm.notes.some((n) => n.text.includes("返済予定表未投入"));
  assert.ok(hasNote);
});

await check("大幅未達 achievement can coexist with pace green", async () => {
  const vm = buildBiViewModel(baseSnapshot, {});
  const achievementCard = vm.primaryCards.find((c) => c.id === "achievement");
  const paceCard = vm.primaryCards.find((c) => c.id === "booking-pace");
  assert.equal(achievementCard.badge, "大幅未達");
  assert.equal(paceCard.value, "グリーン");
  // banner のtoneはpace優先（達成率が低くても赤一色にしない）
  assert.equal(vm.paceComment.tone, "green");
});

await check("_internal helpers format yen/pct/ratio correctly", async () => {
  assert.equal(_internal.yen(1000), "¥1,000");
  assert.equal(_internal.yen(-947641), "-¥947,641");
  assert.equal(_internal.yen(null), "—");
  assert.equal(_internal.pct(0.5), "50.0%");
  assert.equal(_internal.ratio(0.25), "0.25倍");
});

console.log(`\n${passed} biViewModel checks passed`);
