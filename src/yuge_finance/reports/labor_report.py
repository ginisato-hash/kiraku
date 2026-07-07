"""人件費予測レポート出力（Phase C-5）。

data/output/<month>/labor/labor_forecast_daily.csv
data/output/<month>/labor/labor_forecast_summary.json
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict

from .. import csvio

DAILY_COLS = ["date", "occupied_room_nights_day", "occupancy_rate_day",
              "occupied_day_flag", "high_occupancy_day_flag"]

SUMMARY_KEYS = [
    "month", "labor_fixed_salary_cost", "labor_extra_front_cost", "labor_cleaning_cost",
    "labor_night_security_cost", "labor_total_forecast", "labor_total_low_case",
    "labor_total_base_case", "labor_total_high_case", "labor_occupied_days",
    "labor_high_occupancy_days", "labor_room_nights", "labor_uncovered_front_days",
    "labor_cost_per_occupied_room_night", "labor_cost_to_beds24_revenue",
    "labor_model_status",
]


def write(month: str, result: Dict, out_dir: Path) -> Dict:
    labor_dir = out_dir / "labor"
    labor_dir.mkdir(parents=True, exist_ok=True)
    csvio.write_rows(labor_dir / "labor_forecast_daily.csv", result["daily"], DAILY_COLS)
    summary = {k: result.get(k) for k in SUMMARY_KEYS}
    (labor_dir / "labor_forecast_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return {"daily_csv": str(labor_dir / "labor_forecast_daily.csv"),
            "summary_json": str(labor_dir / "labor_forecast_summary.json")}
