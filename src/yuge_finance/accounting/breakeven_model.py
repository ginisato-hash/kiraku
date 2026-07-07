"""固定費・変動費モデルによる損益分岐点算出 v2（喜らく単体・現体制運営前提）。

旧経営体制（食事提供・売店・旧人件費・旧派遣料・旧役員報酬）の固定費構造は使わない。
config/fixed_variable_model.yml の現体制固定費・変動費(三浦屋モデル参照比率)と、
labor_model.yml の人件費予測（base_case）を使い、3種類の損益分岐点を算出する。

  1. Cash operating BEP   : 現場運営のメイン指標（減価償却費を除くキャッシュ実態）
  2. Accounting operating BEP : 会計上の営業黒字ライン（減価償却費を含む）
  3. Finance-inclusive BEP    : 支払利息・元本返済を含めた安全ライン

MC費用（固定費15万円＋GOPプラス時のみの成功報酬15%）もここで算出する。
支払利息は営業BEPに入れない（finance_costで別枠）。元本返済はPL費用にしない
（キャッシュ必要売上には反映する）。返済予定表未投入時は0円・status「予定表未投入」。
Beds24速報売上と銀行入金月売上は同月比較しない（本モジュールはBeds24速報売上のみ使用）。
"""
from __future__ import annotations

import calendar
import datetime as _dt
from typing import Dict, Optional

from .. import config

MODEL_VERSION = "kiraku_current_operation_v2"


def _cfg() -> Dict:
    return config.load_yaml("fixed_variable_model.yml")


def _days_in_month(month: str) -> int:
    y, m = (int(x) for x in month.split("-"))
    return calendar.monthrange(y, m)[1]


def _days_elapsed(month: str, as_of: Optional[_dt.date]) -> int:
    if as_of is None:
        as_of = _dt.date.today()
    y, m = (int(x) for x in month.split("-"))
    start = _dt.date(y, m, 1)
    end = _dt.date(y, m, _days_in_month(month))
    if as_of < start:
        return 0
    if as_of >= end:
        return _days_in_month(month)
    return (as_of - start).days + 1


def fixed_cost_totals(cfg: Dict, labor_total_base_case: float) -> Dict:
    """人件費を除く固定費合計(cash/accounting) + 人件費を加えた合計。"""
    items = cfg.get("fixed_cost_items", {})
    cash_before_labor = sum(v["monthly_amount"] for v in items.values()
                            if v.get("include_in_cash_bep"))
    accounting_before_labor = sum(v["monthly_amount"] for v in items.values()
                                  if v.get("include_in_accounting_bep"))
    return {
        "cash_fixed_cost_before_labor": round(cash_before_labor),
        "accounting_fixed_cost_before_labor": round(accounting_before_labor),
        "cash_fixed_cost_total": round(cash_before_labor + labor_total_base_case),
        "accounting_fixed_cost_total": round(accounting_before_labor + labor_total_base_case),
    }


def variable_cost_rates(cfg: Dict) -> Dict:
    """変動費率（三浦屋モデル参照）。linen/suppliesもBEP計算では売上比例の参考レートを使う。"""
    vc = cfg.get("variable_cost_items", {})
    ota = float(vc.get("ota_fee", {}).get("rate_to_revenue", 0.11875))
    utilities = float(vc.get("utilities", {}).get("rate_to_revenue", 0.10))
    maintenance = float(vc.get("maintenance_variable", {}).get("rate_to_revenue", 0.005))
    linen = float(vc.get("linen", {}).get("reference_rate_to_revenue", 0.025))
    supplies = float(vc.get("supplies", {}).get("reference_rate_to_revenue", 0.02))
    total = round(ota + utilities + maintenance + linen + supplies, 6)
    return {
        "ota_fee_rate_effective": ota, "utilities_variable_rate": utilities,
        "maintenance_variable_rate": maintenance, "linen_reference_rate": linen,
        "supplies_reference_rate": supplies, "variable_cost_rate_total": total,
        "contribution_margin_rate": round(1 - total, 6),
    }


def linen_supplies_reference_cost(cfg: Dict, room_nights: float, adr: float,
                                  beds24_revenue: float) -> Dict:
    """RN×ADR換算（Option B）でのリネン・備品消耗品費の参考額。ADR取得不可時のみ売上比例(A)。"""
    vc = cfg.get("variable_cost_items", {})
    linen_rate = float(vc.get("linen", {}).get("reference_rate_to_revenue", 0.025))
    supplies_rate = float(vc.get("supplies", {}).get("reference_rate_to_revenue", 0.02))
    if adr and room_nights is not None:
        linen_cost = room_nights * adr * linen_rate
        supplies_cost = room_nights * adr * supplies_rate
        method = "rn_x_adr"
    else:
        linen_cost = beds24_revenue * linen_rate
        supplies_cost = beds24_revenue * supplies_rate
        method = "revenue_fallback"
    return {"linen_cost_reference": round(linen_cost), "supplies_cost_reference": round(supplies_cost),
            "linen_supplies_method": method}


def mc_cost(cfg: Dict, revenue: float, variable_cost: float, cash_fixed_cost_total: float) -> Dict:
    """MC固定費＋成功報酬（GOPプラス時のみ）。GOP before success fee = revenue - variable_cost - cash_fixed_cost_total。"""
    mc = cfg.get("management_contract", {})
    rate = float(mc.get("success_fee_rate", 0.15))
    gop_before = revenue - variable_cost - cash_fixed_cost_total
    success_fee = max(0.0, gop_before) * rate
    gop_after = gop_before - success_fee
    margin = round(gop_after / revenue, 4) if revenue else None
    return {
        "gop_before_success_fee": round(gop_before),
        "mc_fixed_fee": round(float(mc.get("fixed_fee_monthly", 150000))),
        "mc_success_fee": round(success_fee),
        "gop_after_mc": round(gop_after),
        "gop_margin_after_mc": margin,
    }


def _bep_status(rate: Optional[float]) -> str:
    if rate is None:
        return "要確認"
    if rate >= 1.0:
        return "達成"
    if rate >= 0.8:
        return "未達"
    return "大幅未達"


def build(month: str, beds24_revenue: float, adr: float, labor_total_base_case: float,
         room_nights: float = None,
         monthly_debt_principal_payment: float = 0.0,
         monthly_debt_interest_payment: float = 0.0,
         debt_service_status: str = "予定表未投入",
         room_count: int = None, as_of: Optional[_dt.date] = None) -> Dict:
    cfg = _cfg()
    rooms = room_count or int(config.kiraku().get("property", {}).get("rooms", 19))

    fixed = fixed_cost_totals(cfg, labor_total_base_case)
    rates = variable_cost_rates(cfg)
    cm_rate = rates["contribution_margin_rate"]
    variable_cost = round(beds24_revenue * rates["variable_cost_rate_total"])
    linen_supplies = linen_supplies_reference_cost(cfg, room_nights, adr, beds24_revenue)
    mc = mc_cost(cfg, beds24_revenue, variable_cost, fixed["cash_fixed_cost_total"])

    def bep(fixed_cost):
        return round(fixed_cost / cm_rate) if cm_rate > 0.001 else None

    def achievement(rev, target):
        return round(rev / target, 4) if target else None

    def gap(rev, target):
        return round(max(0.0, target - rev)) if target else None

    # 1. Cash operating BEP（メイン指標）
    cash_bep = bep(fixed["cash_fixed_cost_total"])
    cash_rate = achievement(beds24_revenue, cash_bep)
    cash_gap = gap(beds24_revenue, cash_bep)

    # 2. Accounting operating BEP（減価償却費を含む）
    acct_bep = bep(fixed["accounting_fixed_cost_total"])
    acct_rate = achievement(beds24_revenue, acct_bep)
    acct_gap = gap(beds24_revenue, acct_bep)

    # 3. Finance-inclusive BEP（支払利息・元本返済を含む安全ライン）
    finance_cfg = cfg.get("finance_cost", {})
    finance_required = fixed["cash_fixed_cost_total"]
    if finance_cfg.get("include_interest_in_finance_bep", True):
        finance_required += monthly_debt_interest_payment
    if finance_cfg.get("include_principal_in_finance_bep", True):
        finance_required += monthly_debt_principal_payment
    finance_bep = bep(finance_required)
    finance_rate = achievement(beds24_revenue, finance_bep)
    finance_gap = gap(beds24_revenue, finance_bep)

    dim = _days_in_month(month)
    de = _days_elapsed(month, as_of)
    remaining_days = max(0, dim - de)
    required_rev_per_day = (round(cash_gap / remaining_days) if cash_gap and remaining_days else
                            (0 if cash_gap == 0 else None))
    required_room_nights = (round(cash_gap / adr, 2) if cash_gap and adr else
                            (0 if cash_gap == 0 else None))
    required_occ_rate = (round(required_room_nights / (rooms * remaining_days), 4)
                         if required_room_nights is not None and remaining_days > 0 else None)

    debt_note = ("返済予定表未投入のため、返済込みBEPは未完全（元本・利息とも0円扱い）"
                if debt_service_status == "予定表未投入" else "")

    return {
        "month": month,
        "breakeven_model_version": MODEL_VERSION,
        # --- 固定費・変動費 ---
        **fixed, **rates, **linen_supplies,
        "revenue_variable_cost": variable_cost,
        # --- MC/GOP ---
        **mc,
        # --- Cash operating BEP（主指標）---
        "cash_operating_breakeven_revenue": cash_bep,
        "cash_operating_breakeven_achievement_rate": cash_rate,
        "cash_revenue_gap_to_breakeven": cash_gap,
        # --- Accounting operating BEP ---
        "accounting_operating_breakeven_revenue": acct_bep,
        "accounting_operating_breakeven_achievement_rate": acct_rate,
        "accounting_revenue_gap_to_breakeven": acct_gap,
        # --- Finance-inclusive BEP ---
        "monthly_debt_principal_payment": round(monthly_debt_principal_payment),
        "monthly_debt_interest_payment": round(monthly_debt_interest_payment),
        "finance_breakeven_revenue": finance_bep,
        "finance_breakeven_achievement_rate": finance_rate,
        "finance_revenue_gap_to_breakeven": finance_gap,
        "debt_service_status": debt_service_status,
        "finance_bep_note": debt_note,
        # --- 旧フィールド（後方互換。BI主指標には使わない）---
        "fixed_non_labor_cost_used": fixed["cash_fixed_cost_before_labor"],
        "labor_cost_used": round(labor_total_base_case),
        "step_fixed_cost": fixed["cash_fixed_cost_total"],
        "variable_cost_rate_used": rates["variable_cost_rate_total"],
        "breakeven_revenue_current_structure": cash_bep,
        "breakeven_achievement_rate_current_structure": cash_rate,
        "revenue_gap_to_breakeven": cash_gap,
        # --- 残り必要売上（キャッシュBEP基準）---
        "required_remaining_revenue_per_day": required_rev_per_day,
        "required_remaining_room_nights": required_room_nights,
        "required_remaining_occupancy_rate": required_occ_rate,
        "days_in_month": dim, "days_elapsed": de, "remaining_days_in_month": remaining_days,
        "breakeven_model_status": _bep_status(cash_rate),
    }
