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
  beds24_onsite_payment_revenue_included: 0,
  beds24_onsite_payment_booking_count: 0,
  beds24_onsite_payment_candidate_amount: 120000,
  beds24_onsite_payment_candidate_count: 2,
  beds24_onsite_payment_logic_status: "payment_method_only_not_revenue",
  beds24_onsite_payment_logic_note: "現地決済はinvoiceItems type=payment（決済手段）としてのみ出現し、priceに既に含まれているため加算していません。",
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

await check("booking pace card is a half-width card like the others (2列×4段グリッドを崩さない)", async () => {
  const vm = buildBiViewModel(baseSnapshot, {});
  const paceCard = vm.primaryCards.find((c) => c.id === "booking-pace");
  assert.notEqual(paceCard.size, "hero", "no card should span both columns anymore");
  assert.equal(paceCard.label, "予約ペース評価");
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

await check("top card count does not grow beyond existing 8 (7 + ADR)", async () => {
  const vm = buildBiViewModel(baseSnapshot, {});
  assert.equal(vm.primaryCards.length, 8);
});

await check("beds24 revenue card uses beds24_revenue_net_for_bi", async () => {
  const vm = buildBiViewModel(baseSnapshot, {});
  const revCard = vm.primaryCards.find((c) => c.id === "beds24-revenue");
  assert.equal(revCard.value, "¥500,000"); // beds24_revenue_net_for_bi
});

await check("beds24 revenue card shows point/onsite/cancel wording (not coupon加算)", async () => {
  const vm = buildBiViewModel(baseSnapshot, {});
  const revCard = vm.primaryCards.find((c) => c.id === "beds24-revenue");
  assert.ok(revCard.helper.includes("ポイント・現地決済確認"),
    "現地決済加算0の実態を「確認」と表現し、誤って加算している印象を避ける");
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

// ---------------- 現地決済/現地払い ----------------
await check("revenue-logic detail shows onsite payment added amount and candidate amount", async () => {
  const vm = buildBiViewModel(baseSnapshot, {});
  const section = vm.details.find((d) => d.id === "revenue-logic");
  const addedRow = section.rows.find(([k]) => k === "現地決済加算額");
  const candidateRow = section.rows.find(([k]) => k === "現地決済候補額");
  assert.ok(addedRow, "現地決済加算額の行が無い");
  assert.equal(addedRow[1], "¥0");
  assert.ok(candidateRow, "現地決済候補額の行が無い");
  assert.equal(candidateRow[1], "¥120,000");
});

await check("revenue-logic detail shows onsite payment logic status and note", async () => {
  const vm = buildBiViewModel(baseSnapshot, {});
  const section = vm.details.find((d) => d.id === "revenue-logic");
  const statusRow = section.rows.find(([k]) => k === "現地決済ロジック状態");
  const noteRow = section.rows.find(([k]) => k === "現地決済ロジック注記");
  assert.ok(statusRow);
  assert.equal(statusRow[1], "決済手段のみ（収入ではない）");
  assert.ok(noteRow);
  assert.ok(noteRow[1].includes("type=payment"));
});

await check("revenue-logic detail shows onsite payment candidate count", async () => {
  const vm = buildBiViewModel(baseSnapshot, {});
  const section = vm.details.find((d) => d.id === "revenue-logic");
  const countRow = section.rows.find(([k]) => k === "現地決済対象件数");
  assert.ok(countRow);
  assert.equal(countRow[1], "2");
});

await check("onsite payment section does not increase top card count (still 8)", async () => {
  const vm = buildBiViewModel(baseSnapshot, {});
  assert.equal(vm.primaryCards.length, 8);
});

await check("today new booking details remain intact alongside onsite payment fields", async () => {
  const vm = buildBiViewModel({
    ...baseSnapshot, today_new_booking_count: 1, today_new_booking_revenue: 12000,
    today_new_booking_logic_status: "ok",
    today_new_booking_details: [{ booking_id: "1", checkin: "2026-07-10", checkout: "2026-07-11",
      guest_name: "Tanaka Ichiro", revenue_for_target_month: 12000 }],
  }, {});
  assert.equal(vm.dailyNewBookings.hasDetails, true);
  assert.equal(vm.dailyNewBookings.details[0].guestName, "Tanaka Ichiro");
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

// ---------------- 銀行CFデータ出所 (sticky bank field source) ----------------
await check("bank-actuals shows 今回取込 for bank_fields_source=current_import", async () => {
  const vm = buildBiViewModel({ ...baseSnapshot, bank_fields_source: "current_import" }, {});
  const section = vm.details.find((d) => d.id === "bank-actuals");
  assert.ok(section.rows.some(([k, v]) => k === "銀行CFデータ出所" && v === "今回取込"));
});

await check("bank-actuals shows 前回公開データを維持 and a note for previous_r2_snapshot", async () => {
  const vm = buildBiViewModel({
    ...baseSnapshot, bank_fields_source: "previous_r2_snapshot",
    bank_fields_preserved_note: "GitHub Actions更新時に銀行CSVは再取込されないため、直近公開済みの銀行CF summaryを維持しています。",
  }, {});
  const section = vm.details.find((d) => d.id === "bank-actuals");
  assert.ok(section.rows.some(([k, v]) => k === "銀行CFデータ出所" && v === "前回公開データを維持"));
  assert.ok(section.rows.some(([k, v]) => k === "データ出所の補足" && v.includes("GitHub Actions更新時")));
});

await check("bank-actuals shows 未取込 for bank_fields_source=not_available with no extra note", async () => {
  const vm = buildBiViewModel({ ...baseSnapshot, bank_fields_source: "not_available" }, {});
  const section = vm.details.find((d) => d.id === "bank-actuals");
  assert.ok(section.rows.some(([k, v]) => k === "銀行CFデータ出所" && v === "未取込"));
  assert.ok(!section.rows.some(([k]) => k === "データ出所の補足"));
});

await check("bank fields source display does not increase top card count", async () => {
  const vm = buildBiViewModel({ ...baseSnapshot, bank_fields_source: "previous_r2_snapshot" }, {});
  assert.equal(vm.primaryCards.length, 8);
});

await check("month selector / daily new bookings / booking pace remain intact alongside bank source field", async () => {
  const manifestForBankSourceCheck = {
    default_month: "2026-07", available_months: ["2026-07", "2026-08"],
    months_with_any_booking: ["2026-07", "2026-08"],
  };
  const vm = buildBiViewModel({ ...baseSnapshot, bank_fields_source: "previous_r2_snapshot" },
    manifestForBankSourceCheck, null, null, { selectedMonth: "2026-08" });
  assert.ok(vm.header.monthOptions.length > 0);
  assert.ok(vm.dailyNewBookings.label === "本日の新規予約");
  const paceCard = vm.primaryCards.find((c) => c.id === "booking-pace");
  assert.equal(paceCard.value, "グリーン");
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

await check("bank sections do not increase primary card count (still 8)", async () => {
  const vm = buildBiViewModel(baseSnapshot, {});
  assert.equal(vm.primaryCards.length, 8);
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

await check("month selector additions do not change top card count (still 8)", async () => {
  const vm = buildBiViewModel(baseSnapshot, manifestWithMonths, null, null, { selectedMonth: "2026-08" });
  assert.equal(vm.primaryCards.length, 8);
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

// ---------------- 本日の新規予約 summary strip ----------------
await check("dailyNewBookings is built with count/revenue formatted", async () => {
  const vm = buildBiViewModel({
    ...baseSnapshot,
    today_new_booking_count: 3,
    today_new_booking_revenue: 84000,
    today_new_booking_logic_status: "ok",
  }, manifestWithMonths, null, null, { selectedMonth: "2026-08" });
  assert.equal(vm.dailyNewBookings.label, "本日の新規予約");
  assert.equal(vm.dailyNewBookings.count, "3件");
  assert.equal(vm.dailyNewBookings.revenue, "¥84,000");
  assert.equal(vm.dailyNewBookings.targetMonthLabel, "2026年8月宿泊分");
});

await check("dailyNewBookings tone is green when count > 0", async () => {
  const vm = buildBiViewModel({
    ...baseSnapshot, today_new_booking_count: 1, today_new_booking_revenue: 12000,
    today_new_booking_logic_status: "ok",
  }, manifestWithMonths);
  assert.equal(vm.dailyNewBookings.tone, "green");
});

await check("dailyNewBookings tone is neutral when count = 0", async () => {
  const vm = buildBiViewModel({
    ...baseSnapshot, today_new_booking_count: 0, today_new_booking_revenue: 0,
    today_new_booking_logic_status: "ok",
  }, manifestWithMonths);
  assert.equal(vm.dailyNewBookings.tone, "neutral");
  assert.ok(vm.dailyNewBookings.helper.includes("まだありません"));
});

await check("dailyNewBookings tone is amber and shows 判定不可 when logic status is field-missing", async () => {
  const vm = buildBiViewModel({
    ...baseSnapshot, today_new_booking_logic_status: "created_at_field_missing",
  }, manifestWithMonths);
  assert.equal(vm.dailyNewBookings.tone, "amber");
  assert.equal(vm.dailyNewBookings.count, "判定不可");
  assert.ok(vm.dailyNewBookings.helper.includes("作成日時"));
});

await check("dailyNewBookings falls back to amber/判定不可 when snapshot has no logic status at all", async () => {
  const vm = buildBiViewModel(baseSnapshot, manifestWithMonths);
  assert.equal(vm.dailyNewBookings.tone, "amber");
});

await check("dailyNewBookings does not affect top card count (still 8)", async () => {
  const vm = buildBiViewModel({
    ...baseSnapshot, today_new_booking_count: 3, today_new_booking_revenue: 84000,
    today_new_booking_logic_status: "ok",
  }, manifestWithMonths, null, null, { selectedMonth: "2026-08" });
  assert.equal(vm.primaryCards.length, 8);
});

await check("dailyNewBookings switches value when selectedMonth/snapshot changes", async () => {
  const vmJuly = buildBiViewModel({
    ...baseSnapshot, today_new_booking_count: 1, today_new_booking_revenue: 12000,
    today_new_booking_logic_status: "ok",
  }, manifestWithMonths, null, null, { selectedMonth: "2026-07" });
  const vmAugust = buildBiViewModel({
    ...baseSnapshot, today_new_booking_count: 3, today_new_booking_revenue: 84000,
    today_new_booking_logic_status: "ok",
  }, manifestWithMonths, null, null, { selectedMonth: "2026-08" });
  assert.equal(vmJuly.dailyNewBookings.count, "1件");
  assert.equal(vmAugust.dailyNewBookings.count, "3件");
  assert.notEqual(vmJuly.dailyNewBookings.targetMonthLabel, vmAugust.dailyNewBookings.targetMonthLabel);
});

// ---------------- 本日新規予約 詳細drilldown ----------------
const sampleBookingDetails = [
  { booking_id: "1", checkin: "2026-08-10", checkout: "2026-08-12", guest_name: "Yamada Taro",
    revenue_for_target_month: 24000, total_booking_revenue: 24000, target_month_nights: 2,
    total_nights: 2, room_name: "201", status: "confirmed", created_at_jst: "2026-08-10T09:00:00+09:00" },
];

await check("dailyNewBookings.details is built from snapshot.today_new_booking_details", async () => {
  const vm = buildBiViewModel({
    ...baseSnapshot, today_new_booking_count: 1, today_new_booking_revenue: 24000,
    today_new_booking_logic_status: "ok", today_new_booking_details: sampleBookingDetails,
  }, {});
  assert.equal(vm.dailyNewBookings.details.length, 1);
  const d = vm.dailyNewBookings.details[0];
  assert.equal(d.checkin, "2026-08-10");
  assert.equal(d.checkout, "2026-08-12");
  assert.equal(d.guestName, "Yamada Taro");
  assert.equal(d.revenue, "¥24,000");
  assert.equal(vm.dailyNewBookings.hasDetails, true);
});

// ---------------- OTA名・部屋タイプ・部屋変更履歴 ----------------
const sampleDetailWithRoomInfo = {
  booking_id: "2", checkin: "2026-08-14", checkout: "2026-08-15", guest_name: "伊東 昌宏",
  revenue_for_target_month: 36000, total_booking_revenue: 36000, target_month_nights: 1,
  total_nights: 1, status: "confirmed", created_at_jst: "2026-08-14T09:00:00+09:00",
  ota_name: "じゃらん", booking_source_raw: "じゃらんnet",
  room_id: "685761", room_type: "シングル｜客室トイレ付", room_type_key: "single_toilet",
  current_room_type: "シングル｜客室トイレ付", current_room_type_key: "single_toilet",
  current_room_id: "685761", original_room_type: null, original_room_type_key: null,
  room_change_history_status: "not_available", room_change_history: [],
};

await check("dailyNewBookings.details exposes ota_name/booking_source_raw", async () => {
  const vm = buildBiViewModel({
    ...baseSnapshot, today_new_booking_count: 1, today_new_booking_revenue: 36000,
    today_new_booking_logic_status: "ok", today_new_booking_details: [sampleDetailWithRoomInfo],
  }, {});
  const d = vm.dailyNewBookings.details[0];
  assert.equal(d.otaName, "じゃらん");
  assert.equal(d.bookingSourceRaw, "じゃらんnet");
});

await check("dailyNewBookings.details exposes room_type/current_room_type/original_room_type", async () => {
  const vm = buildBiViewModel({
    ...baseSnapshot, today_new_booking_count: 1, today_new_booking_revenue: 36000,
    today_new_booking_logic_status: "ok", today_new_booking_details: [sampleDetailWithRoomInfo],
  }, {});
  const d = vm.dailyNewBookings.details[0];
  assert.equal(d.roomType, "シングル｜客室トイレ付");
  assert.equal(d.currentRoomType, "シングル｜客室トイレ付");
  assert.equal(d.originalRoomType, null);
});

await check("dailyNewBookings.details reports 部屋変更履歴取得不可 when status is not_available", async () => {
  const vm = buildBiViewModel({
    ...baseSnapshot, today_new_booking_count: 1, today_new_booking_revenue: 36000,
    today_new_booking_logic_status: "ok", today_new_booking_details: [sampleDetailWithRoomInfo],
  }, {});
  const d = vm.dailyNewBookings.details[0];
  assert.equal(d.hasRoomChange, false);
  assert.equal(d.roomChangeSummary, "変更履歴取得不可");
});

await check("dailyNewBookings.details reports room change count in summary when history exists", async () => {
  const detailWithChange = {
    ...sampleDetailWithRoomInfo, booking_id: "3",
    room_change_history_status: "available",
    room_change_history: [{
      changed_at: "2026-08-13T10:00:00+09:00", from_room_type: "シングル｜客室トイレ付",
      to_room_type: "ツイン｜客室トイレ付", from_room_id: "685761", to_room_id: "686762",
      changed_by: "staff", raw_note: "満室のため変更",
    }],
  };
  const vm = buildBiViewModel({
    ...baseSnapshot, today_new_booking_count: 1, today_new_booking_revenue: 36000,
    today_new_booking_logic_status: "ok", today_new_booking_details: [detailWithChange],
  }, {});
  const d = vm.dailyNewBookings.details[0];
  assert.equal(d.hasRoomChange, true);
  assert.equal(d.roomChangeSummary, "部屋変更 1件");
  assert.equal(d.roomChangeHistory.length, 1);
  assert.equal(d.roomChangeHistory[0].toRoomType, "ツイン｜客室トイレ付");
  assert.equal(d.roomChangeHistory[0].rawNote, "満室のため変更");
});

await check("dailyNewBookings.details does not leak PII fields for the new OTA/room-type additions", async () => {
  const vm = buildBiViewModel({
    ...baseSnapshot, today_new_booking_count: 1, today_new_booking_revenue: 36000,
    today_new_booking_logic_status: "ok", today_new_booking_details: [sampleDetailWithRoomInfo],
  }, {});
  const json = JSON.stringify(vm.dailyNewBookings.details[0]);
  for (const forbidden of ["email", "phone", "address", "firstName", "lastName", "passport"]) {
    assert.ok(!json.toLowerCase().includes(forbidden.toLowerCase()), `must not leak ${forbidden}`);
  }
});

await check("dailyNewBookings.guestName defaults to 氏名未取得 when missing", async () => {
  const vm = buildBiViewModel({
    ...baseSnapshot, today_new_booking_count: 1, today_new_booking_revenue: 24000,
    today_new_booking_logic_status: "ok",
    today_new_booking_details: [{ ...sampleBookingDetails[0], guest_name: "" }],
  }, {});
  assert.equal(vm.dailyNewBookings.details[0].guestName, "氏名未取得");
});

await check("dailyNewBookings.hasDetails is false and details empty when count is 0", async () => {
  const vm = buildBiViewModel({
    ...baseSnapshot, today_new_booking_count: 0, today_new_booking_revenue: 0,
    today_new_booking_logic_status: "ok", today_new_booking_details: [],
  }, {});
  assert.equal(vm.dailyNewBookings.hasDetails, false);
  assert.deepEqual(vm.dailyNewBookings.details, []);
});

await check("dailyNewBookings has detailsUnavailableNote when logic status is field-missing", async () => {
  const vm = buildBiViewModel({
    ...baseSnapshot, today_new_booking_logic_status: "created_at_field_missing",
  }, {});
  assert.equal(vm.dailyNewBookings.hasDetails, false);
  assert.ok(vm.dailyNewBookings.detailsUnavailableNote.includes("予約作成日時を確認できない"));
});

await check("dailyNewBookings.details swap when selectedMonth/snapshot changes", async () => {
  const vmJuly = buildBiViewModel({
    ...baseSnapshot, today_new_booking_count: 1, today_new_booking_revenue: 12000,
    today_new_booking_logic_status: "ok",
    today_new_booking_details: [{ ...sampleBookingDetails[0], booking_id: "july-1" }],
  }, manifestWithMonths, null, null, { selectedMonth: "2026-07" });
  const vmAugust = buildBiViewModel({
    ...baseSnapshot, today_new_booking_count: 1, today_new_booking_revenue: 24000,
    today_new_booking_logic_status: "ok",
    today_new_booking_details: [{ ...sampleBookingDetails[0], booking_id: "august-1" }],
  }, manifestWithMonths, null, null, { selectedMonth: "2026-08" });
  assert.equal(vmJuly.dailyNewBookings.details[0].bookingId, "july-1");
  assert.equal(vmAugust.dailyNewBookings.details[0].bookingId, "august-1");
});

await check("dailyNewBookings details drilldown does not change top card count (still 8)", async () => {
  const vm = buildBiViewModel({
    ...baseSnapshot, today_new_booking_count: 1, today_new_booking_revenue: 24000,
    today_new_booking_logic_status: "ok", today_new_booking_details: sampleBookingDetails,
  }, {});
  assert.equal(vm.primaryCards.length, 8);
});

// ---------------- 2026-08「当日売上カード」回帰テスト ----------------
// backend側(calculate_today_new_bookings_for_month)が同日キャンセル(50,000円)を
// 二重控除していた実バグの再現データ(修正後の正しい値: 57,978円)。
// このカードはsnapshotの値をそのまま表示するだけなので、backendが正しい値を返す限り
// カードも正しく表示される。逆に言えばbackendが壊れると気づかずそのまま表示するため、
// backend側のテスト(test_today_new_bookings.py)と対にして固定する。
const augustDetails2026 = [
  { booking_id: "89595831", checkin: "2026-08-04", checkout: "2026-08-06", guest_name: "FENG ZHU",
    revenue_for_target_month: 21978, total_booking_revenue: 21978, target_month_nights: 2,
    total_nights: 2, room_name: null, status: "confirmed", created_at_jst: "2026-07-10T13:10:14+09:00" },
  { booking_id: "89585384", checkin: "2026-08-14", checkout: "2026-08-15", guest_name: "伊東 昌宏",
    revenue_for_target_month: 36000, total_booking_revenue: 36000, target_month_nights: 1,
    total_nights: 1, room_name: null, status: "confirmed", created_at_jst: "2026-07-10T05:40:14+09:00" },
];

await check("dailyNewBookings.revenue matches sum of details for 2026-08 (regression: was 7978, must be 57978)", async () => {
  const vm = buildBiViewModel({
    ...baseSnapshot, month: "2026-08", target_month: "2026-08",
    today_new_booking_count: 2, today_new_booking_revenue: 57978,
    today_new_booking_logic_status: "ok", today_new_booking_details: augustDetails2026,
  }, manifestWithMonths, null, null, { selectedMonth: "2026-08" });
  const detailSum = augustDetails2026.reduce((acc, d) => acc + d.revenue_for_target_month, 0);
  assert.equal(detailSum, 57978);
  assert.equal(vm.dailyNewBookings.revenue, "¥57,978");
  assert.equal(vm.dailyNewBookings.count, "2件");
  assert.equal(vm.dailyNewBookings.details.length, 2);
});

await check("switching from 2026-07 to 2026-08 does not carry over the previous month's today revenue", async () => {
  const vmJuly = buildBiViewModel({
    ...baseSnapshot, month: "2026-07", target_month: "2026-07",
    today_new_booking_count: 1, today_new_booking_revenue: 10189,
    today_new_booking_logic_status: "ok",
    today_new_booking_details: [{ ...sampleBookingDetails[0], booking_id: "89582827", revenue_for_target_month: 10189 }],
  }, manifestWithMonths, null, null, { selectedMonth: "2026-07" });
  const vmAugust = buildBiViewModel({
    ...baseSnapshot, month: "2026-08", target_month: "2026-08",
    today_new_booking_count: 2, today_new_booking_revenue: 57978,
    today_new_booking_logic_status: "ok", today_new_booking_details: augustDetails2026,
  }, manifestWithMonths, null, null, { selectedMonth: "2026-08" });
  assert.equal(vmJuly.dailyNewBookings.revenue, "¥10,189");
  assert.equal(vmAugust.dailyNewBookings.revenue, "¥57,978");
  assert.notEqual(vmJuly.dailyNewBookings.revenue, vmAugust.dailyNewBookings.revenue);
});

// ---------------- ADRカード ----------------
await check("primaryCards includes an ADR card with formatted value/helper/note", async () => {
  const vm = buildBiViewModel({
    ...baseSnapshot, adr_gross: 18450, sold_room_nights: 65, available_room_nights: 114,
    occupancy_rate_month: 57.0,
  }, {});
  const adrCard = vm.primaryCards.find((c) => c.id === "adr");
  assert.ok(adrCard, "ADR card must exist");
  assert.equal(adrCard.label, "ADR");
  assert.equal(adrCard.value, "¥18,450");
  assert.equal(adrCard.helper, "販売室泊 65 / 提供室泊 114");
  assert.equal(adrCard.note, "稼働率 57.0%");
});

await check("ADR card sits immediately after the beds24-revenue card (2列レイアウトで隣接表示)", async () => {
  const vm = buildBiViewModel(baseSnapshot, {});
  const ids = vm.primaryCards.map((c) => c.id);
  assert.equal(vm.primaryCards.length, 8);
  const revIdx = ids.indexOf("beds24-revenue");
  const adrIdx = ids.indexOf("adr");
  assert.ok(revIdx >= 0 && adrIdx >= 0);
  assert.equal(adrIdx, revIdx + 1, "ADR should immediately follow the revenue card");
});

await check("ADR card shows データなし when sold_room_nights is 0", async () => {
  const vm = buildBiViewModel({ ...baseSnapshot, adr_gross: 0, sold_room_nights: 0 }, {});
  const adrCard = vm.primaryCards.find((c) => c.id === "adr");
  assert.equal(adrCard.value, "データなし");
  assert.equal(adrCard.note, null);
});

// ---------------- 部屋タイプ別 日別稼働率グラフ(view model) ----------------
const roomTypeChartSeries2026_08 = [
  { date: "2026-08-01", "シングル": 50.0, "ツイン": 30.0 },
  { date: "2026-08-02", "シングル": 0.0, "ツイン": 60.0 },
];

await check("roomTypeOccupancyChart builds dates/lines from selected snapshot series", async () => {
  const vm = buildBiViewModel({
    ...baseSnapshot, month: "2026-08", target_month: "2026-08",
    room_type_occupancy_chart_series: roomTypeChartSeries2026_08,
  }, {}, null, null, { selectedMonth: "2026-08" });
  const chart = vm.roomTypeOccupancyChart;
  assert.equal(chart.hasData, true);
  assert.deepEqual(chart.dates, ["2026-08-01", "2026-08-02"]);
  const single = chart.lines.find((l) => l.label === "シングル");
  const twin = chart.lines.find((l) => l.label === "ツイン");
  assert.deepEqual(single.points, [50.0, 0.0]);
  assert.deepEqual(twin.points, [30.0, 60.0]);
  assert.ok(single.color && twin.color && single.color !== twin.color, "legend colors must differ");
});

await check("roomTypeOccupancyChart is データなし(hasData=false) when series is empty", async () => {
  const vm = buildBiViewModel({ ...baseSnapshot, room_type_occupancy_chart_series: [] }, {});
  assert.equal(vm.roomTypeOccupancyChart.hasData, false);
  assert.deepEqual(vm.roomTypeOccupancyChart.lines, []);
});

await check("roomTypeOccupancyChart surfaces room_type_metrics_warnings", async () => {
  const vm = buildBiViewModel({
    ...baseSnapshot, room_type_occupancy_chart_series: roomTypeChartSeries2026_08,
    room_type_metrics_warnings: ["2026-08-12 ツインの稼働率が100%を超えています: sold=4 available=3"],
  }, {});
  assert.equal(vm.roomTypeOccupancyChart.warnings.length, 1);
});

// ---------------- 部屋タイプ別 売上構成(view model) ----------------
const roomTypeRevenueMixSample = [
  { room_type: "twin_toilet", room_type_label: "ツイン（トイレ）", revenue: 600000, share: 62.5,
    sold_room_nights: 35, adr: 17143 },
  { room_type: "single", room_type_label: "シングル", revenue: 120000, share: 12.5,
    sold_room_nights: 10, adr: 12000 },
];

await check("roomTypeRevenueMix formats revenue/share/sold nights/ADR per row", async () => {
  const vm = buildBiViewModel({ ...baseSnapshot, room_type_revenue_mix: roomTypeRevenueMixSample }, {});
  const mix = vm.roomTypeRevenueMix;
  assert.equal(mix.hasData, true);
  assert.equal(mix.rows.length, 2);
  const twin = mix.rows.find((r) => r.roomTypeLabel === "ツイン（トイレ）");
  assert.equal(twin.revenue, "¥600,000");
  assert.equal(twin.share, "62.5%");
  assert.equal(twin.soldRoomNights, "35泊");
  assert.equal(twin.adr, "¥17,143");
  assert.equal(twin.sharePercent, 62.5);
});

await check("roomTypeRevenueMix is データなし(hasData=false) when mix is empty", async () => {
  const vm = buildBiViewModel({ ...baseSnapshot, room_type_revenue_mix: [] }, {});
  assert.equal(vm.roomTypeRevenueMix.hasData, false);
  assert.deepEqual(vm.roomTypeRevenueMix.rows, []);
});

// ---------------- 月切替でroom metricsもselected snapshotを使う ----------------
await check("switching month uses that month's own room_type metrics (no cross-month leak)", async () => {
  const vmJuly = buildBiViewModel({
    ...baseSnapshot, month: "2026-07", target_month: "2026-07",
    adr_gross: 11000, sold_room_nights: 40,
    room_type_revenue_mix: [{ room_type: "single", room_type_label: "シングル",
      revenue: 100000, share: 100.0, sold_room_nights: 10, adr: 10000 }],
  }, manifestWithMonths, null, null, { selectedMonth: "2026-07" });
  const vmAugust = buildBiViewModel({
    ...baseSnapshot, month: "2026-08", target_month: "2026-08",
    adr_gross: 17581, sold_room_nights: 72,
    room_type_revenue_mix: roomTypeRevenueMixSample,
  }, manifestWithMonths, null, null, { selectedMonth: "2026-08" });
  assert.equal(vmJuly.primaryCards.find((c) => c.id === "adr").value, "¥11,000");
  assert.equal(vmAugust.primaryCards.find((c) => c.id === "adr").value, "¥17,581");
  assert.equal(vmJuly.roomTypeRevenueMix.rows.length, 1);
  assert.equal(vmAugust.roomTypeRevenueMix.rows.length, 2);
});

console.log(`\n${passed} biViewModel checks passed`);
