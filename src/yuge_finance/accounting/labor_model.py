"""人件費予測モデル（喜らく単体・Phase C）。

Beds24日別稼働データから月次人件費を「速報推計」する。給与確定仕訳ではない
（BI用の管理会計予測。実給与は銀行/現金/会計士確定値で処理する）。
設定は config/labor_model.yml。
"""
from __future__ import annotations

import calendar
import datetime as _dt
from typing import Dict, List

from .. import config
from ..normalize.schema import BookingRecord


def _cfg() -> Dict:
    return config.load_yaml("labor_model.yml")


def _days_in_month(month: str) -> int:
    y, m = (int(x) for x in month.split("-"))
    return calendar.monthrange(y, m)[1]


def _month_dates(month: str) -> List[_dt.date]:
    y, m = (int(x) for x in month.split("-"))
    n = calendar.monthrange(y, m)[1]
    return [_dt.date(y, m, d) for d in range(1, n + 1)]


def daily_occupancy(month: str, bookings: List[BookingRecord],
                    exclude_statuses: List[str] = None) -> List[Dict]:
    """日別 販売室数・稼働率・稼働フラグ を返す。"""
    cfg = _cfg()
    room_count = int(cfg.get("property", {}).get("room_count", 19))
    threshold = float(cfg.get("cleaning", {}).get("high_occupancy_threshold", 0.70))
    exclude = exclude_statuses or ["cancelled", "canceled", "black"]

    active = []
    for b in bookings:
        if (b.status or "").lower() in [s.lower() for s in exclude]:
            continue
        try:
            ci = _dt.date.fromisoformat((b.checkin_date or "")[:10])
            co = _dt.date.fromisoformat((b.checkout_date or "")[:10])
        except ValueError:
            continue
        active.append((ci, co, max(1, b.rooms)))

    rows = []
    for day in _month_dates(month):
        occupied_rooms = sum(rooms for ci, co, rooms in active if ci <= day < co)
        rate = occupied_rooms / room_count if room_count else 0.0
        rows.append({
            "date": day.isoformat(),
            "occupied_room_nights_day": occupied_rooms,
            "occupancy_rate_day": round(rate, 4),
            "occupied_day_flag": occupied_rooms >= 1,
            "high_occupancy_day_flag": rate > threshold,
        })
    return rows


def _monthly_costs(cfg: Dict, occupied_days: int, high_occupancy_days: int,
                   matsumoto_workdays: int) -> Dict:
    matsumoto = cfg.get("fixed_staff", {}).get("matsumoto", {})
    front = cfg.get("front", {})
    cleaning = cfg.get("cleaning", {})
    night = cfg.get("night_security", {})

    matsumoto_workdays_used = min(matsumoto_workdays, occupied_days)
    uncovered_front_days = max(0, occupied_days - matsumoto_workdays_used)

    fixed_salary_cost = float(matsumoto.get("monthly_salary_gross", 334000))
    extra_front_cost = uncovered_front_days * float(front.get("cost_per_uncovered_occupied_day", 9600))
    cleaning_cost = (occupied_days * float(cleaning.get("base_cost_per_occupied_day", 5500))
                     + high_occupancy_days * float(cleaning.get("additional_high_occupancy_cost_per_day", 5500)))
    night_security_cost = occupied_days * float(night.get("cost_per_occupied_day", 7500))
    total = fixed_salary_cost + extra_front_cost + cleaning_cost + night_security_cost

    return {
        "matsumoto_workdays_used": matsumoto_workdays_used,
        "uncovered_front_days": uncovered_front_days,
        "fixed_salary_cost": round(fixed_salary_cost),
        "extra_front_cost": round(extra_front_cost),
        "cleaning_cost": round(cleaning_cost),
        "night_security_cost": round(night_security_cost),
        "total_labor_forecast": round(total),
    }


def build(month: str, bookings: List[BookingRecord],
         beds24_revenue: float = 0.0) -> Dict:
    cfg = _cfg()
    daily = daily_occupancy(month, bookings)
    occupied_days = sum(1 for d in daily if d["occupied_day_flag"])
    high_occupancy_days = sum(1 for d in daily if d["high_occupancy_day_flag"])
    room_nights = sum(d["occupied_room_nights_day"] for d in daily)

    matsumoto = cfg.get("fixed_staff", {}).get("matsumoto", {})
    default_wd = int(cfg.get("rules", {}).get("default_matsumoto_workdays", 22))
    wd_low = int(matsumoto.get("expected_workdays_max", 23))    # 出勤日数多い=コスト低
    wd_base = int(matsumoto.get("expected_workdays_base", default_wd))
    wd_high = int(matsumoto.get("expected_workdays_min", 21))   # 出勤日数少ない=コスト高

    base = _monthly_costs(cfg, occupied_days, high_occupancy_days, wd_base)
    low = _monthly_costs(cfg, occupied_days, high_occupancy_days, wd_low)
    high = _monthly_costs(cfg, occupied_days, high_occupancy_days, wd_high)

    status = "推計" if bookings or occupied_days == 0 else "要確認"
    cost_per_rn = round(base["total_labor_forecast"] / room_nights, 2) if room_nights else None
    cost_to_rev = (round(base["total_labor_forecast"] / beds24_revenue, 4)
                  if beds24_revenue else None)

    return {
        "month": month,
        "daily": daily,
        "labor_occupied_days": occupied_days,
        "labor_high_occupancy_days": high_occupancy_days,
        "labor_room_nights": room_nights,
        "labor_uncovered_front_days": base["uncovered_front_days"],
        "labor_fixed_salary_cost": base["fixed_salary_cost"],
        "labor_extra_front_cost": base["extra_front_cost"],
        "labor_cleaning_cost": base["cleaning_cost"],
        "labor_night_security_cost": base["night_security_cost"],
        "labor_total_forecast": base["total_labor_forecast"],
        "labor_total_low_case": low["total_labor_forecast"],
        "labor_total_base_case": base["total_labor_forecast"],
        "labor_total_high_case": high["total_labor_forecast"],
        "labor_cost_per_occupied_room_night": cost_per_rn,
        "labor_cost_to_beds24_revenue": cost_to_rev,
        "labor_model_status": status,
    }
