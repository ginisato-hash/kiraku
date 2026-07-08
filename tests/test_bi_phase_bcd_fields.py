"""Phase H-5: bi_snapshot.json に opening balance / labor / breakeven / debt フィールドがあること。
同月Beds24売上と銀行入金差額を主指標に出さないことも確認する。
"""
import json

from yuge_finance import db, monthly
from yuge_finance.ingest import opening_balance
from yuge_finance.reports import bi_export


def test_snapshot_has_new_phase_fields(tmp_path):
    conn = db.connect(tmp_path / "t.sqlite")
    opening_balance.run(conn)  # 会計士確定BS(実データ)をDBへ投入
    ctx = monthly.assemble("2026-06", conn)
    sev = {"all_ok": True, "critical": [], "warnings": []}
    bi_export.write_all("2026-06", ctx, checks=[], wb_checks=[], severity=sev, out_dir=tmp_path)
    snap = json.loads((tmp_path / "bi" / "bi_snapshot.json").read_text(encoding="utf-8"))

    labor_fields = ["labor_fixed_salary_cost", "labor_extra_front_cost", "labor_cleaning_cost",
                    "labor_night_security_cost", "labor_total_forecast", "labor_total_low_case",
                    "labor_total_base_case", "labor_total_high_case", "labor_occupied_days",
                    "labor_high_occupancy_days", "labor_room_nights", "labor_uncovered_front_days",
                    "labor_cost_per_occupied_room_night", "labor_cost_to_beds24_revenue",
                    "labor_model_status"]
    breakeven_fields = ["breakeven_revenue_current_structure",
                        "breakeven_achievement_rate_current_structure",
                        "revenue_gap_to_breakeven", "required_remaining_revenue_per_day",
                        "required_remaining_room_nights", "required_remaining_occupancy_rate",
                        "contribution_margin_rate", "variable_cost_rate_used",
                        "fixed_non_labor_cost_used", "labor_cost_used", "breakeven_model_status",
                        # v2（現体制運営前提。BI主指標）
                        "breakeven_model_version",
                        "cash_fixed_cost_before_labor", "accounting_fixed_cost_before_labor",
                        "cash_fixed_cost_total", "accounting_fixed_cost_total",
                        "variable_cost_rate_total", "ota_fee_rate_effective",
                        "utilities_variable_rate", "maintenance_variable_rate",
                        "linen_reference_rate", "supplies_reference_rate",
                        "cash_operating_breakeven_revenue",
                        "cash_operating_breakeven_achievement_rate",
                        "cash_revenue_gap_to_breakeven",
                        "accounting_operating_breakeven_revenue",
                        "accounting_operating_breakeven_achievement_rate",
                        "accounting_revenue_gap_to_breakeven",
                        "finance_breakeven_revenue", "finance_breakeven_achievement_rate",
                        "finance_revenue_gap_to_breakeven",
                        "gop_before_success_fee", "mc_fixed_fee", "mc_success_fee",
                        "gop_after_mc", "gop_margin_after_mc"]
    debt_fields = ["debt_opening_balance_total", "debt_closing_balance_total",
                  "monthly_debt_principal_payment", "monthly_debt_interest_payment",
                  "monthly_debt_total_payment", "debt_service_status",
                  "debt_schedule_missing_count", "debt_schedule_exception_amount"]
    opening_fields = ["opening_balance_date", "opening_balance_status",
                      "opening_cash_balance", "opening_interest_bearing_debt_total",
                      "opening_equity_total"]

    for f in labor_fields + breakeven_fields + debt_fields + opening_fields:
        assert f in snap, f"missing field: {f}"

    # 開始残高が会計士確定BSロック値どおりであること
    assert snap["opening_asset_total"] == 77335346
    assert snap["opening_interest_bearing_debt_total"] == 118128637
    assert snap["debt_opening_balance_total"] == 118128637

    # BI主指標は cash_operating_breakeven_revenue（旧 breakeven_revenue_current_structure は後方互換のみ）
    assert snap["breakeven_model_version"] == "kiraku_current_operation_v2"
    assert snap["cash_operating_breakeven_revenue"] is not None
    assert snap["breakeven_revenue_current_structure"] == snap["cash_operating_breakeven_revenue"]
    conn.close()


def test_snapshot_has_revenue_logic_and_debt_placeholder_fields(tmp_path):
    """売上速報ロジックv2・固定費(温泉代)・返済仮置きの新fieldがbi_snapshotに出ていること。"""
    conn = db.connect(tmp_path / "t3.sqlite")
    opening_balance.run(conn)
    ctx = monthly.assemble("2026-06", conn)
    sev = {"all_ok": True, "critical": [], "warnings": []}
    bi_export.write_all("2026-06", ctx, checks=[], wb_checks=[], severity=sev, out_dir=tmp_path)
    snap = json.loads((tmp_path / "bi" / "bi_snapshot.json").read_text(encoding="utf-8"))

    revenue_logic_fields = [
        "beds24_revenue_gross_stay", "beds24_point_revenue_included",
        "beds24_point_booking_count", "beds24_coupon_discount_detected",
        "beds24_coupon_discount_amount", "beds24_coupon_discount_booking_count",
        "beds24_cancelled_revenue_excluded", "beds24_revenue_net_for_bi",
        "beds24_revenue_logic_version", "beds24_revenue_logic_status",
        "beds24_revenue_logic_note", "beds24_cancelled_booking_count",
        # 旧field（deprecated。互換性のため0で残る）
        "beds24_coupon_revenue_included", "beds24_coupon_booking_count",
    ]
    debt_placeholder_fields = [
        "hot_spring_fee_monthly", "bank_debt_service_placeholder",
        "takamiya_monthly_equivalent_cash_out", "standard_finance_required_cost",
        "full_debt_reserve_required_cost", "full_debt_reserve_breakeven_revenue",
        "full_debt_reserve_breakeven_achievement_rate",
        "full_debt_reserve_revenue_gap_to_breakeven", "debt_service_note",
    ]
    for f in revenue_logic_fields + debt_placeholder_fields:
        assert f in snap, f"missing field: {f}"

    # 互換field: beds24_stay_month_revenue_excluding_cancelled == beds24_revenue_net_for_bi
    assert snap["beds24_stay_month_revenue_excluding_cancelled"] == snap["beds24_revenue_net_for_bi"]

    # 固定費・返済仮置きの期待値
    assert snap["hot_spring_fee_monthly"] == 160000
    assert snap["bank_debt_service_placeholder"] == 400000
    assert snap["takamiya_monthly_equivalent_cash_out"] == 700000
    assert snap["debt_service_status"] == "返済仮置き"

    # couponは売上に加算されない（deprecated fieldは常に0）
    assert snap["beds24_coupon_revenue_included"] == 0
    assert snap["beds24_coupon_booking_count"] == 0
    # net = gross_stay + point（couponは含まない）
    assert snap["beds24_revenue_net_for_bi"] == (
        snap["beds24_revenue_gross_stay"] + snap["beds24_point_revenue_included"])

    # 高見屋70万円は標準finance BEPに含まれない
    assert snap["standard_finance_required_cost"] == snap["cash_fixed_cost_total"] + 400000
    assert snap["full_debt_reserve_required_cost"] == snap["standard_finance_required_cost"] + 700000
    assert snap["full_debt_reserve_breakeven_revenue"] > snap["finance_breakeven_revenue"]
    conn.close()


def test_same_month_diff_not_headline(tmp_path):
    # revenue_recon の headline fields に revenue_reconciliation_difference が出ないこと（legacy参考値のみ）
    conn = db.connect(tmp_path / "t2.sqlite")
    ctx = monthly.assemble("2026-06", conn)
    rr = ctx["revenue_recon"]
    assert "revenue_reconciliation_difference" not in rr
    assert "legacy_same_month_reference" in rr
    assert rr["same_month_revenue_comparison_applicable"] is False
    conn.close()
