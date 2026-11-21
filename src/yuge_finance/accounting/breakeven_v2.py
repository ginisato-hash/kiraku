"""損益分岐 v2：MC契約（固定150,000円＋MC控除前GOPが正の時の15%）を含む一つの計算契約。

    G(q) = 税抜売上 R(q) − MC以外の営業費用 C(q)
    MC(q) = 150,000 + 0.15 × max(G(q), 0)     （税抜。税込請求の丸めは請求時に一度だけ）
    P(q) = G(q) − MC(q)
    BEP  = 販売可能範囲の整数客室泊 q で P(q) ≥ 0 となる最小点

シナリオは明示する: 単価は対象月の泊日ADR、販売客室泊は営業日に均等配分。人件費は既存
labor_model.yml の段階モデル（稼働日・高稼働日で増える）を q ごとに再計算する。利益の
単調性を仮定した二分法は使わず、0..販売可能客室泊の全整数点を評価する。到達できなければ
到達不可と返す。人件費は給与・勤怠の実績同期ではなく仮定（出所を version で示す）。
"""
from __future__ import annotations

from typing import Dict, List, Optional

MC_CONTRACT_VERSION = "MC-150000+15PCT-POSITIVE-GOP@2026-11-19"
MODEL_VERSION = "bep-v2-contract-mc"
MC_FIXED_JPY = 150_000
MC_RATE_PCT = 15
LABOR_SOURCE_ASSUMPTION = "assumption"
LABOR_SOURCE_MF_ACTUAL = "mf_attendance_actual"
# L-2: the cost comes from the MF給与 支給控除一覧表 (pay records), not from attendance punches.
LABOR_SOURCE_MF_PAYROLL = "mf_payroll_actual"


def mc_fee_ex_tax(gop: float) -> float:
    """契約MC（税抜）。正のGOPにだけ15%。"""
    return MC_FIXED_JPY + max(gop, 0.0) * MC_RATE_PCT / 100


def even_distribution(q: int, capacities: List[int]) -> List[int]:
    """q 客室泊を営業日（capacity>0）に均等配分。各日の在庫を超えない。"""
    open_days = [i for i, c in enumerate(capacities) if c > 0]
    out = [0] * len(capacities)
    remaining = q
    # 1周ずつ各営業日へ1泊ずつ置く（在庫上限を守る）。q ≤ 総在庫なら必ず配り切れる。
    while remaining > 0 and open_days:
        placed = False
        for i in open_days:
            if remaining == 0:
                break
            if out[i] < capacities[i]:
                out[i] += 1
                remaining -= 1
                placed = True
        if not placed:
            break
    return out


def labor_for_days(cfg: Dict, rooms_per_day: List[int], capacities: List[int]) -> float:
    """labor_model.yml の段階モデルを日別販売室数から計算（固定給は休館月も残る）。"""
    matsumoto = cfg.get("fixed_staff", {}).get("matsumoto", {})
    front = cfg.get("front", {})
    cleaning = cfg.get("cleaning", {})
    night = cfg.get("night_security", {})
    threshold = float(cleaning.get("high_occupancy_threshold", 0.70))
    workdays = int(matsumoto.get("expected_workdays_base", cfg.get("rules", {}).get("default_matsumoto_workdays", 22)))
    occupied = sum(1 for r in rooms_per_day if r >= 1)
    high = sum(1 for r, c in zip(rooms_per_day, capacities) if c and r / c > threshold)
    uncovered = max(0, occupied - min(workdays, occupied))
    return (float(matsumoto.get("monthly_salary_gross", 334000))
            + uncovered * float(front.get("cost_per_uncovered_occupied_day", 9600))
            + occupied * float(cleaning.get("base_cost_per_occupied_day", 5500))
            + high * float(cleaning.get("additional_high_occupancy_cost_per_day", 5500))
            + occupied * float(night.get("cost_per_occupied_day", 7500)))


def labor_actual(attendance: Dict, payroll_cost_jpy: Optional[float] = None) -> Dict:
    """MF勤怠実績（mf_attendance_csv.AttendanceMonth.summary()）→ 実績人件費の入力。

    勤怠CSVは時間だけで円を持たない。円は給与・事業主負担の実績（オーナー提供の給与出力）が
    渡された時だけ cost に入る。時間×仮定時給で円を作らない。勤怠に例外日が残る月も cost を
    出さない（部分集計を実績と呼ばない）。
    """
    complete = bool(attendance.get("complete"))
    if not complete:
        status, cost = "attendance_incomplete", None
    elif payroll_cost_jpy is None:
        status, cost = "needs_payroll_export", None
    else:
        status, cost = "computed", float(payroll_cost_jpy)
    return {"labor_source": LABOR_SOURCE_MF_ACTUAL, "month": attendance.get("month"), "attendance_complete": complete,
            "employee_count": attendance.get("employee_count", 0), "worked_days": attendance.get("worked_days", 0),
            "worked_hours": attendance.get("worked_hours", 0.0),
            "exception_count": len(attendance.get("exceptions") or []), "cost_status": status, "cost": cost}


def labor_actual_from_payroll(payroll: Dict, attendance: Optional[Dict] = None) -> Dict:
    """MF給与 支給控除一覧表（mf_payroll_csv.PayrollMonth.summary()）→ 実績人件費の入力。

    cost は verification_status = "verified"（合計行・事業者・確定済み・対象人数・会社負担・月末締め）の時だけ。
    出所を分けて持つ：人件費は給与帳票、時間は給与計算に取り込まれた勤怠項目、打刻は attendance（別に渡された時だけ）。
    片方の完備で他方を保証しない。会社負担は MF の計算額（employer_burden_basis）。
    """
    verified = payroll.get("verification_status") == "verified" and payroll.get("period_alignment") == "calendar_month" and bool(payroll.get("work_month"))
    att = None if attendance is None else {"complete": bool(attendance.get("complete")), "worked_hours": attendance.get("worked_hours"),
                                            "worked_days": attendance.get("worked_days"), "exception_count": len(attendance.get("exceptions") or [])}
    payroll_hours = payroll.get("worked_hours")
    return {"labor_source": LABOR_SOURCE_MF_PAYROLL, "month": payroll.get("work_month"),
            "cost_status": "computed" if verified else "unverified", "cost": float(payroll["labor_cost_jpy"]) if verified else None,
            "cost_source": "MF給与 支給控除一覧表（確定済み支給月）",
            "verification_problems": list(payroll.get("verification_problems") or []),
            "gross_pay": payroll.get("gross_pay_jpy"), "employer_burden": payroll.get("employer_burden_jpy"),
            "employer_burden_basis": payroll.get("employer_burden_basis"), "employee_count": payroll.get("employee_count", 0),
            "payroll_hours": {"source": "MF給与 勤怠項目（給与計算へ取り込まれた時間）", "status": payroll.get("hours_status"),
                              "worked_hours": payroll_hours, "worked_days": payroll.get("worked_days"),
                              "unverified_columns": list(payroll.get("hours_unverified_columns") or []), "unit_basis": payroll.get("hours_unit_basis")},
            "attendance": att if att is not None else {"status": "not_provided"},
            "hours_difference": (None if att is None or payroll_hours is None or att["worked_hours"] is None else round(payroll_hours - att["worked_hours"], 2)),
            "pay_date": payroll.get("pay_date"), "closing_date": payroll.get("closing_date"), "source_sha256": payroll.get("source_sha256")}


def profit(q: int, adr: float, variable_rate: float, fixed_non_mc: float, labor_cfg: Dict, capacities: List[int],
           labor_override: Optional[float] = None, override_source: str = LABOR_SOURCE_MF_ACTUAL) -> Dict:
    revenue = q * adr
    labor = labor_for_days(labor_cfg, even_distribution(q, capacities), capacities) if labor_override is None else labor_override
    gop = revenue - revenue * variable_rate - fixed_non_mc - labor
    mc = mc_fee_ex_tax(gop)
    return {"room_nights": q, "revenue": revenue, "labor": labor, "gop_before_mc": gop, "mc_ex_tax": mc, "profit_after_mc": gop - mc,
            "labor_source": LABOR_SOURCE_ASSUMPTION if labor_override is None else override_source}


def build(capacities: List[int], adr: Optional[float], variable_rate: float, fixed_non_mc: float, labor_cfg: Dict,
          actual: Dict, labor_actual_input: Optional[Dict] = None) -> Dict:
    """capacities: 対象月の日別販売可能室数（休館0）。adr: 泊日ADR（無ければNone）。actual: 実績（泊日売上・販売泊）。

    labor_actual_input: labor_actual() の結果。cost が入っている時だけ実績点の人件費を差し替える。
    BEP曲線（仮想の q）は常に段階モデル仮定のまま — 実績人件費は実際の販売泊にしか対応しない。
    """
    available = sum(capacities)
    actual_cost = (labor_actual_input or {}).get("cost")
    base = {"version": MODEL_VERSION, "mc_contract_version": MC_CONTRACT_VERSION,
            "scenario": {"adr": adr, "distribution": "even_over_open_days", "labor": "labor_model.yml staged assumption (not payroll actuals)",
                         "labor_source": LABOR_SOURCE_ASSUMPTION, "variable_rate": variable_rate, "fixed_non_mc": round(fixed_non_mc)},
            "available_room_nights": available}
    if labor_actual_input is not None:
        base["labor_actual"] = labor_actual_input
    if adr is None or adr <= 0 or available == 0:
        return {**base, "status": "not_computable", "reason": "no_rooms_on_sale" if available == 0 else "no_adr"}
    bep = None
    for q in range(0, available + 1):
        p = profit(q, adr, variable_rate, fixed_non_mc, labor_cfg, capacities)
        if p["profit_after_mc"] >= 0:
            bep = p
            break
    sold = int(actual.get("sold_room_nights") or 0)
    act = profit(sold, actual.get("revenue", 0.0) / sold if sold else adr, variable_rate, fixed_non_mc, labor_cfg, capacities,
                 labor_override=actual_cost, override_source=(labor_actual_input or {}).get("labor_source", LABOR_SOURCE_MF_ACTUAL)) if sold <= available else None
    out = {**base, "status": "reachable" if bep else "not_reachable",
           "actual": None if act is None else {k: round(v) if isinstance(v, float) else v for k, v in act.items()}}
    if bep:
        out.update({"breakeven_room_nights": bep["room_nights"], "breakeven_revenue": round(bep["revenue"]),
                    "breakeven_occupancy_pct": round(bep["room_nights"] / available * 100, 1),
                    "breakeven_achievement_rate": round(actual.get("revenue", 0.0) / bep["revenue"], 4) if bep["revenue"] else None,
                    "at_breakeven": {k: round(v) if isinstance(v, float) else v for k, v in bep.items()}})
    return out
