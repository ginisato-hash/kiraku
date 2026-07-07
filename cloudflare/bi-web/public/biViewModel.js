// 喜らく 速報BI — bi_snapshot.json を表示用に整形する normalizer。
// app.js は生のフィールド名を直接大量に参照せず、この view model 経由で描画する。
//
// 重要な前提（画面側でも守る）：
//   - Beds24は速報KPI。確定会計売上ではない。
//   - 銀行入金月売上とBeds24宿泊月売上は同月比較しない。
//   - 「達成率」（現在のBEPに対する到達度）と「予約ペース」（月末着地見込み）は別指標。
//     大幅未達 かつ 予約ペース：グリーン/要注意 は矛盾しない。
//   - 元本返済は予定表未投入の間、推定しない。
//
// DEPRECATED（primary表示に使わない。JSONには残る）:
const DEPRECATED_FIELDS = ["breakeven_revenue_current_structure", "revenue_reconciliation_difference"];

const DASH = "—";

function isNil(v) {
  return v == null || v === "";
}

function yen(n) {
  return isNil(n) ? DASH : "¥" + Number(n).toLocaleString("ja-JP");
}

function pct(n, digits) {
  if (isNil(n)) return DASH;
  return (Number(n) * 100).toFixed(digits == null ? 1 : digits) + "%";
}

function ratio(n) {
  return isNil(n) ? DASH : Number(n).toFixed(2) + "倍";
}

function num(n) {
  return isNil(n) ? DASH : Number(n).toLocaleString("ja-JP");
}

function pick(obj, key, fallback) {
  const v = obj == null ? undefined : obj[key];
  return v === undefined ? fallback : v;
}

// 現在のBEP達成率のラベル（既存の「大幅未達」表記を維持）
function achievementStatus(rate) {
  if (isNil(rate)) return { key: "unknown", label: "判定不可" };
  if (rate >= 1.0) return { key: "achieved", label: "達成" };
  if (rate >= 0.8) return { key: "close", label: "あと少し" };
  return { key: "far", label: "大幅未達" };
}

const PACE_LABELS = { green: "グリーン", yellow: "要注意", red: "レッド", unknown: "判定不可" };
const PACE_SEVERITY = { green: "green", yellow: "yellow", red: "red", unknown: "neutral" };

function paceInfo(snapshot) {
  const status = pick(snapshot, "booking_pace_status", "unknown");
  return {
    key: status,
    label: pick(snapshot, "booking_pace_label", PACE_LABELS[status] || DASH),
    reason: pick(snapshot, "booking_pace_reason", ""),
    severity: PACE_SEVERITY[status] || "neutral",
  };
}

// achievement_status + booking_pace_status を組み合わせた短いコメント（20〜60字程度）
function buildPaceComment(achievement, pace) {
  const achText = achievement.key === "far" ? "現時点ではCash BEPに対して大幅未達です。"
    : achievement.key === "close" ? "現時点ではCash BEPにあと少しです。"
    : achievement.key === "achieved" ? "現時点でCash BEPを達成しています。"
    : "現時点のCash BEP達成状況は判定できません。";

  let paceText;
  if (pace.key === "green") paceText = "予約ペースはグリーンです。";
  else if (pace.key === "yellow") paceText = "ただし予約ペースは要注意です。";
  else if (pace.key === "red") paceText = "予約ペースもレッドです。追加予約が必要です。";
  else paceText = "予約ペースは判定できません。";

  const severity = pace.key === "unknown" ? "neutral" : pace.severity;
  return { text: (achText + paceText).slice(0, 80), severity };
}

function statusChip(label, value, tone) {
  return { label, value: value || DASH, tone: tone || "neutral" };
}

function toneForStatus(value, okValues, warnValues) {
  if (isNil(value)) return "neutral";
  if (okValues.includes(value)) return "ok";
  if (warnValues.includes(value)) return "warn";
  return "danger";
}

function buildStatusChips(s) {
  return [
    statusChip("revenue_data_status", s.revenue_data_status,
      toneForStatus(s.revenue_data_status, ["会計確定"], ["入金実績あり", "精算明細待ち", "速報"])),
    statusChip("breakeven_model_status", s.breakeven_model_status,
      toneForStatus(s.breakeven_model_status, ["達成"], ["未達"])),
    statusChip("pace_model_status", s.pace_model_status,
      toneForStatus(s.pace_model_status, ["推計"], ["対象外"])),
    statusChip("labor_model_status", s.labor_model_status,
      toneForStatus(s.labor_model_status, ["実績反映済"], ["推計"])),
    statusChip("debt_service_status", s.debt_service_status,
      toneForStatus(s.debt_service_status, ["確定", "予定表投入済"], ["予定表未投入"])),
    statusChip("opening_balance_status", s.opening_balance_status,
      toneForStatus(s.opening_balance_status, ["会計士確定"], [])),
  ];
}

function buildNotes(s) {
  const notes = [];
  if (s.debt_service_status === "予定表未投入") {
    notes.push({
      text: "返済予定表未投入のため、返済込みBEPは未完全です。元本返済はデータ確定後に反映します。",
      severity: "warn",
    });
  }
  notes.push({
    text: "同月比較対象外：Beds24は速報KPI（確定会計売上ではありません）。銀行入金とは同月比較しません。会計確定は銀行/会計士資料ベースです。",
    severity: "info",
  });
  return notes;
}

function buildPrimaryCards(s, achievement, pace) {
  return [
    { label: "速報売上", value: yen(s.beds24_stay_month_revenue_excluding_cancelled), tone: "accent" },
    { label: "キャッシュBEP", value: yen(s.cash_operating_breakeven_revenue), tone: "normal" },
    { label: "達成率", value: pct(s.cash_operating_breakeven_achievement_rate),
      badge: achievement.label, badgeTone: achievement.key === "far" ? "danger"
        : achievement.key === "close" ? "warn" : achievement.key === "achieved" ? "ok" : "neutral" },
    { label: "予約ペース", value: pace.label,
      badge: pace.label, badgeTone: pace.severity === "neutral" ? "neutral" : pace.severity },
    { label: "残り必要売上",
      value: (s.cash_revenue_gap_to_breakeven != null && s.cash_revenue_gap_to_breakeven <= 0)
        ? "達成済み" : yen(s.cash_revenue_gap_to_breakeven) },
    { label: "必要日商", value: yen(s.required_remaining_revenue_per_day) },
    { label: "MC後GOP", value: yen(s.gop_after_mc), tone: "accent" },
  ];
}

function buildDetails(s) {
  return {
    breakeven: [
      ["会計BEP", yen(s.accounting_operating_breakeven_revenue)],
      ["会計BEP達成率", ratio(s.accounting_operating_breakeven_achievement_rate)],
      ["返済込みBEP", yen(s.finance_breakeven_revenue)],
      ["返済込みBEP達成率", ratio(s.finance_breakeven_achievement_rate)],
      ["返済込みBEPまで残り売上", yen(s.finance_revenue_gap_to_breakeven)],
      ["貢献利益率", pct(s.contribution_margin_rate)],
      ["変動費率合計", pct(s.variable_cost_rate_total)],
      ["キャッシュ固定費(人件費前)", yen(s.cash_fixed_cost_before_labor)],
      ["会計固定費(人件費前)", yen(s.accounting_fixed_cost_before_labor)],
      ["キャッシュ固定費合計", yen(s.cash_fixed_cost_total)],
      ["会計固定費合計", yen(s.accounting_fixed_cost_total)],
    ],
    pace: [
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
    labor: [
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
    variableCost: [
      ["OTA/決済手数料率", pct(s.ota_fee_rate_effective)],
      ["水道光熱費率", pct(s.utilities_variable_rate)],
      ["修繕費率", pct(s.maintenance_variable_rate)],
      ["リネン費率", pct(s.linen_reference_rate)],
      ["備品消耗品費率", pct(s.supplies_reference_rate)],
    ],
    mc: [
      ["MC固定費", yen(s.mc_fixed_fee)],
      ["MC成功報酬", yen(s.mc_success_fee)],
      ["GOP before success fee", yen(s.gop_before_success_fee)],
      ["GOP after MC", yen(s.gop_after_mc)],
      ["GOP margin after MC", pct(s.gop_margin_after_mc)],
    ],
    finance: [
      ["期初現預金", yen(s.opening_cash_balance)],
      ["期初有利子負債", yen(s.opening_interest_bearing_debt_total)],
      ["期初純資産", yen(s.opening_equity_total)],
      ["月次元本返済", yen(s.monthly_debt_principal_payment)],
      ["月次支払利息", yen(s.monthly_debt_interest_payment)],
      ["debt_service_status", s.debt_service_status || DASH],
    ],
    validation: [
      ["入金月OTA入金", yen(s.bank_deposit_month_ota_revenue)],
      ["会計認識売上(入金)", yen(s.accounting_revenue_confirmed)],
      ["総入金", yen(s.bank_deposit_month_total_inflow)],
      ["総出金", yen(s.bank_deposit_month_total_outflow)],
      ["revenue_data_status", s.revenue_data_status || DASH],
      ["同月比較", s.revenue_comparison_status || "同月比較対象外"],
    ],
  };
}

export function buildBiViewModel(snapshot, manifest, validation, exception) {
  const s = snapshot || {};
  const generatedAtJst = (manifest && manifest.generated_at_jst) || s.current_date_jst || null;

  const achievement = achievementStatus(s.cash_operating_breakeven_achievement_rate);
  const pace = paceInfo(s);

  return {
    header: {
      title: "喜らく 速報BI",
      subtitle: "Beds24速報 / Cash BEP / 予約ペース / 人件費 / MC後GOP",
      generatedAtJst,
      targetMonth: s.month || s.target_month || DASH,
    },
    primaryCards: buildPrimaryCards(s, achievement, pace),
    paceComment: buildPaceComment(achievement, pace),
    statusChips: buildStatusChips(s),
    notes: buildNotes(s),
    details: buildDetails(s),
    validationSummary: validation
      ? { ok: !!validation.all_ok, criticalCount: validation.critical_count || 0,
          warningCount: validation.warning_count || 0 }
      : null,
    exceptionCount: exception ? exception.total : null,
  };
}

export const _internal = { DEPRECATED_FIELDS, yen, pct, ratio, num, achievementStatus, paceInfo };
