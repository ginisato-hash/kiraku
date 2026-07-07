"""予約ペース判定モデル（喜らく単体）。

「現在のBEP達成率」（月間Cash BEPに対する現在累計）と、
「予約ペース」（月内進捗率に対して、最終的にBEPを達成できそうか）を分けて扱う。
月初・月中は達成率が低く出るのは当然であり、それ自体は「大幅未達」等の表記を維持する。
一方、Beds24速報売上（宿泊月全体の予約済み＝on-the-books）が月内経過率に対して
十分なペースであれば、別指標として「予約ペース：グリーン」を出せるようにする。
「大幅未達」と「予約ペース：グリーン」は矛盾しない（別軸の指標）。
"""
from __future__ import annotations

import calendar
import datetime as _dt
from typing import Dict, Optional

from .. import config

JST = _dt.timezone(_dt.timedelta(hours=9))

STATUS_LABELS = {"green": "グリーン", "yellow": "要注意", "red": "レッド", "unknown": "判定不可"}


def _days_in_month(month: str) -> int:
    y, m = (int(x) for x in month.split("-"))
    return calendar.monthrange(y, m)[1]


def _now_jst() -> _dt.datetime:
    return _dt.datetime.now(JST)


def _month_bounds(month: str):
    y, m = (int(x) for x in month.split("-"))
    dim = calendar.monthrange(y, m)[1]
    return _dt.date(y, m, 1), _dt.date(y, m, dim), dim


def build(month: str,
         cash_operating_breakeven_revenue: Optional[float],
         beds24_month_on_the_books_revenue: float,
         beds24_revenue_to_date: Optional[float] = None,
         adr: Optional[float] = None,
         room_count: int = None,
         now_jst: Optional[_dt.datetime] = None) -> Dict:
    now = now_jst or _now_jst()
    current_date = now.date()
    rooms = room_count or int(config.kiraku().get("property", {}).get("rooms", 19))
    month_start, month_end, dim = _month_bounds(month)

    if current_date > month_end:                 # 対象月が過去月
        day_of_month = dim
        days_elapsed = dim
        month_elapsed_rate = 1.0
    elif current_date < month_start:              # 対象月が未来月
        day_of_month = 0
        days_elapsed = 0
        month_elapsed_rate = 0.0
    else:                                          # 対象月が当月
        day_of_month = current_date.day
        days_elapsed = day_of_month
        month_elapsed_rate = round(days_elapsed / dim, 6)

    days_remaining = dim - days_elapsed
    month_remaining_rate = round(1 - month_elapsed_rate, 6)

    cash_bep = cash_operating_breakeven_revenue
    expected_bep_progress_to_date = (round(cash_bep * month_elapsed_rate)
                                     if cash_bep and cash_bep > 0 else None)
    expected_bep_progress_rate_to_date = month_elapsed_rate

    beds24_otb = beds24_month_on_the_books_revenue or 0.0
    booking_pace_achievement_rate = (
        round(beds24_otb / expected_bep_progress_to_date, 4)
        if expected_bep_progress_to_date and expected_bep_progress_to_date > 0 else None)
    booking_pace_gap_to_expected = (
        round(beds24_otb - expected_bep_progress_to_date)
        if expected_bep_progress_to_date is not None else None)

    # B. OTB projection（初期実装の主判定）。Beds24速報売上は既に月末までの予約を含むため、
    # 単純な OTB/経過率(A. time pace projection)は過大評価になりやすく主判定には使わない。
    projected_month_end_revenue = round(beds24_otb)
    projected_month_end_bep_achievement_rate = (
        round(projected_month_end_revenue / cash_bep, 4) if cash_bep and cash_bep > 0 else None)

    cash_gap = max(0.0, cash_bep - beds24_otb) if cash_bep and cash_bep > 0 else None
    required_rev_per_day = (round(cash_gap / days_remaining) if cash_gap and days_remaining else
                            (0 if cash_gap == 0 else None))
    required_room_nights = (round(cash_gap / adr, 2) if cash_gap and adr else
                            (0 if cash_gap == 0 else None))
    required_occ_rate = (round(required_room_nights / (rooms * days_remaining), 4)
                         if required_room_nights is not None and days_remaining > 0 else None)

    status, reason = _pace_status(cash_bep, expected_bep_progress_to_date,
                                  projected_month_end_bep_achievement_rate,
                                  booking_pace_achievement_rate)
    pace_model_status = "推計" if current_date >= month_start else "対象外"

    return {
        "current_date_jst": current_date.isoformat(),
        "target_month": month,
        "days_in_month": dim,
        "day_of_month": day_of_month,
        "days_elapsed_in_month": days_elapsed,
        "days_remaining_in_month": days_remaining,
        "month_elapsed_rate": month_elapsed_rate,
        "month_remaining_rate": month_remaining_rate,
        "cash_operating_breakeven_revenue": cash_bep,
        "expected_bep_progress_to_date": expected_bep_progress_to_date,
        "expected_bep_progress_rate_to_date": expected_bep_progress_rate_to_date,
        "beds24_revenue_to_date": beds24_revenue_to_date,
        "beds24_month_on_the_books_revenue": round(beds24_otb),
        "booking_pace_achievement_rate": booking_pace_achievement_rate,
        "booking_pace_gap_to_expected": booking_pace_gap_to_expected,
        "projected_month_end_revenue": projected_month_end_revenue,
        "projected_month_end_bep_achievement_rate": projected_month_end_bep_achievement_rate,
        "required_remaining_revenue_per_day": required_rev_per_day,
        "required_remaining_room_nights": required_room_nights,
        "required_remaining_occupancy_rate": required_occ_rate,
        "booking_pace_status": status,
        "booking_pace_label": STATUS_LABELS[status],
        "booking_pace_reason": reason,
        "pace_model_status": pace_model_status,
    }


def _pace_status(cash_bep, expected_bep_progress_to_date,
                 projected_rate, pace_rate):
    if not cash_bep or cash_bep <= 0:
        return "unknown", "キャッシュBEPが算出できません。"
    if not expected_bep_progress_to_date or expected_bep_progress_to_date <= 0:
        # 月内経過率が0（対象月が未来月、または月初当日で四捨五入により0）
        return "unknown", "対象月がまだ開始していないため、予約ペースを判定できません。"
    if projected_rate is not None and projected_rate >= 1.0:
        return "green", "現時点の予約済み売上がキャッシュBEP以上です。"
    if projected_rate is not None and projected_rate >= 0.85 and pace_rate is not None and pace_rate >= 1.0:
        return "yellow", "月内進捗に対しては順調ですが、月末BEPにはまだ不足しています。"
    if pace_rate is not None and pace_rate >= 1.0:
        return "yellow", "月内進捗比では遅れていませんが、月末BEP達成には追加予約が必要です。"
    return "red", "月内進捗に対して予約売上が不足しています。"
