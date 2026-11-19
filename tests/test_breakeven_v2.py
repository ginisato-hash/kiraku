"""損益分岐 v2（MC契約込み）の独立期待値テスト。Issue #72 §4.8。"""
import math

from yuge_finance import db, monthly
from yuge_finance.accounting import breakeven_v2 as v2

# 人件費を固定給だけにした設定（段階増加なし）で解析式と突き合わせる。
FLAT_LABOR = {"fixed_staff": {"matsumoto": {"monthly_salary_gross": 300000, "expected_workdays_base": 31}},
              "front": {"cost_per_uncovered_occupied_day": 0}, "cleaning": {"base_cost_per_occupied_day": 0,
              "additional_high_occupancy_cost_per_day": 0, "high_occupancy_threshold": 0.7}, "night_security": {"cost_per_occupied_day": 0}}


def test_contract_mc_is_not_the_old_fixed_fee_bep():
    # 旧BEP地点でMC前GOPが150,000円 → 新契約MC172,500円、MC後利益 −22,500円（分岐点ではない）
    assert v2.mc_fee_ex_tax(150_000) == 172_500
    assert 150_000 - v2.mc_fee_ex_tax(150_000) == -22_500
    # 整数円GOP: 176,470円では −0.5円、176,471円では +0.35円
    assert math.isclose(176_470 - v2.mc_fee_ex_tax(176_470), -0.5)
    assert math.isclose(176_471 - v2.mc_fee_ex_tax(176_471), 0.35)
    assert v2.mc_fee_ex_tax(-10_000) == 150_000


def test_analytic_bep_with_fixed_costs_only():
    """C = F + vR（人件費一定）なら R_BEP = (F + 150000/(1−0.15)) / (1−v)。整数客室泊ではその直後の点。"""
    capacities = [10] * 30
    adr, v, fixed = 10_000.0, 0.2, 500_000.0
    out = v2.build(capacities, adr, v, fixed, FLAT_LABOR, {"revenue": 0.0, "sold_room_nights": 0})
    f = fixed + 300_000  # 人件費（固定給のみ）
    r_bep = (f + 150_000 / 0.85) / (1 - v)
    assert out["status"] == "reachable"
    assert out["breakeven_room_nights"] == math.ceil(r_bep / adr)
    assert out["at_breakeven"]["profit_after_mc"] >= 0
    # 1泊手前はMC後で赤字（最小点であること）
    prev = v2.profit(out["breakeven_room_nights"] - 1, adr, v, fixed, FLAT_LABOR, capacities)
    assert prev["profit_after_mc"] < 0
    # 旧式（MC固定費を分子に足すだけ）より必要売上は大きい
    assert out["breakeven_revenue"] > (f + 150_000) / (1 - v)


def test_staged_labor_is_recomputed_per_point_not_assumed_monotone():
    """稼働日・高稼働日で人件費が段階的に増えても、全整数点を評価して最小点を返す。"""
    staged = {"fixed_staff": {"matsumoto": {"monthly_salary_gross": 334000, "expected_workdays_base": 22}},
              "front": {"cost_per_uncovered_occupied_day": 9600}, "cleaning": {"base_cost_per_occupied_day": 5500,
              "additional_high_occupancy_cost_per_day": 5500, "high_occupancy_threshold": 0.7}, "night_security": {"cost_per_occupied_day": 7500}}
    capacities = [14] * 31
    out = v2.build(capacities, 15_000.0, 0.26875, 700_000.0, staged, {"revenue": 0.0, "sold_room_nights": 0})
    q = out["breakeven_room_nights"]
    assert all(v2.profit(x, 15_000.0, 0.26875, 700_000.0, staged, capacities)["profit_after_mc"] < 0 for x in range(q))
    assert out["at_breakeven"]["profit_after_mc"] >= 0


def test_unreachable_and_closed_months_are_not_a_target():
    # 料金が低すぎて在庫を全部売っても届かない → 到達不可（100%超を目標にしない）
    out = v2.build([2] * 30, 1_000.0, 0.3, 2_000_000.0, FLAT_LABOR, {"revenue": 0.0, "sold_room_nights": 0})
    assert out["status"] == "not_reachable" and "breakeven_revenue" not in out
    # 休館（在庫0）は旧室数へ戻さず計算不能、固定費は消さない
    closed = v2.build([0] * 30, 10_000.0, 0.2, 500_000.0, FLAT_LABOR, {"revenue": 0.0, "sold_room_nights": 0})
    assert closed["status"] == "not_computable" and closed["reason"] == "no_rooms_on_sale"
    assert v2.labor_for_days(FLAT_LABOR, [0] * 30, [0] * 30) == 300_000


def test_even_distribution_respects_daily_inventory():
    out = v2.even_distribution(5, [2, 0, 2, 1])
    assert sum(out) == 5 and all(o <= c for o, c in zip(out, [2, 0, 2, 1])) and out[1] == 0


def test_breakeven_v2_present_in_snapshot(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    ctx = monthly.assemble("2026-08", conn)
    b = ctx["room_type_metrics"]["stay_date_kpi"]["breakeven_v2"]
    assert b["version"] == v2.MODEL_VERSION and b["mc_contract_version"] == v2.MC_CONTRACT_VERSION
    assert b["status"] in {"reachable", "not_reachable", "not_computable"}
