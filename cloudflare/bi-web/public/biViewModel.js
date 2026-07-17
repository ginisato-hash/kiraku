// 喜らく 速報BI — bi_snapshot.json を表示用に整形する normalizer。
// app.js / components.js は生のフィールド名を直接大量に参照せず、この view model 経由で描画する。
//
// 重要な前提（画面側でも守る）：
//   - Beds24は速報KPI。確定会計売上ではない。
//   - 銀行入金月売上とBeds24宿泊月売上は同月比較しない。
//   - 「達成率」（現在のBEPに対する到達度）と「予約ペース」（月末着地見込み）は別指標。
//     大幅未達 かつ 予約ペース：グリーン/要注意 は矛盾しない。
//   - 元本返済は予定表未投入の間、推定しない。
//
// DEPRECATED（primary表示に使わない。JSONには残す）:
export const DEPRECATED_FIELDS = ["breakeven_revenue_current_structure", "revenue_reconciliation_difference"];

const DASH = "—";

function isNil(v) {
  return v == null || v === "";
}

function yen(n) {
  if (isNil(n)) return DASH;
  const v = Number(n);
  const sign = v < 0 ? "-" : "";
  return sign + "¥" + Math.abs(v).toLocaleString("ja-JP");
}

function pct(n, digits) {
  if (isNil(n)) return DASH;
  return (Number(n) * 100).toFixed(digits == null ? 1 : digits) + "%";
}

// 倍率は「0.25x」ではなく日本語で「0.25倍」
function ratio(n) {
  return isNil(n) ? DASH : Number(n).toFixed(2) + "倍";
}

function num(n) {
  return isNil(n) ? DASH : Number(n).toLocaleString("ja-JP");
}

function pick(obj, key, fallback) {
  const v = obj == null ? undefined : obj[key];
  return v === undefined || v === null ? fallback : v;
}

// 現在のBEP達成率のラベル（既存の「大幅未達」表記を維持）
function achievementStatus(rate) {
  if (isNil(rate)) return { key: "unknown", label: "判定不可", tone: "gray" };
  if (rate >= 1.0) return { key: "achieved", label: "達成", tone: "green" };
  if (rate >= 0.8) return { key: "close", label: "あと少し", tone: "amber" };
  return { key: "far", label: "大幅未達", tone: "red" };
}

const PACE_LABELS = { green: "グリーン", yellow: "要注意", red: "レッド", unknown: "判定不可" };
const PACE_TONE = { green: "green", yellow: "amber", red: "red", unknown: "gray" };

function paceInfo(snapshot) {
  const status = pick(snapshot, "booking_pace_status", "unknown");
  return {
    key: status,
    label: pick(snapshot, "booking_pace_label", PACE_LABELS[status] || DASH),
    reason: pick(snapshot, "booking_pace_reason", ""),
    tone: PACE_TONE[status] || "gray",
  };
}

// achievement + pace を組み合わせた短いコメント（insight banner用。20〜60字程度）
function buildPaceComment(achievement, pace) {
  const achText = achievement.key === "far" ? "現時点の達成率は大幅未達です。"
    : achievement.key === "close" ? "現時点の達成率はあと少しです。"
    : achievement.key === "achieved" ? "現時点でCash BEPを達成しています。"
    : "現時点の達成状況は判定できません。";

  let paceText;
  if (pace.key === "green") paceText = "予約ペースはグリーンです。";
  else if (pace.key === "yellow") paceText = "予約ペースは要注意です。";
  else if (pace.key === "red") paceText = "予約ペースもレッドです。追加予約が必要です。";
  else paceText = "予約ペースは判定できません。";

  // banner全体のtoneはpaceを優先する（達成率が低くてもpaceが良ければ赤一色にしない）
  const tone = pace.key === "unknown" ? "neutral" : pace.tone;
  return { text: (achText + paceText).slice(0, 80), tone };
}

// 銀行CF summaryの出所（GitHub Actions実行時はローカル銀行CSVが無いため、
// 直近公開snapshotから引き継ぐ場合がある。raw明細ではなく集計フィールドのみ）。
const BANK_FIELDS_SOURCE_LABELS = {
  current_import: "今回取込",
  previous_r2_snapshot: "前回公開データを維持",
  not_available: "未取込",
};

function bankFieldsSourceLabel(source) {
  return BANK_FIELDS_SOURCE_LABELS[source] || DASH;
}

// 現地決済/現地払いロジック状態の日本語表示（raw statusをそのまま出さない）。
const ONSITE_PAYMENT_STATUS_LABELS = {
  already_included_in_price: "priceに含まれているため加算なし",
  added_from_separate_charge: "別建て収入として加算済み",
  payment_method_only_not_revenue: "決済手段のみ（収入ではない）",
  field_missing: "該当データなし",
  candidate_not_selected: "候補はあるが未加算（要確認）",
};

function onsitePaymentStatusLabel(status) {
  return ONSITE_PAYMENT_STATUS_LABELS[status] || DASH;
}

// "2026-07" -> "2026年7月"（内部valueは常にYYYY-MM。表示は日本語）
function monthLabel(m) {
  if (!m || !/^\d{4}-\d{2}$/.test(m)) return DASH;
  const [y, mo] = m.split("-");
  return `${y}年${Number(mo)}月`;
}

function buildMonthOptions(manifest) {
  const months = (manifest && (manifest.months_with_any_booking || manifest.available_months)) || [];
  return months.map((m) => ({ value: m, label: monthLabel(m) }));
}

// 選択月が実際の「今月」でない場合の小さな補足（過去月/未来月レビュー時の注意喚起）。
function buildMonthContextNote(selectedMonth, realCurrentMonth) {
  if (!selectedMonth || !realCurrentMonth || selectedMonth === realCurrentMonth) return null;
  const label = monthLabel(selectedMonth);
  if (selectedMonth < realCurrentMonth) {
    return { text: `現在表示中：${label}。過去月の速報BIを表示しています。`, tone: "neutral" };
  }
  return { text: `現在表示中：${label}。未来月の予約速報を表示しています。`, tone: "neutral" };
}

function statusChip(label, value, tone) {
  return { label, value: value || DASH, tone: tone || "neutral" };
}

function toneForStatus(value, okValues, warnValues) {
  if (isNil(value)) return "neutral";
  if (okValues.includes(value)) return "green";
  if (warnValues.includes(value)) return "amber";
  return "red";
}

// raw system field名(revenue_data_status等)は画面に出さない。日本語の短いlabelへ変換する。
function buildStatusChips(s) {
  return [
    statusChip("売上", s.revenue_data_status,
      toneForStatus(s.revenue_data_status, ["会計確定"], ["入金実績あり", "精算明細待ち", "速報"])),
    statusChip("損益", s.breakeven_model_status,
      toneForStatus(s.breakeven_model_status, ["達成"], ["未達", "大幅未達"])),
    statusChip("ペース", s.pace_model_status,
      toneForStatus(s.pace_model_status, ["推計"], ["対象外"])),
    statusChip("人件費", s.labor_model_status,
      toneForStatus(s.labor_model_status, ["実績反映済", "推計"], ["要確認"])),
    statusChip("返済", s.debt_service_status,
      toneForStatus(s.debt_service_status, ["確定", "予定表投入済"],
        ["予定表未投入", "返済仮置き"])),
    statusChip("期初BS", s.opening_balance_status,
      toneForStatus(s.opening_balance_status, ["会計士確定"], [])),
  ];
}

function buildNotes(s) {
  const notes = [];
  if (s.debt_service_status === "予定表未投入") {
    notes.push({
      text: "返済予定表未投入のため、返済込みBEPは未完全です。元本返済はデータ確定後に反映します。",
      tone: "amber",
    });
  }
  if (s.debt_service_status === "返済仮置き") {
    notes.push({
      text: s.debt_service_note ||
        "返済予定表は未投入ですが、金融機関返済40万円を仮置きでfinance BEPに反映しています。高見屋返済70万円は別シナリオで表示しています。",
      tone: "amber",
    });
  }
  if (s.takamiya_monthly_equivalent_cash_out) {
    notes.push({
      text: "高見屋本体返済70万円は毎月返済とは限らず、一括返済も可能なため、"
        + "通常の月次finance BEPとは分けて表示しています。",
      tone: "neutral",
    });
  }
  notes.push({
    text: "同月比較対象外：Beds24は速報KPI（確定会計売上ではありません）。銀行入金とは同月比較しません。会計確定は銀行/会計士資料ベースです。",
    tone: "neutral",
  });
  return notes;
}

// primaryCards: id/label/value/tone/size/helper/meta を持つ型付きカード。
// size: "large"(1列・強調) | "normal"(1列・補助)。8枚を2列×4段の均等グリッドに収めるため、
// どのカードも1列(半幅)のみを占める(以前は予約ペースだけ2列hero表示だったが統一した)。
function buildPrimaryCards(s, achievement, pace) {
  const gap = s.cash_revenue_gap_to_breakeven;
  const gapAchieved = gap != null && gap <= 0;
  const gopValue = s.gop_after_mc;
  const gopTone = isNil(gopValue) ? "gray" : Number(gopValue) >= 0 ? "green" : "red";
  // 速報売上: 総額ベース(price。coupon/point/banktransfer/事前決済/現地決済は別途加算・控除しない。
  // キャンセル除外のみ行う)を主参照。旧fieldはfallback。
  // 2026-07-11 v5・ユーザー最終判断で確定: couponはBI上の参考表示のみで売上には含めない/引かない。
  const beds24Revenue = !isNil(s.beds24_revenue_net_for_bi)
    ? s.beds24_revenue_net_for_bi : s.beds24_stay_month_revenue_excluding_cancelled;

  return [
    // --- 最重要 ---
    {
      id: "booking-pace", label: "予約ペース評価", value: pace.label, tone: pace.tone, size: "large",
      helper: pace.reason,
      meta: [
        { label: "月内経過", value: pct(s.month_elapsed_rate) },
        { label: "月末着地", value: ratio(s.projected_month_end_bep_achievement_rate) },
        { label: "必要日商", value: yen(s.required_remaining_revenue_per_day) },
      ],
    },
    {
      id: "achievement", label: "Cash BEP達成率", value: pct(s.cash_operating_breakeven_achievement_rate),
      tone: achievement.tone, size: "large", badge: achievement.label,
      helper: "現在のBEPに対する到達度（予約ペースとは別指標）",
    },
    {
      id: "beds24-revenue", label: "速報売上", value: yen(beds24Revenue),
      tone: "blue", size: "large", helper: "Beds24 / 総額ベース / キャンセル除外",
      note: "確定会計売上ではありません",
    },
    // ADRは速報売上(単価の元)のすぐ近くに置く
    buildAdrCard(s),
    // --- 次点 ---
    {
      id: "cash-bep", label: "Cash BEP", value: yen(s.cash_operating_breakeven_revenue),
      tone: "gray", size: "normal", helper: "損益分岐点（キャッシュベース）",
    },
    {
      id: "remaining-revenue", label: "残り必要売上",
      value: gapAchieved ? "達成済み" : yen(gap),
      tone: gapAchieved ? "green" : "gray", size: "normal",
    },
    {
      id: "daily-need", label: "必要日商", value: yen(s.required_remaining_revenue_per_day),
      tone: "gray", size: "normal",
    },
    {
      id: "gop-after-mc", label: "MC後GOP", value: yen(gopValue), tone: gopTone, size: "normal",
      helper: "GOP after MC",
    },
  ];
}

// ADRカード（対象月の宿泊室料ベース平均単価。beds24_revenue_gross_stay/販売室泊）。
// point/coupon/onsite/cancelled調整後のnet売上カード（速報売上）とは別管理。
function buildAdrCard(s) {
  const soldNights = s.sold_room_nights;
  const hasData = !isNil(soldNights) && Number(soldNights) > 0;
  const occRate = s.occupancy_rate_month;
  return {
    id: "adr", label: "ADR", value: hasData ? yen(s.adr_gross) : "データなし",
    tone: "gray", size: "normal",
    helper: hasData
      ? `販売室泊 ${num(soldNights)} / 提供室泊 ${num(s.available_room_nights)}`
      : "対象月の販売室泊がありません",
    note: hasData ? `稼働率 ${isNil(occRate) ? DASH : Number(occRate).toFixed(1) + "%"}` : null,
  };
}

// detailSections: id/title/summary(先頭代表値)/rows の構造化配列。
// app.js/components.js は固定のdetailsキーを直接参照せず、この配列を素直にループする。
function buildDetailSections(s) {
  return [
    {
      id: "revenue-logic",
      title: "売上速報ロジック",
      summary: `速報売上net ${yen(s.beds24_revenue_net_for_bi)}`,
      rows: [
        ["売上額(Beds24速報売上 本体)", yen(s.beds24_revenue_gross_stay)],
        ["クーポン割引額（参考。売上からは控除しません）", yen(s.beds24_coupon_discount_amount)],
        ["ポイント/決済内訳", "総額に含む"],
        ["ポイント加算額", yen(s.beds24_point_revenue_included)],
        ["現地決済加算額", yen(s.beds24_onsite_payment_revenue_included)],
        ["キャンセル除外額", yen(s.beds24_cancelled_revenue_excluded)],
        ["認識売上(速報売上 net)", yen(s.beds24_revenue_net_for_bi)],
        ["キャンセル除外件数", num(s.beds24_cancelled_booking_count)],
        ["ポイント対象件数", num(s.beds24_point_booking_count)],
        ["クーポン利用検出", s.beds24_coupon_discount_detected ? "あり" : "なし"],
        ["クーポン利用件数", num(s.beds24_coupon_discount_booking_count)],
        ["現地決済候補額", yen(s.beds24_onsite_payment_candidate_amount)],
        ["現地決済対象件数", num(s.beds24_onsite_payment_candidate_count)],
        ["現地決済ロジック状態", onsitePaymentStatusLabel(s.beds24_onsite_payment_logic_status)],
        ["現地決済ロジック注記", s.beds24_onsite_payment_logic_note || DASH],
        ["売上ロジック状態", s.beds24_revenue_logic_status || DASH],
        ["売上ロジック注記", s.beds24_revenue_logic_note || DASH],
      ],
    },
    {
      id: "breakeven",
      title: "損益分岐",
      summary: `会計BEP ${yen(s.accounting_operating_breakeven_revenue)}`,
      rows: [
        ["会計BEP", yen(s.accounting_operating_breakeven_revenue)],
        ["会計BEP達成率", ratio(s.accounting_operating_breakeven_achievement_rate)],
        ["返済込みBEP", yen(s.finance_breakeven_revenue)],
        ["返済込みBEP達成率", ratio(s.finance_breakeven_achievement_rate)],
        ["返済込みBEPまで残り売上", yen(s.finance_revenue_gap_to_breakeven)],
        ["貢献利益率", pct(s.contribution_margin_rate)],
        ["変動費率合計", pct(s.variable_cost_rate_total)],
        ["温泉代", yen(s.hot_spring_fee_monthly) + " / 月"],
        ["キャッシュ固定費(人件費前)", yen(s.cash_fixed_cost_before_labor)],
        ["会計固定費(人件費前)", yen(s.accounting_fixed_cost_before_labor)],
        ["キャッシュ固定費合計", yen(s.cash_fixed_cost_total)],
        ["会計固定費合計", yen(s.accounting_fixed_cost_total)],
      ],
    },
    {
      id: "pace",
      title: "予約ペース",
      summary: `月内経過 ${pct(s.month_elapsed_rate)}`,
      rows: [
        ["現在日時(JST)", s.current_date_jst || DASH],
        ["対象月", s.target_month || DASH],
        ["月の日数", num(s.days_in_month)],
        ["現在日", num(s.day_of_month)],
        ["経過日数", num(s.days_elapsed_in_month)],
        ["残り日数", num(s.days_remaining_in_month)],
        ["月内経過率", pct(s.month_elapsed_rate)],
        ["経過時点の期待BEP進捗", yen(s.expected_bep_progress_to_date)],
        ["経過時点の期待進捗率", pct(s.expected_bep_progress_rate_to_date)],
        ["予約ペース達成率", ratio(s.booking_pace_achievement_rate)],
        ["期待進捗との差", yen(s.booking_pace_gap_to_expected)],
        ["月末着地見込み(OTB)", yen(s.projected_month_end_revenue)],
        ["月末着地見込みBEP達成率", ratio(s.projected_month_end_bep_achievement_rate)],
        ["必要日商", yen(s.required_remaining_revenue_per_day)],
        ["必要残り客室泊数", num(s.required_remaining_room_nights)],
        ["必要残り稼働率", pct(s.required_remaining_occupancy_rate)],
        ["判定理由", s.booking_pace_reason || DASH],
      ],
    },
    {
      id: "labor",
      title: "人件費",
      summary: `予測人件費 ${yen(s.labor_total_forecast)}`,
      rows: [
        ["松元固定給与", yen(s.labor_fixed_salary_cost)],
        ["追加フロント費", yen(s.labor_extra_front_cost)],
        ["清掃費", yen(s.labor_cleaning_cost)],
        ["夜間警備費", yen(s.labor_night_security_cost)],
        ["予測人件費合計", yen(s.labor_total_forecast)],
        ["稼働日数", num(s.labor_occupied_days) + "日"],
        ["70%超日数", num(s.labor_high_occupancy_days) + "日"],
        ["フロント未カバー日数", num(s.labor_uncovered_front_days) + "日"],
        ["人件費率(対Beds24速報売上)", pct(s.labor_cost_to_beds24_revenue)],
      ],
    },
    {
      id: "variable-cost",
      title: "変動費率",
      summary: `変動費率合計 ${pct(s.variable_cost_rate_total)}`,
      rows: [
        ["OTA/決済手数料率", pct(s.ota_fee_rate_effective)],
        ["水道光熱費率", pct(s.utilities_variable_rate)],
        ["修繕費率", pct(s.maintenance_variable_rate)],
        ["リネン費率", pct(s.linen_reference_rate)],
        ["備品消耗品費率", pct(s.supplies_reference_rate)],
      ],
    },
    {
      id: "mc-gop",
      title: "MC / GOP",
      summary: `GOP after MC ${yen(s.gop_after_mc)}`,
      rows: [
        ["MC固定費", yen(s.mc_fixed_fee)],
        ["MC成功報酬", yen(s.mc_success_fee)],
        ["GOP before success fee", yen(s.gop_before_success_fee)],
        ["GOP after MC", yen(s.gop_after_mc)],
        ["GOP margin after MC", pct(s.gop_margin_after_mc)],
      ],
    },
    {
      id: "finance",
      title: "財務・返済",
      summary: `返済: ${s.debt_service_status || DASH}`,
      rows: [
        ["期初現預金", yen(s.opening_cash_balance)],
        ["期初有利子負債", yen(s.opening_interest_bearing_debt_total)],
        ["期初純資産", yen(s.opening_equity_total)],
        ["月次元本返済", yen(s.monthly_debt_principal_payment)],
        ["月次支払利息", yen(s.monthly_debt_interest_payment)],
        ["金融機関返済 仮置き", yen(s.bank_debt_service_placeholder) + " / 月"],
        ["高見屋本体返済 月次換算", yen(s.takamiya_monthly_equivalent_cash_out) + " / 月"],
        ["標準返済込みBEP", yen(s.finance_breakeven_revenue)],
        ["高見屋返済込みBEP", yen(s.full_debt_reserve_breakeven_revenue)],
        ["高見屋返済込みBEPまで残り", yen(s.full_debt_reserve_revenue_gap_to_breakeven)],
        ["返済ステータス", s.debt_service_status || DASH],
        ["返済注記", s.debt_service_note || DASH],
      ],
    },
    {
      id: "validation",
      title: "検証",
      summary: null, // validationSummary/exceptionCountから動的に描画する
      rows: [
        ["入金月OTA入金", yen(s.bank_deposit_month_ota_revenue)],
        ["会計認識売上(入金)", yen(s.accounting_revenue_confirmed)],
        ["総入金", yen(s.bank_deposit_month_total_inflow)],
        ["総出金", yen(s.bank_deposit_month_total_outflow)],
        ["売上データ状態", s.revenue_data_status || DASH],
        ["同月比較", s.revenue_comparison_status || "同月比較対象外"],
      ],
    },
    {
      id: "bank-actuals",
      title: "銀行残高・実績CF",
      summary: `最新残高 ${yen(s.bank_actual_latest_balance)}`,
      rows: [
        ["最新銀行残高", yen(s.bank_actual_latest_balance)],
        ["最新銀行残高日", s.bank_actual_latest_balance_date || DASH],
        ["対象期間", (s.bank_source_period_start || DASH) + " 〜 " + (s.bank_source_period_end || DASH)],
        ["総入金", yen(s.bank_total_deposits)],
        ["総出金", yen(s.bank_total_withdrawals)],
        ["純キャッシュフロー", yen(s.bank_net_cashflow)],
        ["月末観測残高", yen(s.bank_month_end_balance_observed)],
        ["月末観測残高日", s.bank_month_end_balance_date || DASH],
        ["会計士確定BS現預金(普通預金)", yen(s.accountant_bs_cash_balance)],
        ["会計BSとの差異", yen(s.bank_vs_accountant_difference)],
        ["残高照合ステータス", s.bank_balance_reconciliation_status || DASH],
        ["銀行CSV取込状態", s.bank_csv_import_status || "未取込"],
        ["銀行CSV取込件数", num(s.bank_csv_imported_rows)],
        ["分類レビュー必要件数", num(s.bank_classification_review_required_count)],
        ["銀行CFデータ出所", bankFieldsSourceLabel(s.bank_fields_source)],
        ...(s.bank_fields_source === "previous_r2_snapshot"
          ? [["データ出所の補足", s.bank_fields_preserved_note ||
              "GitHub Actions更新時に銀行CSVは再取込されないため、直近公開済みの銀行CF summaryを維持しています。"]]
          : []),
      ],
    },
    {
      id: "bank-cost-candidates",
      title: "固定費・変動費更新候補",
      summary: `固定費候補 ${yen(s.bank_fixed_cost_candidate_total)} / 変動費候補 ${yen(s.bank_variable_cost_candidate_total)}`,
      rows: [
        ["固定費候補合計(当月実績)", yen(s.bank_fixed_cost_candidate_total)],
        ["変動費候補合計(当月実績)", yen(s.bank_variable_cost_candidate_total)],
        ["債務返済候補合計(当月実績)", yen(s.bank_debt_service_candidate_total)],
        ["レビュー必要件数", num(s.bank_classification_review_required_count)],
        ["注記", "候補のみ。固定費・変動費モデル(config)は自動更新しません。"],
      ],
    },
  ];
}

function defaultCurrentMonth() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

// 部屋変更履歴の要約テキスト。取得不可の場合はnull(=表示自体をしない)。
// 履歴を確認できて0件なら「なし」、履歴があれば件数を出す
// (詳細はroomChangeHistoryをdetails内で開いて見せる)。
function roomChangeSummaryText(status, historyLen) {
  if (status === "not_available" || status === "unknown") return null;
  if (historyLen > 0) return `部屋変更 ${historyLen}件`;
  return "部屋変更なし";
}

// 本日サマリー(新規予約/チェックイン)1件分の詳細行を表示用に整形する。PII(email/phone/
// address等)はsnapshot側にそもそも入っていないため、ここではchecked-inフィールドのみを扱う。
function formatGlobalBookingDetail(raw) {
  const rawHistory = Array.isArray(raw.room_change_history) ? raw.room_change_history : [];
  const status = raw.room_change_history_status || "unknown";
  return {
    bookingId: raw.booking_id || "",
    checkin: raw.checkin || DASH,
    checkout: raw.checkout || DASH,
    guestName: raw.guest_name || "氏名未取得",
    revenue: yen(raw.revenue),
    roomName: raw.room_name || null,
    createdAtJst: raw.created_at_jst || null,
    // --- 予約経路(OTA) ---
    otaName: raw.ota_name || "Direct",
    bookingSourceRaw: raw.booking_source_raw || null,
    // --- 部屋タイプ ---
    roomType: raw.room_type || "未分類",
    roomTypeKey: raw.room_type_key || null,
    // --- 部屋変更履歴(現状Beds24 payloadからは取得不可。将来対応時にhasRoomChangeがtrueになる) ---
    hasRoomChange: rawHistory.length > 0,
    roomChangeHistoryStatus: status,
    roomChangeSummary: roomChangeSummaryText(status, rawHistory.length),
    roomChangeHistory: rawHistory.map((c) => ({
      changedAt: c.changed_at || null,
      fromRoomType: c.from_room_type || null,
      toRoomType: c.to_room_type || null,
      changedBy: c.changed_by || null,
      rawNote: c.raw_note || null,
    })),
  };
}

// 日次サマリー3カード（本日の新規予約・前日の新規予約・本日のチェックイン）。
// いずれも月選択に依らないグローバル集計。
//
// single source of truthは snapshot.daily_global_summary（backend:
// calculate_today_global_summary が生成）。flat fields(today_new_booking_count_global等)は
// backend内の他消費者(manifest等)向けの互換fieldであり、フロントはここを直接参照しない
// ("複数schemaを場当たり的に推測する構造"を避けるため、daily_global_summaryのみを見る)。
function formatDateJstLabel(dateIso) {
  if (typeof dateIso !== "string") return DASH;
  const m = dateIso.match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (!m) return DASH;
  return `${Number(m[1])}年${Number(m[2])}月${Number(m[3])}日`;
}

const DAILY_CARD_DEFS = [
  {
    key: "today_new_bookings", label: "本日の新規予約", subLabel: "今日入った予約",
    detailsTitle: "本日新規予約一覧",
    emptyHelper: "本日の新規予約はまだありません",
    okHelper: "予約総額・キャンセル除外（チェックイン月は問わない）",
    missingHelper: "Beds24の予約作成日時fieldを確認できません",
  },
  {
    key: "yesterday_new_bookings", label: "前日の新規予約", subLabel: "昨日入った予約",
    detailsTitle: "前日新規予約一覧",
    emptyHelper: "前日の新規予約はありませんでした",
    okHelper: "予約総額・キャンセル除外（チェックイン月は問わない）",
    missingHelper: "Beds24の予約作成日時fieldを確認できません",
  },
  {
    key: "today_checkins", label: "本日のチェックイン", subLabel: "今日到着する予約",
    detailsTitle: "本日チェックイン予約一覧",
    emptyHelper: "本日チェックインの予約はありません",
    okHelper: "予約総額・キャンセル除外（予約作成日は問わない）",
    missingHelper: "Beds24のチェックイン日fieldを確認できません",
  },
];

// 正常に計算できた0件はtone=neutralの「0件」表示にする（"判定不可"にはしない）。
// "判定不可"はstatusがそもそも欠損/created_at_field_missingの場合のみ。
function buildDailySummaryCard(bucket, def) {
  const detailsCta = "詳細を見る";
  const b = (bucket && typeof bucket === "object") ? bucket : {};
  const status = b.status;
  const dateLabel = formatDateJstLabel(b.date_jst);

  if (!status || status === "created_at_field_missing") {
    return {
      label: def.label, subLabel: def.subLabel, dateLabel,
      count: "判定不可", revenue: "",
      helper: def.missingHelper,
      tone: "amber", status: status || "created_at_field_missing",
      details: [], hasDetails: false, detailsTitle: def.detailsTitle, detailsCta,
      detailsUnavailableNote: "予約作成日時を確認できないため、詳細を表示できません",
    };
  }

  const count = b.count;
  const countText = isNil(count) ? DASH : `${Number(count).toLocaleString("ja-JP")}件`;
  const revenueText = yen(b.revenue);
  const rawDetails = Array.isArray(b.details) ? b.details : [];
  const details = rawDetails.map(formatGlobalBookingDetail);

  return {
    label: def.label, subLabel: def.subLabel, dateLabel,
    count: countText, revenue: revenueText,
    helper: count ? def.okHelper : def.emptyHelper,
    tone: count ? "green" : "neutral", status,
    details, hasDetails: details.length > 0, detailsTitle: def.detailsTitle, detailsCta,
  };
}

function buildDailySummaryCards(s) {
  const summary = (s && typeof s.daily_global_summary === "object" && s.daily_global_summary) || {};
  return DAILY_CARD_DEFS.map((def) => buildDailySummaryCard(summary[def.key], def));
}

// データ鮮度表示（ヘッダー付近）。nowMsは呼び出し側(app.js)がDate.now()を渡す
// (biViewModel自体は現在時刻に依存しない純関数に保つため、ここでは受け取るだけ)。
const STALE_THRESHOLD_MS = 30 * 60 * 1000; // 30分

export function formatFreshness(generatedAtJstIso, nowMs) {
  if (!generatedAtJstIso) return { text: "", stale: false };
  const generated = new Date(generatedAtJstIso);
  if (Number.isNaN(generated.getTime())) return { text: "", stale: false };
  const ageMs = nowMs - generated.getTime();
  const ageMin = Math.max(0, Math.round(ageMs / 60000));
  const stale = ageMs > STALE_THRESHOLD_MS;
  const agoText = ageMin <= 0 ? "1分未満前" : `${ageMin}分前`;
  const pad2 = (n) => String(n).padStart(2, "0");
  const dateLabel = `${generated.getFullYear()}/${pad2(generated.getMonth() + 1)}/${pad2(generated.getDate())} `
    + `${pad2(generated.getHours())}:${pad2(generated.getMinutes())}`;
  const text = stale
    ? `最終更新: ${dateLabel}（${agoText}・更新が遅れています）`
    : `最終更新: ${dateLabel}（${agoText}・15分ごとに自動更新）`;
  return { text, stale, ageMin };
}

// 部屋タイプ別 日別稼働率グラフ用の系列データ（SVG描画はcomponents.js側の責務）。
// room_type_occupancy_chart_series は選択月のsnapshotのみを参照するため、月切替で自動更新される。
const ROOM_TYPE_CHART_COLORS = ["#2f6fed", "#e2725b", "#3fae6a", "#c48f2b", "#8a63d2", "#4fb0c6"];

function buildRoomTypeOccupancyChart(s) {
  const title = "部屋タイプ別 日別稼働率";
  const helper = "選択月の日別推移。キャンセル除外、月跨ぎ按分。";
  const series = Array.isArray(s.room_type_occupancy_chart_series) ? s.room_type_occupancy_chart_series : [];
  const warnings = Array.isArray(s.room_type_metrics_warnings) ? s.room_type_metrics_warnings : [];
  if (!series.length) {
    return { title, helper, hasData: false, dates: [], lines: [], warnings };
  }
  const labels = Object.keys(series[0]).filter((k) => k !== "date");
  const dates = series.map((row) => row.date);
  const lines = labels.map((label, i) => ({
    label,
    color: ROOM_TYPE_CHART_COLORS[i % ROOM_TYPE_CHART_COLORS.length],
    points: series.map((row) => Number(row[label] ?? 0)),
  }));
  return { title, helper, hasData: true, dates, lines, warnings };
}

// 部屋タイプ別 売上構成カード。横棒/progress bar表示用に0-100のsharePercentも渡す。
function buildRoomTypeRevenueMix(s) {
  const title = "部屋タイプ別 売上構成";
  const rawRows = Array.isArray(s.room_type_revenue_mix) ? s.room_type_revenue_mix : [];
  if (!rawRows.length) {
    return { title, hasData: false, rows: [] };
  }
  const rows = rawRows.map((r) => ({
    roomTypeLabel: r.room_type_label || r.room_type || DASH,
    revenue: yen(r.revenue),
    share: isNil(r.share) ? DASH : `${Number(r.share).toFixed(1)}%`,
    sharePercent: isNil(r.share) ? 0 : Number(r.share),
    soldRoomNights: isNil(r.sold_room_nights) ? DASH : `${num(r.sold_room_nights)}泊`,
    adr: yen(r.adr),
  }));
  return { title, hasData: true, rows };
}

export function buildBiViewModel(snapshot, manifest, validation, exception, options) {
  const s = snapshot || {};
  const opts = options || {};
  const generatedAtJst = (manifest && manifest.generated_at_jst) || s.current_date_jst || null;

  const achievement = achievementStatus(s.cash_operating_breakeven_achievement_rate);
  const pace = paceInfo(s);

  const validationSummary = validation
    ? { ok: !!validation.all_ok, criticalCount: validation.critical_count || 0,
        warningCount: validation.warning_count || 0 }
    : null;
  const exceptionCount = exception ? exception.total : null;

  const selectedMonth = opts.selectedMonth || s.month || s.target_month || null;
  const realCurrentMonth = opts.currentMonth || defaultCurrentMonth();

  const notes = buildNotes(s);
  const monthNote = buildMonthContextNote(selectedMonth, realCurrentMonth);
  if (monthNote) notes.unshift(monthNote);

  return {
    header: {
      title: "喜らく 速報BI",
      subtitle: "Beds24速報 / Cash BEP / 予約ペース / MC後GOP",
      generatedAtJst,
      targetMonth: s.month || s.target_month || DASH,
      selectedMonth,
      monthOptions: buildMonthOptions(manifest),
      statusPill: { label: s.revenue_data_status || "速報",
                   tone: s.revenue_data_status === "会計確定" ? "green" : "blue" },
    },
    primaryCards: buildPrimaryCards(s, achievement, pace),
    dailySummaryCards: buildDailySummaryCards(s),
    roomTypeOccupancyChart: buildRoomTypeOccupancyChart(s),
    roomTypeRevenueMix: buildRoomTypeRevenueMix(s),
    paceComment: buildPaceComment(achievement, pace),
    statusChips: buildStatusChips(s),
    notes,
    details: buildDetailSections(s),
    validationSummary,
    exceptionCount,
  };
}

export const _internal = {
  DEPRECATED_FIELDS, yen, pct, ratio, num, achievementStatus, paceInfo, buildPaceComment,
  monthLabel, buildMonthOptions, buildMonthContextNote, bankFieldsSourceLabel,
  onsitePaymentStatusLabel, formatFreshness, formatDateJstLabel,
};
