"""固定費・変動費モデル v2（現体制運営前提）による損益分岐点。

旧経営体制（食事・売店・旧人件費・旧派遣料・旧役員報酬）の固定費構造は使わない。
"""
from yuge_finance.accounting import breakeven_model

TOL = 100  # 円。丸め許容誤差


def _cfg():
    return breakeven_model._cfg()


# ---------------- fixed cost tests ----------------
def test_cash_fixed_before_labor_matches_expected():
    """温泉代16万円を含む(753,662 + 160,000)。"""
    fixed = breakeven_model.fixed_cost_totals(_cfg(), labor_total_base_case=0)
    assert fixed["cash_fixed_cost_before_labor"] == 913662


def test_accounting_fixed_before_labor_matches_expected():
    """温泉代16万円を含む(1,247,662 + 160,000)。"""
    fixed = breakeven_model.fixed_cost_totals(_cfg(), labor_total_base_case=0)
    assert fixed["accounting_fixed_cost_before_labor"] == 1407662


def test_hot_spring_fee_present_in_config():
    cfg = _cfg()
    assert cfg["fixed_cost_items"]["hot_spring_fee"]["monthly_amount"] == 160000
    assert cfg["fixed_cost_items"]["hot_spring_fee"]["include_in_cash_bep"] is True
    assert cfg["fixed_cost_items"]["hot_spring_fee"]["include_in_accounting_bep"] is True


def test_depreciation_excluded_from_cash_bep_included_in_accounting_bep():
    cfg = _cfg()
    dep = cfg["fixed_cost_items"]["depreciation"]
    assert dep["include_in_cash_bep"] is False
    assert dep["include_in_accounting_bep"] is True
    fixed = breakeven_model.fixed_cost_totals(cfg, labor_total_base_case=0)
    # 会計固定費 - キャッシュ固定費 = 減価償却費(494,000)
    assert (fixed["accounting_fixed_cost_before_labor"]
            - fixed["cash_fixed_cost_before_labor"]) == dep["monthly_amount"] == 494000


def test_old_labor_and_food_retail_not_included():
    cfg = _cfg()
    labels = {v["label"] for v in cfg["fixed_cost_items"].values()}
    forbidden = {"役員報酬", "給料手当", "派遣料", "雑給", "商品仕入高", "売店仕入", "接待交際費"}
    assert not (labels & forbidden)


def test_mc_fixed_fee_included_in_fixed_costs():
    cfg = _cfg()
    assert cfg["fixed_cost_items"]["mc_fixed_fee"]["monthly_amount"] == 150000
    assert cfg["fixed_cost_items"]["mc_fixed_fee"]["include_in_cash_bep"] is True


# ---------------- variable cost tests ----------------
def test_ota_fee_rate_effective():
    rates = breakeven_model.variable_cost_rates(_cfg())
    assert rates["ota_fee_rate_effective"] == 0.11875


def test_total_variable_cost_rate_and_contribution_margin():
    rates = breakeven_model.variable_cost_rates(_cfg())
    assert rates["variable_cost_rate_total"] == 0.26875
    assert rates["contribution_margin_rate"] == 0.73125


def test_linen_and_supplies_included_in_variable_cost():
    rates = breakeven_model.variable_cost_rates(_cfg())
    assert rates["linen_reference_rate"] == 0.025
    assert rates["supplies_reference_rate"] == 0.02


def test_product_purchase_excluded_from_variable_cost():
    cfg = _cfg()
    labels = {v["label"] for v in cfg["variable_cost_items"].values()}
    assert "商品仕入高" not in labels
    assert "売店仕入" not in labels


# ---------------- 60% occupancy labor/breakeven test ----------------
def test_60pct_occupancy_breakeven_golden_values():
    """温泉代16万円を含む固定費での60%稼働ゴールデン値。"""
    res = breakeven_model.build(
        "2026-06", beds24_revenue=0, adr=11499, labor_total_base_case=800800,
        room_nights=342)
    assert res["cash_fixed_cost_total"] == 1714462       # 913,662 + 800,800
    assert res["accounting_fixed_cost_total"] == 2208462  # 1,407,662 + 800,800
    assert abs(res["cash_operating_breakeven_revenue"] - round(1714462 / 0.73125)) <= TOL
    assert abs(res["accounting_operating_breakeven_revenue"] - round(2208462 / 0.73125)) <= TOL


def test_cash_bep_lower_than_accounting_bep_due_to_depreciation():
    res = breakeven_model.build("2026-06", beds24_revenue=0, adr=11499,
                               labor_total_base_case=800800, room_nights=342)
    assert res["cash_operating_breakeven_revenue"] < res["accounting_operating_breakeven_revenue"]


# ---------------- MC success fee test ----------------
def test_mc_success_fee_positive_gop():
    cfg = _cfg()
    result = breakeven_model.mc_cost(cfg, revenue=3000000, variable_cost=806250,
                                     cash_fixed_cost_total=1554462)
    assert result["gop_before_success_fee"] == 639288
    assert abs(result["mc_success_fee"] - 95893) <= TOL
    assert abs(result["gop_after_mc"] - 543395) <= TOL


def test_mc_success_fee_zero_when_gop_negative():
    cfg = _cfg()
    result = breakeven_model.mc_cost(cfg, revenue=500000, variable_cost=806250,
                                     cash_fixed_cost_total=1554462)
    assert result["gop_before_success_fee"] < 0
    assert result["mc_success_fee"] == 0
    assert result["gop_after_mc"] == result["gop_before_success_fee"]


# ---------------- finance-inclusive BEP ----------------
def test_finance_bep_higher_than_cash_bep_when_debt_service_present():
    no_debt = breakeven_model.build("2026-06", beds24_revenue=0, adr=11499,
                                    labor_total_base_case=800800, room_nights=342)
    with_debt = breakeven_model.build(
        "2026-06", beds24_revenue=0, adr=11499, labor_total_base_case=800800,
        room_nights=342, monthly_debt_principal_payment=600000,
        monthly_debt_interest_payment=50000, debt_service_status="予定表投入済")
    assert with_debt["finance_breakeven_revenue"] > no_debt["finance_breakeven_revenue"]
    assert with_debt["finance_breakeven_revenue"] > with_debt["cash_operating_breakeven_revenue"]


def test_debt_status_is_placeholder_when_schedule_missing_but_placeholder_active():
    """返済予定表未投入でも、金融機関返済40万円の仮置きが有効なため『返済仮置き』になる。"""
    res = breakeven_model.build("2026-06", beds24_revenue=0, adr=11499,
                               labor_total_base_case=800800, room_nights=342)
    assert res["debt_service_status"] == "返済仮置き"
    assert res["monthly_debt_principal_payment"] == 0
    assert res["monthly_debt_interest_payment"] == 0
    assert "仮置き" in res["debt_service_note"]


def test_interest_not_in_operating_bep_but_in_finance_bep():
    res = breakeven_model.build(
        "2026-06", beds24_revenue=0, adr=11499, labor_total_base_case=800800,
        room_nights=342, monthly_debt_principal_payment=600000,
        monthly_debt_interest_payment=50000, debt_service_status="予定表投入済")
    # cash_operating_breakeven_revenue には利息・返済仮置きが含まれない
    assert res["cash_operating_breakeven_revenue"] == round(
        res["cash_fixed_cost_total"] / res["contribution_margin_rate"])
    # finance_breakeven_revenue には利息+元本+金融機関返済仮置き40万円が含まれる
    finance_required = res["cash_fixed_cost_total"] + 400000 + 600000 + 50000
    assert res["standard_finance_required_cost"] == finance_required
    assert abs(res["finance_breakeven_revenue"]
              - round(finance_required / res["contribution_margin_rate"])) <= 1
    # 実スケジュール投入済のstatusはそのまま尊重される（仮置きで上書きしない）
    assert res["debt_service_status"] == "予定表投入済"


# ---------------- 金融機関返済仮置き / 高見屋別シナリオ ----------------
def test_bank_debt_service_placeholder_matches_config():
    res = breakeven_model.build("2026-06", beds24_revenue=0, adr=11499,
                               labor_total_base_case=800800, room_nights=342)
    assert res["bank_debt_service_placeholder"] == 400000
    assert res["takamiya_monthly_equivalent_cash_out"] == 700000


def test_takamiya_not_included_in_standard_finance_bep():
    """高見屋70万円は標準finance BEPに混ざらない。"""
    res = breakeven_model.build("2026-06", beds24_revenue=0, adr=11499,
                               labor_total_base_case=800800, room_nights=342)
    expected_standard = res["cash_fixed_cost_total"] + 400000  # 高見屋70万円を含まない
    assert res["standard_finance_required_cost"] == expected_standard
    assert res["finance_breakeven_revenue"] == round(expected_standard / res["contribution_margin_rate"])


def test_takamiya_included_only_in_full_debt_reserve_bep():
    """高見屋70万円は高見屋返済込みBEPにのみ含まれる。"""
    res = breakeven_model.build("2026-06", beds24_revenue=1000000, adr=11499,
                               labor_total_base_case=800800, room_nights=342)
    expected_full = res["standard_finance_required_cost"] + 700000
    assert res["full_debt_reserve_required_cost"] == expected_full
    assert res["full_debt_reserve_breakeven_revenue"] == round(
        expected_full / res["contribution_margin_rate"])
    assert res["full_debt_reserve_breakeven_revenue"] > res["finance_breakeven_revenue"]
    assert res["full_debt_reserve_breakeven_achievement_rate"] == round(
        1000000 / res["full_debt_reserve_breakeven_revenue"], 4)
    assert res["full_debt_reserve_revenue_gap_to_breakeven"] == max(
        0, res["full_debt_reserve_breakeven_revenue"] - 1000000)


# ---------------- 主指標フィールド ----------------
def test_primary_bep_field_is_cash_operating():
    res = breakeven_model.build("2026-06", beds24_revenue=1000000, adr=11499,
                               labor_total_base_case=800800, room_nights=342)
    assert res["cash_operating_breakeven_revenue"] is not None
    assert res["breakeven_model_version"] == "kiraku_current_operation_v2"
    # 旧フィールドは後方互換で残るが主指標と同じ値
    assert res["breakeven_revenue_current_structure"] == res["cash_operating_breakeven_revenue"]
