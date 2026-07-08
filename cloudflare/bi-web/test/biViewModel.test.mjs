// biViewModel.js の軽量テスト（node実行・wrangler不要）。
import assert from "node:assert";
import { buildBiViewModel, DEPRECATED_FIELDS, _internal } from "../public/biViewModel.js";

let passed = 0;
async function check(name, fn) { await fn(); passed++; console.log("ok -", name); }

const baseSnapshot = {
  month: "2026-07",
  beds24_stay_month_revenue_excluding_cancelled: 500000,
  beds24_revenue_gross_stay: 480000,
  beds24_point_revenue_included: 20000,
  beds24_point_booking_count: 2,
  beds24_coupon_discount_detected: true,
  beds24_coupon_discount_amount: 15000,
  beds24_coupon_discount_booking_count: 4,
  beds24_cancelled_revenue_excluded: 30000,
  beds24_revenue_net_for_bi: 500000,
  beds24_revenue_logic_version: "beds24_revenue_v3",
  beds24_revenue_logic_status: "point_added_from_invoice_items",
  beds24_revenue_logic_note: "test note",
  beds24_cancelled_booking_count: 3,
  // deprecated（意味が誤っていたため常に0。primary表示には使わない）
  beds24_coupon_revenue_included: 0,
  beds24_coupon_booking_count: 0,
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
  hot_spring_fee_monthly: 160000,
  bank_debt_service_placeholder: 400000,
  takamiya_monthly_equivalent_cash_out: 700000,
  standard_finance_required_cost: 2100000,
  full_debt_reserve_required_cost: 2800000,
  finance_breakeven_revenue: 2871000,
  finance_breakeven_achievement_rate: 0.17,
  full_debt_reserve_breakeven_revenue: 3829000,
  full_debt_reserve_breakeven_achievement_rate: 0.13,
  full_debt_reserve_revenue_gap_to_breakeven: 3329000,
  debt_service_status: "返済仮置き",
  debt_service_note: "返済予定表は未投入ですが、金融機関返済40万円を仮置きでfinance BEPに反映しています。高見屋返済70万円は別シナリオで表示しています。",
  opening_balance_status: "会計士確定",
  // deprecated（primary表示には使わない。JSONには残る）
  breakeven_revenue_current_structure: 999999,
  revenue_reconciliation_difference: 123456,
  // --- 銀行口座実績レイヤー（BI/分析専用）---
  bank_actual_latest_balance: 3052421,
  bank_actual_latest_balance_date: "2026-07-07",
  bank_source_period_start: "2026-05-01",
  bank_source_period_end: "2026-07-07",
  bank_total_deposits: 36144028,
  bank_total_withdrawals: 44075154,
  bank_net_cashflow: -7931126,
  bank_month_end_balance_observed: 3522735,
  bank_month_end_balance_date: "2026-06-30",
  bank_opening_balance_before_first_transaction: 10983547,
  bank_balance_reconciliation_status: "日付相違のため要確認（自動エラーではない）",
  accountant_bs_cash_balance: 7950646,
  bank_vs_accountant_difference: -201428,
  bank_csv_import_status: "imported",
  bank_csv_imported_rows: 89,
  bank_classification_review_required_count: 31,
  bank_fixed_cost_candidate_total: 7082555,
  bank_variable_cost_candidate_total: 724231,
  bank_debt_service_candidate_total: 269994,
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
  assert.deepEqual(ids, ["revenue-logic", "breakeven", "pace", "labor", "variable-cost",
    "mc-gop", "finance", "validation", "bank-actuals", "bank-cost-candidates"]);
});

await check("debt_service_status 返済仮置き produces a note", async () => {
  const vm = buildBiViewModel(baseSnapshot, {});
  const hasNote = vm.notes.some((n) => n.text.includes("仮置き"));
  assert.ok(hasNote);
});

await check("top card count does not grow beyond existing 7", async () => {
  const vm = buildBiViewModel(baseSnapshot, {});
  assert.equal(vm.primaryCards.length, 7);
});

await check("beds24 revenue card uses beds24_revenue_net_for_bi", async () => {
  const vm = buildBiViewModel(baseSnapshot, {});
  const revCard = vm.primaryCards.find((c) => c.id === "beds24-revenue");
  assert.equal(revCard.value, "¥500,000"); // beds24_revenue_net_for_bi
});

await check("beds24 revenue card shows point/cancel wording (not coupon加算)", async () => {
  const vm = buildBiViewModel(baseSnapshot, {});
  const revCard = vm.primaryCards.find((c) => c.id === "beds24-revenue");
  assert.ok(revCard.helper.includes("ポイント加算"));
  assert.ok(revCard.helper.includes("キャンセル除外"));
  assert.ok(!revCard.helper.includes("クーポン加算"), "クーポン加算という誤表記が残っている");
});

await check("revenue-logic detail shows point amount and cancel amount", async () => {
  const vm = buildBiViewModel(baseSnapshot, {});
  const section = vm.details.find((d) => d.id === "revenue-logic");
  const rowText = JSON.stringify(section.rows);
  assert.ok(rowText.includes("¥20,000"));  // ポイント加算額
  assert.ok(rowText.includes("¥30,000"));  // キャンセル除外額
  assert.ok(section.rows.some(([k]) => k === "ポイント加算額"));
  assert.ok(section.rows.some(([k]) => k === "キャンセル除外額"));
  assert.ok(!section.rows.some(([k]) => k === "クーポン加算額"), "クーポン加算額という誤表記が残っている");
});

await check("revenue-logic detail shows coupon as discount, not addition", async () => {
  const vm = buildBiViewModel(baseSnapshot, {});
  const section = vm.details.find((d) => d.id === "revenue-logic");
  assert.ok(section.rows.some(([k]) => k === "クーポン割引額"));
  assert.ok(section.rows.some(([k]) => k === "クーポン割引検出"));
  const discountRow = section.rows.find(([k]) => k === "クーポン割引額");
  assert.equal(discountRow[1], "¥15,000");
  const detectedRow = section.rows.find(([k]) => k === "クーポン割引検出");
  assert.equal(detectedRow[1], "あり");
});

await check("finance detail shows bank 400k and takamiya 700k placeholders", async () => {
  const vm = buildBiViewModel(baseSnapshot, {});
  const section = vm.details.find((d) => d.id === "finance");
  const rowText = JSON.stringify(section.rows);
  assert.ok(rowText.includes("¥400,000"));
  assert.ok(rowText.includes("¥700,000"));
  assert.ok(section.rows.some(([k]) => k === "高見屋返済込みBEP"));
});

await check("takamiya note appears when takamiya cash-out is present", async () => {
  const vm = buildBiViewModel(baseSnapshot, {});
  const hasTakamiyaNote = vm.notes.some((n) => n.text.includes("高見屋") && n.text.includes("一括返済"));
  assert.ok(hasTakamiyaNote);
});

await check("debt chip shows 仮置き (not raw field name)", async () => {
  const vm = buildBiViewModel(baseSnapshot, {});
  const debtChip = vm.statusChips.find((c) => c.label === "返済");
  assert.equal(debtChip.value, "返済仮置き");
});

await check("raw field names do not appear in rendered chip/card text", async () => {
  const vm = buildBiViewModel(baseSnapshot, {});
  const allText = JSON.stringify(vm.primaryCards) + JSON.stringify(vm.statusChips);
  for (const raw of ["beds24_stay_month_revenue_excluding_cancelled", "bank_debt_service_placeholder",
    "takamiya_monthly_equivalent_cash_out", "debt_service_status"]) {
    assert.ok(!allText.includes(raw), `raw field name leaked: ${raw}`);
  }
});

await check("booking pace green still works after revenue/debt changes", async () => {
  const vm = buildBiViewModel(baseSnapshot, {});
  const paceCard = vm.primaryCards.find((c) => c.id === "booking-pace");
  assert.equal(paceCard.value, "グリーン");
  assert.equal(paceCard.tone, "green");
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

await check("bank-actuals detail section renders with latest balance and import status", async () => {
  const vm = buildBiViewModel(baseSnapshot, {});
  const section = vm.details.find((d) => d.id === "bank-actuals");
  assert.ok(section, "bank-actuals section must exist");
  const rowText = JSON.stringify(section.rows);
  assert.ok(rowText.includes("¥3,052,421"), "latest bank balance must be shown");
  assert.ok(rowText.includes("2026-07-07"));
  assert.ok(rowText.includes("imported"), "import status must be shown");
  assert.ok(section.summary.includes("¥3,052,421"));
});

await check("bank-cost-candidates detail section shows fixed/variable candidate summary", async () => {
  const vm = buildBiViewModel(baseSnapshot, {});
  const section = vm.details.find((d) => d.id === "bank-cost-candidates");
  assert.ok(section, "bank-cost-candidates section must exist");
  const rowText = JSON.stringify(section.rows);
  assert.ok(rowText.includes("¥7,082,555"), "fixed cost candidate total must be shown");
  assert.ok(rowText.includes("¥724,231"), "variable cost candidate total must be shown");
  assert.ok(section.summary.includes("固定費候補"));
});

await check("bank sections do not increase primary card count (still 7)", async () => {
  const vm = buildBiViewModel(baseSnapshot, {});
  assert.equal(vm.primaryCards.length, 7);
});

// ---------------- 月選択ドロップダウン ----------------
const manifestWithMonths = {
  generated_at_jst: "2026-07-08T00:00:00+09:00",
  default_month: "2026-07",
  available_months: ["2026-07", "2026-08"],
  months_with_any_booking: ["2026-07", "2026-08"],
  months_with_active_booking: ["2026-07"],
};

await check("monthOptions are built from manifest.months_with_any_booking", async () => {
  const vm = buildBiViewModel(baseSnapshot, manifestWithMonths);
  assert.deepEqual(vm.header.monthOptions, [
    { value: "2026-07", label: "2026年7月" },
    { value: "2026-08", label: "2026年8月" },
  ]);
});

await check("selectedMonth defaults to snapshot.month when no options given", async () => {
  const vm = buildBiViewModel(baseSnapshot, manifestWithMonths);
  assert.equal(vm.header.selectedMonth, "2026-07");
});

await check("selectedMonth uses options.selectedMonth when provided", async () => {
  const vm = buildBiViewModel({ ...baseSnapshot, month: "2026-08" }, manifestWithMonths,
    null, null, { selectedMonth: "2026-08" });
  assert.equal(vm.header.selectedMonth, "2026-08");
});

await check("no month context note when selected month is the real current month", async () => {
  const vm = buildBiViewModel(baseSnapshot, manifestWithMonths, null, null,
    { selectedMonth: "2026-07", currentMonth: "2026-07" });
  assert.ok(!vm.notes.some((n) => n.text.includes("現在表示中")));
});

await check("past month note appears when selected month is before current month", async () => {
  const vm = buildBiViewModel({ ...baseSnapshot, month: "2026-06" }, manifestWithMonths, null, null,
    { selectedMonth: "2026-06", currentMonth: "2026-07" });
  const note = vm.notes.find((n) => n.text.includes("現在表示中"));
  assert.ok(note, "past month note must appear");
  assert.ok(note.text.includes("2026年6月"));
  assert.ok(note.text.includes("過去月の速報BIを表示しています"));
});

await check("future month note appears when selected month is after current month", async () => {
  const vm = buildBiViewModel({ ...baseSnapshot, month: "2026-08" }, manifestWithMonths, null, null,
    { selectedMonth: "2026-08", currentMonth: "2026-07" });
  const note = vm.notes.find((n) => n.text.includes("現在表示中"));
  assert.ok(note, "future month note must appear");
  assert.ok(note.text.includes("2026年8月"));
  assert.ok(note.text.includes("未来月の予約速報を表示しています"));
});

await check("monthOptions is empty array when manifest has no month fields", async () => {
  const vm = buildBiViewModel(baseSnapshot, { generated_at_jst: "x" });
  assert.deepEqual(vm.header.monthOptions, []);
});

await check("_internal.monthLabel formats YYYY-MM as Japanese year/month", async () => {
  assert.equal(_internal.monthLabel("2026-07"), "2026年7月");
  assert.equal(_internal.monthLabel("2026-12"), "2026年12月");
  assert.equal(_internal.monthLabel(null), "—");
});

await check("month selector additions do not change top card count (still 7)", async () => {
  const vm = buildBiViewModel(baseSnapshot, manifestWithMonths, null, null, { selectedMonth: "2026-08" });
  assert.equal(vm.primaryCards.length, 7);
});

await check("existing pace/finance/bank sections remain intact with month options present", async () => {
  const vm = buildBiViewModel(baseSnapshot, manifestWithMonths, null, null, { selectedMonth: "2026-07" });
  const paceCard = vm.primaryCards.find((c) => c.id === "booking-pace");
  assert.equal(paceCard.value, "グリーン");
  const financeSection = vm.details.find((d) => d.id === "finance");
  assert.ok(JSON.stringify(financeSection.rows).includes("¥400,000"));
  const bankSection = vm.details.find((d) => d.id === "bank-actuals");
  assert.ok(JSON.stringify(bankSection.rows).includes("¥3,052,421"));
});

console.log(`\n${passed} biViewModel checks passed`);
