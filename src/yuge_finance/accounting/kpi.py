"""損益分岐(break-even)KPI（喜らく単体）。

固定費・変動費は config/kiraku.yml の breakeven 設定で分類。
MCコストは補助科目(MC固定/MC変動)で固定・変動に振り分ける。
"""
from __future__ import annotations

import calendar
from datetime import date
from typing import Dict, List, Optional

from .. import config
from ..normalize.schema import BookingRecord, JournalEntry


def _days_in_month(month: str) -> int:
    y, m = (int(x) for x in month.split("-"))
    return calendar.monthrange(y, m)[1]


def _days_elapsed(month: str, as_of: Optional[date]) -> int:
    if as_of is None:
        as_of = date.today()
    y, m = (int(x) for x in month.split("-"))
    start = date(y, m, 1)
    end = date(y, m, _days_in_month(month))
    if as_of < start:
        return 0
    if as_of >= end:
        return _days_in_month(month)
    return (as_of - start).days + 1


def split_costs(entries: List[JournalEntry]) -> Dict[str, float]:
    cfg = config.kiraku().get("breakeven", {})
    fixed_accs = set(cfg.get("fixed_accounts", []))
    var_accs = set(cfg.get("variable_accounts", []))
    mc_fixed = cfg.get("mc_fixed_subaccount", "MC固定")
    mc_var = cfg.get("mc_variable_subaccount", "MC変動")
    fixed = 0.0
    variable = 0.0
    for e in entries:
        if e.debit_account == "MCコスト":
            if e.debit_subaccount == mc_var:
                variable += e.debit_amount
            else:
                fixed += e.debit_amount   # MC固定 / 既定は固定
        elif e.debit_account in var_accs:
            variable += e.debit_amount
        elif e.debit_account in fixed_accs:
            fixed += e.debit_amount
    return {"fixed": round(fixed, 2), "variable": round(variable, 2)}


def room_nights(bookings: List[BookingRecord], month: str) -> int:
    cfg = config.kiraku()
    exclude = cfg.get("revenue", {}).get("exclude_statuses", ["cancelled"])
    total = 0
    for b in bookings:
        if b.is_cancelled(exclude) or b.checkin_date[:7] != month:
            continue
        total += max(1, b.stay_nights) * max(1, b.rooms)
    return total


def build(month: str, confirmed: List[JournalEntry], revenue: float,
          bookings: List[BookingRecord], as_of: Optional[date] = None) -> Dict:
    cfg = config.kiraku().get("breakeven", {})
    costs = split_costs(confirmed)
    fixed, variable = costs["fixed"], costs["variable"]

    v_ratio = (variable / revenue) if revenue > 0 else 0.0
    if 1 - v_ratio > 0.01:
        be_revenue = round(fixed / (1 - v_ratio), 0)
    else:
        be_revenue = None  # 変動費率>=100% で算出不能

    rn = room_nights(bookings, month)
    adr = round(revenue / rn) if rn > 0 else cfg.get("default_adr", 15000)

    achievement = round(revenue / be_revenue, 4) if be_revenue else None
    remaining_rev = round(max(0.0, be_revenue - revenue)) if be_revenue else None
    remaining_rooms = (round(remaining_rev / adr, 1)
                       if (be_revenue and adr > 0) else None)

    # 月末着地見込
    dim = _days_in_month(month)
    de = _days_elapsed(month, as_of)
    landing_revenue = round(revenue * dim / de) if de > 0 else round(revenue)
    landing_achievement = (round(landing_revenue / be_revenue, 4)
                           if be_revenue else None)

    return {
        "revenue": round(revenue),
        "fixed_cost": fixed, "variable_cost": variable,
        "variable_ratio": round(v_ratio, 4),
        "adr": adr, "room_nights": rn,
        "days_in_month": dim, "days_elapsed": de,
        "損益分岐売上": be_revenue,
        "損益分岐達成率": achievement,
        "損益分岐まで残り売上": remaining_rev,
        "損益分岐まで残り販売室数": remaining_rooms,
        "月末着地見込売上": landing_revenue,
        "月末着地見込ベース達成率": landing_achievement,
    }
