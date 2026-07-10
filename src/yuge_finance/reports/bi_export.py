"""BI用出力（喜らく単体）。

data/output/<month>/bi/ に以下を出力:
  - bi_snapshot.json          : 経営スナップショット（PL/BS/CF/KPI/YTD）
  - bi_daily_timeseries.csv   : 日次タイムシリーズ（売上・現金・銀行・予約）
  - bi_validation_status.json : 検証ステータス（BI監視用）
  - bi_exception_summary.json : 例外サマリ（source/rule/confidence別）
"""
from __future__ import annotations

import calendar
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List

from .. import config, csvio
from ..ingest import opening_balance as opening_balance_mod
from . import bank_cashflow_report

JST = timezone(timedelta(hours=9))


def _jst_now_str() -> str:
    return datetime.now(timezone.utc).astimezone(JST).isoformat(timespec="seconds")

DAILY_COLS = ["date", "宿泊売上", "現金入金", "現金支払", "銀行入金", "銀行出金",
              "予約件数", "仕訳件数"]


def _daily_timeseries(month: str, ctx: Dict) -> List[Dict]:
    y, m = (int(x) for x in month.split("-"))
    ndays = calendar.monthrange(y, m)[1]
    rows = {f"{month}-{d:02d}": {c: 0 for c in DAILY_COLS} for d in range(1, ndays + 1)}
    for k, day in rows.items():
        day["date"] = k
        day["宿泊売上"] = 0.0
        day["現金入金"] = day["現金支払"] = 0.0
        day["銀行入金"] = day["銀行出金"] = 0.0

    def bump(date_str, key, amt):
        d = (date_str or "")[:10]
        if d in rows:
            rows[d][key] += amt

    for e in ctx["confirmed"]:
        if e.credit_account == "宿泊売上":
            bump(e.journal_date, "宿泊売上", e.credit_amount)
        bump(e.journal_date, "仕訳件数", 1)
    for t in ctx["cash_txns"]:
        if t.transaction_type == "現金入金":
            bump(t.transaction_date, "現金入金", t.amount)
        elif t.transaction_type == "現金支払":
            bump(t.transaction_date, "現金支払", t.amount)
    for t in ctx["bank_txns"]:
        bump(t.transaction_date, "銀行入金", t.deposit_amount)
        bump(t.transaction_date, "銀行出金", t.withdrawal_amount)
    cfg_exclude = ["cancelled", "canceled", "black"]
    for b in ctx["bookings"]:
        if (b.status or "").lower() not in cfg_exclude:
            bump(b.checkin_date, "予約件数", 1)

    return [rows[k] for k in sorted(rows)]


def _combined_ytd(accountant: Dict[str, float], system_lines: List[Dict]) -> Dict:
    sys_map = {ln["item"]: ln["amount"] for ln in system_lines}
    keys = set(accountant) | set(sys_map)
    return {k: round(accountant.get(k, 0) + sys_map.get(k, 0), 2) for k in keys}


MONTHLY_KPI_COLS = [
    "month", "beds24_stay_month_revenue_excluding_cancelled",
    "beds24_stay_month_cancelled_revenue", "adr", "revpar", "occupancy",
    "break_even_achievement_rate_sokuho", "bank_deposit_month_ota_revenue",
    "accounting_revenue_confirmed", "revenue_data_status",
    "cash_operating_breakeven_revenue", "cash_operating_breakeven_achievement_rate",
    "accounting_operating_breakeven_revenue", "finance_breakeven_revenue",
    "cash_revenue_gap_to_breakeven", "labor_total_forecast", "labor_model_status",
    "debt_service_status", "breakeven_model_status", "gop_after_mc",
    "booking_pace_status", "month_elapsed_rate", "projected_month_end_bep_achievement_rate",
]


def write_monthly_kpi(month_ctxs: List[Dict], path: Path) -> Path:
    """各月の Beds24速報KPI + 入金実績 + 損益分岐(cash/accounting/finance)/人件費/債務/予約ペース を1行にまとめたCSV。"""
    rows = []
    for ctx in month_ctxs:
        rr = ctx["revenue_recon"]
        bm = ctx.get("breakeven_model", {})
        lf = ctx.get("labor_forecast", {})
        dbt = ctx.get("debt", {})
        pace = ctx.get("pace_model", {})
        row = {k: rr.get(k, "") for k in MONTHLY_KPI_COLS}
        row.update({
            "cash_operating_breakeven_revenue": bm.get("cash_operating_breakeven_revenue", ""),
            "cash_operating_breakeven_achievement_rate":
                bm.get("cash_operating_breakeven_achievement_rate", ""),
            "accounting_operating_breakeven_revenue":
                bm.get("accounting_operating_breakeven_revenue", ""),
            "finance_breakeven_revenue": bm.get("finance_breakeven_revenue", ""),
            "cash_revenue_gap_to_breakeven": bm.get("cash_revenue_gap_to_breakeven", ""),
            "labor_total_forecast": lf.get("labor_total_forecast", ""),
            "labor_model_status": lf.get("labor_model_status", ""),
            "debt_service_status": bm.get("debt_service_status", dbt.get("debt_service_status", "")),
            "breakeven_model_status": bm.get("breakeven_model_status", ""),
            "gop_after_mc": bm.get("gop_after_mc", ""),
            "booking_pace_status": pace.get("booking_pace_status", ""),
            "month_elapsed_rate": pace.get("month_elapsed_rate", ""),
            "projected_month_end_bep_achievement_rate":
                pace.get("projected_month_end_bep_achievement_rate", ""),
        })
        rows.append(row)
    csvio.write_rows(path, rows, MONTHLY_KPI_COLS)
    return path


def write_all(month: str, ctx: Dict, checks: List[Dict], wb_checks: List[Dict],
              severity: Dict, out_dir: Path, conn=None) -> Dict:
    bi_dir = out_dir / "bi"
    bi_dir.mkdir(parents=True, exist_ok=True)

    # 1. snapshot
    rr = ctx["revenue_recon"]
    lf = ctx.get("labor_forecast", {})
    bm = ctx.get("breakeven_model", {})
    dbt = ctx.get("debt", {})
    pace = ctx.get("pace_model", {})
    opening_records = ctx.get("opening_records", [])
    opening_tot = (opening_balance_mod.account_totals(opening_records)
                  if opening_records else {"asset_total": None, "liability_total": None,
                                           "equity_total": None})
    opening = ctx.get("opening", {})
    opening_cash = round((opening.get("現預金", {}).get("debit", 0)
                         - opening.get("現預金", {}).get("credit", 0)), 2)
    snapshot = {
        "month": month,
        "property": "喜らく",
        "generated_at_jst": _jst_now_str(),
        # === A. 宿泊月ベース速報（Beds24・KPI先行指標）===
        "beds24_stay_month_gross_revenue": rr["beds24_stay_month_gross_revenue"],
        "beds24_stay_month_revenue_excluding_cancelled":
            rr["beds24_stay_month_revenue_excluding_cancelled"],
        "beds24_stay_month_cancelled_revenue": rr["beds24_stay_month_cancelled_revenue"],
        # --- 売上速報ロジック v3（point加算・coupon直割引の明確化）---
        "beds24_revenue_gross_stay": rr["beds24_revenue_gross_stay"],
        "beds24_point_revenue_included": rr["beds24_point_revenue_included"],
        "beds24_point_booking_count": rr["beds24_point_booking_count"],
        "beds24_coupon_discount_detected": rr["beds24_coupon_discount_detected"],
        "beds24_coupon_discount_amount": rr["beds24_coupon_discount_amount"],
        "beds24_coupon_discount_booking_count": rr["beds24_coupon_discount_booking_count"],
        "beds24_onsite_payment_revenue_included": rr["beds24_onsite_payment_revenue_included"],
        "beds24_onsite_payment_booking_count": rr["beds24_onsite_payment_booking_count"],
        "beds24_onsite_payment_candidate_amount": rr["beds24_onsite_payment_candidate_amount"],
        "beds24_onsite_payment_candidate_count": rr["beds24_onsite_payment_candidate_count"],
        "beds24_onsite_payment_logic_status": rr["beds24_onsite_payment_logic_status"],
        "beds24_onsite_payment_logic_note": rr["beds24_onsite_payment_logic_note"],
        "beds24_cancelled_revenue_excluded": rr["beds24_cancelled_revenue_excluded"],
        "beds24_revenue_net_for_bi": rr["beds24_revenue_net_for_bi"],
        "beds24_revenue_logic_version": rr["beds24_revenue_logic_version"],
        "beds24_revenue_logic_status": rr["beds24_revenue_logic_status"],
        "beds24_revenue_logic_note": rr["beds24_revenue_logic_note"],
        "beds24_cancelled_booking_count": rr["beds24_cancelled_booking_count"],
        # --- 旧field（意味が誤っていたためdeprecated。互換性のため0で残す。UIでは使わない）---
        "beds24_coupon_revenue_included": rr["beds24_coupon_revenue_included"],
        "beds24_coupon_booking_count": rr["beds24_coupon_booking_count"],
        "adr": rr["adr"],
        "revpar": rr["revpar"],
        "occupancy": rr["occupancy"],
        "break_even_achievement_rate_sokuho": rr["break_even_achievement_rate_sokuho"],
        # === B. 入金月ベース会計/資金実績（銀行/現金）===
        "bank_deposit_month_ota_revenue": rr["bank_deposit_month_ota_revenue"],
        "bank_deposit_month_total_inflow": rr["bank_deposit_month_total_inflow"],
        "bank_deposit_month_total_outflow": rr["bank_deposit_month_total_outflow"],
        "cash_in_basis_revenue": rr["cash_in_basis_revenue"],
        "net_cash_movement": rr["net_cash_movement"],
        "accounting_revenue_confirmed": rr["accounting_revenue_confirmed"],
        # === C. 精算ラグ注記 ===
        "ota_settlement_lag_note": rr["ota_settlement_lag_note"],
        "same_month_revenue_comparison_applicable":
            rr["same_month_revenue_comparison_applicable"],
        "revenue_comparison_status": rr["revenue_comparison_status"],
        "settlement_reconciliation_status": rr["settlement_reconciliation_status"],
        "revenue_data_status": rr["revenue_data_status"],
        "legacy_same_month_reference": rr["legacy_same_month_reference"],
        # --- 会計PL（銀行/OTA入金ベースの確定値）---
        "accounting_pl_revenue": ctx["pl"]["revenue"],
        "net_income_month": ctx["pl"]["net_income"],
        "pl_month": ctx["pl"]["lines"],
        "pl_cumulative_from_jun": ctx["pl_cumulative"]["lines"],
        "bs": ctx["bs"]["lines"],
        "bs_balanced": ctx["bs"]["balanced"],
        "cf_month": ctx["cf"]["lines"],
        "breakeven": ctx["breakeven"],
        # === Phase C: 人件費予測モデル（速報。給与確定仕訳ではない）===
        "labor_fixed_salary_cost": lf.get("labor_fixed_salary_cost"),
        "labor_extra_front_cost": lf.get("labor_extra_front_cost"),
        "labor_cleaning_cost": lf.get("labor_cleaning_cost"),
        "labor_night_security_cost": lf.get("labor_night_security_cost"),
        "labor_total_forecast": lf.get("labor_total_forecast"),
        "labor_total_low_case": lf.get("labor_total_low_case"),
        "labor_total_base_case": lf.get("labor_total_base_case"),
        "labor_total_high_case": lf.get("labor_total_high_case"),
        "labor_occupied_days": lf.get("labor_occupied_days"),
        "labor_high_occupancy_days": lf.get("labor_high_occupancy_days"),
        "labor_room_nights": lf.get("labor_room_nights"),
        "labor_uncovered_front_days": lf.get("labor_uncovered_front_days"),
        "labor_cost_per_occupied_room_night": lf.get("labor_cost_per_occupied_room_night"),
        "labor_cost_to_beds24_revenue": lf.get("labor_cost_to_beds24_revenue"),
        "labor_model_status": lf.get("labor_model_status"),
        # === Phase D v2: 固定費・変動費モデル（現体制運営前提）===
        # 主指標は cash_operating_breakeven_revenue / achievement_rate。旧フィールドは後方互換で残す。
        "breakeven_model_version": bm.get("breakeven_model_version"),
        "cash_fixed_cost_before_labor": bm.get("cash_fixed_cost_before_labor"),
        "accounting_fixed_cost_before_labor": bm.get("accounting_fixed_cost_before_labor"),
        "labor_total_forecast": lf.get("labor_total_forecast"),
        "cash_fixed_cost_total": bm.get("cash_fixed_cost_total"),
        "accounting_fixed_cost_total": bm.get("accounting_fixed_cost_total"),
        "variable_cost_rate_total": bm.get("variable_cost_rate_total"),
        "contribution_margin_rate": bm.get("contribution_margin_rate"),
        "ota_fee_rate_effective": bm.get("ota_fee_rate_effective"),
        "utilities_variable_rate": bm.get("utilities_variable_rate"),
        "maintenance_variable_rate": bm.get("maintenance_variable_rate"),
        "linen_reference_rate": bm.get("linen_reference_rate"),
        "supplies_reference_rate": bm.get("supplies_reference_rate"),
        # --- Cash operating BEP（BI主指標）---
        "cash_operating_breakeven_revenue": bm.get("cash_operating_breakeven_revenue"),
        "cash_operating_breakeven_achievement_rate":
            bm.get("cash_operating_breakeven_achievement_rate"),
        "cash_revenue_gap_to_breakeven": bm.get("cash_revenue_gap_to_breakeven"),
        # --- Accounting operating BEP ---
        "accounting_operating_breakeven_revenue": bm.get("accounting_operating_breakeven_revenue"),
        "accounting_operating_breakeven_achievement_rate":
            bm.get("accounting_operating_breakeven_achievement_rate"),
        "accounting_revenue_gap_to_breakeven": bm.get("accounting_revenue_gap_to_breakeven"),
        # --- Finance-inclusive BEP（支払利息・元本返済込み）---
        "finance_breakeven_revenue": bm.get("finance_breakeven_revenue"),
        "finance_breakeven_achievement_rate": bm.get("finance_breakeven_achievement_rate"),
        "finance_revenue_gap_to_breakeven": bm.get("finance_revenue_gap_to_breakeven"),
        "finance_bep_note": bm.get("finance_bep_note"),
        # --- 固定費内訳（温泉代）・返済仮置き ---
        "hot_spring_fee_monthly": bm.get("hot_spring_fee_monthly"),
        "bank_debt_service_placeholder": bm.get("bank_debt_service_placeholder"),
        "standard_finance_required_cost": bm.get("standard_finance_required_cost"),
        # --- 高見屋返済込みBEP（別シナリオ。標準finance BEPには含めない）---
        "takamiya_monthly_equivalent_cash_out": bm.get("takamiya_monthly_equivalent_cash_out"),
        "full_debt_reserve_required_cost": bm.get("full_debt_reserve_required_cost"),
        "full_debt_reserve_breakeven_revenue": bm.get("full_debt_reserve_breakeven_revenue"),
        "full_debt_reserve_breakeven_achievement_rate":
            bm.get("full_debt_reserve_breakeven_achievement_rate"),
        "full_debt_reserve_revenue_gap_to_breakeven":
            bm.get("full_debt_reserve_revenue_gap_to_breakeven"),
        "debt_service_note": bm.get("debt_service_note"),
        # --- MC / GOP ---
        "gop_before_success_fee": bm.get("gop_before_success_fee"),
        "mc_fixed_fee": bm.get("mc_fixed_fee"),
        "mc_success_fee": bm.get("mc_success_fee"),
        "gop_after_mc": bm.get("gop_after_mc"),
        "gop_margin_after_mc": bm.get("gop_margin_after_mc"),
        # --- 残り必要売上（キャッシュBEP基準）・旧フィールド(後方互換) ---
        "revenue_gap_to_breakeven": bm.get("cash_revenue_gap_to_breakeven"),
        "required_remaining_revenue_per_day": bm.get("required_remaining_revenue_per_day"),
        "required_remaining_room_nights": bm.get("required_remaining_room_nights"),
        "required_remaining_occupancy_rate": bm.get("required_remaining_occupancy_rate"),
        "variable_cost_rate_used": bm.get("variable_cost_rate_used"),
        "fixed_non_labor_cost_used": bm.get("fixed_non_labor_cost_used"),
        "labor_cost_used": bm.get("labor_cost_used"),
        "breakeven_revenue_current_structure": bm.get("breakeven_revenue_current_structure"),
        "breakeven_achievement_rate_current_structure":
            bm.get("breakeven_achievement_rate_current_structure"),
        "breakeven_model_status": bm.get("breakeven_model_status"),
        # === Phase B: 月次債務返済 ===
        "debt_opening_balance_total": dbt.get("debt_opening_balance_total"),
        "debt_closing_balance_total": dbt.get("debt_closing_balance_total"),
        "monthly_debt_principal_payment": dbt.get("monthly_debt_principal_payment"),
        "monthly_debt_interest_payment": dbt.get("monthly_debt_interest_payment"),
        "monthly_debt_total_payment": dbt.get("monthly_debt_total_payment"),
        # debt_service_status は breakeven_model側の実効値を採用する
        # （実スケジュール投入済ならそれを尊重、未投入かつ返済仮置き有効時は「返済仮置き」）。
        "debt_service_status": bm.get("debt_service_status", dbt.get("debt_service_status")),
        "debt_schedule_missing_count": dbt.get("debt_schedule_missing_count"),
        "debt_schedule_exception_amount": dbt.get("debt_schedule_exception_amount"),
        # === 予約ペース判定（達成率とは別軸。「大幅未達」でも予約ペースは別途評価する）===
        "current_date_jst": pace.get("current_date_jst"),
        "target_month": pace.get("target_month"),
        "days_in_month": pace.get("days_in_month"),
        "day_of_month": pace.get("day_of_month"),
        "days_elapsed_in_month": pace.get("days_elapsed_in_month"),
        "days_remaining_in_month": pace.get("days_remaining_in_month"),
        "month_elapsed_rate": pace.get("month_elapsed_rate"),
        "month_remaining_rate": pace.get("month_remaining_rate"),
        "expected_bep_progress_to_date": pace.get("expected_bep_progress_to_date"),
        "expected_bep_progress_rate_to_date": pace.get("expected_bep_progress_rate_to_date"),
        "beds24_revenue_to_date": pace.get("beds24_revenue_to_date"),
        "beds24_month_on_the_books_revenue": pace.get("beds24_month_on_the_books_revenue"),
        "booking_pace_achievement_rate": pace.get("booking_pace_achievement_rate"),
        "booking_pace_gap_to_expected": pace.get("booking_pace_gap_to_expected"),
        "projected_month_end_revenue": pace.get("projected_month_end_revenue"),
        "projected_month_end_bep_achievement_rate":
            pace.get("projected_month_end_bep_achievement_rate"),
        "booking_pace_status": pace.get("booking_pace_status"),
        "booking_pace_label": pace.get("booking_pace_label"),
        "booking_pace_reason": pace.get("booking_pace_reason"),
        "pace_model_status": pace.get("pace_model_status"),
        # === Phase A: 開始残高（会計士確定BSロック）===
        "opening_balance_date": config.kiraku().get("opening_balance_lock", {}).get("date"),
        "opening_balance_status": "会計士確定",
        "opening_cash_balance": opening_cash,
        "opening_interest_bearing_debt_total": dbt.get("debt_opening_balance_total"),
        "opening_equity_total": opening_tot.get("equity_total"),
        "opening_asset_total": opening_tot.get("asset_total"),
        "opening_liability_total": opening_tot.get("liability_total"),
        "rollforward": {
            "cash": ctx["cash_rollforward"],
            "loan": ctx["loan_rollforward"],
        },
        "ytd": {
            "accountant_pl_through_may": ctx["accountant_pl_ytd"],
            "accountant_cf_through_may": ctx["accountant_cf_ytd"],
            "system_pl_jun_onward": {ln["item"]: ln["amount"]
                                     for ln in ctx["pl_cumulative"]["lines"]},
            "full_year_pl_ytd": _combined_ytd(ctx["accountant_pl_ytd"],
                                              ctx["pl_cumulative"]["lines"]),
        },
        "counts": {
            "beds24": len(ctx["bookings"]),
            "bank": len(ctx["bank_txns"]),
            "cash": len(ctx["cash_txns"]),
            "manual": len(ctx["manual"]),
            "confirmed": len(ctx["confirmed"]),
            "exceptions": len(ctx["exceptions"]),
        },
        "validation_ok": severity.get("all_ok", False) and not any(
            c["status"] == "critical" for c in ctx.get("opening_critical", [])),
        # === 銀行口座実績レイヤー（BI/分析専用。仕訳・PL/BS/CFには一切反映しない）===
        **ctx.get("bank_actual_bi", {}),
        # === 本日の新規予約（BI専用サマリ。仕訳・PL/BS/CFには一切反映しない）===
        **ctx.get("today_new_bookings", {}),
    }
    (bi_dir / "bi_snapshot.json").write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    # 2. daily timeseries
    daily = _daily_timeseries(month, ctx)
    csvio.write_rows(bi_dir / "bi_daily_timeseries.csv", daily, DAILY_COLS)

    # 3. validation status
    opening_critical = ctx.get("opening_critical", [])
    opening_failed = [c for c in opening_critical if c["status"] == "critical"]
    vstatus = {
        "month": month,
        "all_ok": severity.get("all_ok", False) and not opening_failed,
        "critical_count": len(severity.get("critical", [])) + len(opening_failed),
        "warning_count": len(severity.get("warnings", [])),
        "critical": severity.get("critical", []) + opening_failed,
        "warnings": severity.get("warnings", []),
        "checks": checks,
        "opening_balance_checks": opening_critical,
        "workbook_checks": wb_checks,
    }
    (bi_dir / "bi_validation_status.json").write_text(
        json.dumps(vstatus, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    # 4. exception summary
    exc = ctx["exceptions"]
    summary = {
        "month": month,
        "total": len(exc),
        "by_source": dict(Counter(e.source for e in exc)),
        "by_rule": dict(Counter(e.rule_id for e in exc)),
        "by_confidence": dict(Counter(e.confidence for e in exc)),
        "items": [{
            "journal_id": e.journal_id, "date": e.journal_date, "source": e.source,
            "rule_id": e.rule_id, "confidence": e.confidence,
            "description": e.description,
            "debit_account": e.debit_account, "credit_account": e.credit_account,
            "amount": e.debit_amount,
        } for e in exc],
    }
    (bi_dir / "bi_exception_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    # 5. 銀行口座実績 月次CF/費目候補（config/fixed_variable_model.yml は直接更新しない）
    if conn is not None:
        bank_cashflow_report.write_all(conn, bi_dir, month)

    return {"bi_dir": str(bi_dir), "daily_rows": len(daily),
            "exceptions": len(exc)}
