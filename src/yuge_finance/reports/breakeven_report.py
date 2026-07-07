"""損益分岐点レポート出力（Phase D-5）。

data/output/<month>/breakeven/breakeven_summary.json
data/output/<month>/breakeven/breakeven_daily.csv （日次の累積売上 vs 損益分岐点への進捗）
"""
from __future__ import annotations

import calendar
import json
from pathlib import Path
from typing import Dict, List

from .. import csvio
from ..normalize.schema import BookingRecord

DAILY_COLS = ["date", "day_number", "daily_revenue", "cumulative_revenue",
              "breakeven_target_cumulative", "pace_achievement_rate"]

SUMMARY_KEYS = [
    "month", "breakeven_model_version",
    "cash_fixed_cost_before_labor", "accounting_fixed_cost_before_labor",
    "labor_cost_used", "cash_fixed_cost_total", "accounting_fixed_cost_total",
    "variable_cost_rate_total", "contribution_margin_rate",
    "ota_fee_rate_effective", "utilities_variable_rate", "maintenance_variable_rate",
    "linen_reference_rate", "supplies_reference_rate",
    "cash_operating_breakeven_revenue", "cash_operating_breakeven_achievement_rate",
    "cash_revenue_gap_to_breakeven",
    "accounting_operating_breakeven_revenue", "accounting_operating_breakeven_achievement_rate",
    "accounting_revenue_gap_to_breakeven",
    "monthly_debt_principal_payment", "monthly_debt_interest_payment",
    "finance_breakeven_revenue", "finance_breakeven_achievement_rate",
    "finance_revenue_gap_to_breakeven", "debt_service_status", "finance_bep_note",
    "gop_before_success_fee", "mc_fixed_fee", "mc_success_fee", "gop_after_mc",
    "gop_margin_after_mc",
    "required_remaining_revenue_per_day", "required_remaining_room_nights",
    "required_remaining_occupancy_rate",
    "days_in_month", "days_elapsed", "remaining_days_in_month", "breakeven_model_status",
]

# 予約ペース判定（Phase 2）。breakeven_summary.jsonへ統合出力する。
PACE_KEYS = [
    "current_date_jst", "target_month", "days_in_month", "day_of_month",
    "days_elapsed_in_month", "days_remaining_in_month", "month_elapsed_rate",
    "month_remaining_rate", "expected_bep_progress_to_date",
    "expected_bep_progress_rate_to_date", "beds24_revenue_to_date",
    "beds24_month_on_the_books_revenue", "booking_pace_achievement_rate",
    "booking_pace_gap_to_expected", "projected_month_end_revenue",
    "projected_month_end_bep_achievement_rate",
    "booking_pace_status", "booking_pace_label", "booking_pace_reason",
    "pace_model_status",
]


def _daily_pace(month: str, bookings: List[BookingRecord], break_even_revenue,
                exclude_statuses: List[str]) -> List[Dict]:
    y, m = (int(x) for x in month.split("-"))
    dim = calendar.monthrange(y, m)[1]
    by_day = {d: 0.0 for d in range(1, dim + 1)}
    for b in bookings:
        if (b.status or "").lower() in [s.lower() for s in exclude_statuses]:
            continue
        ci = (b.checkin_date or "")[:10]
        if ci[:7] != month:
            continue
        try:
            day_num = int(ci[8:10])
        except ValueError:
            continue
        by_day[day_num] = by_day.get(day_num, 0.0) + b.gross_revenue

    rows = []
    cum = 0.0
    for d in range(1, dim + 1):
        cum += by_day.get(d, 0.0)
        target = (round(break_even_revenue * d / dim) if break_even_revenue else None)
        rate = round(cum / target, 4) if target else None
        rows.append({
            "date": f"{month}-{d:02d}", "day_number": d,
            "daily_revenue": round(by_day.get(d, 0.0)),
            "cumulative_revenue": round(cum),
            "breakeven_target_cumulative": target,
            "pace_achievement_rate": rate,
        })
    return rows


def write(month: str, breakeven: Dict, bookings: List[BookingRecord],
         exclude_statuses: List[str], out_dir: Path, pace: Dict = None) -> Dict:
    be_dir = out_dir / "breakeven"
    be_dir.mkdir(parents=True, exist_ok=True)
    daily = _daily_pace(month, bookings, breakeven.get("cash_operating_breakeven_revenue"),
                        exclude_statuses)
    csvio.write_rows(be_dir / "breakeven_daily.csv", daily, DAILY_COLS)
    summary = {k: breakeven.get(k) for k in SUMMARY_KEYS}
    if pace:
        summary.update({k: pace.get(k) for k in PACE_KEYS})
    (be_dir / "breakeven_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return {"daily_csv": str(be_dir / "breakeven_daily.csv"),
            "summary_json": str(be_dir / "breakeven_summary.json")}
