"""喜らく単体 部屋タイプ別KPI(ADR/日別稼働率/売上構成)。

部屋タイプの判定はBeds24 `/properties?includeAllRooms=true`(実API、2026-07-10確認)で
取得した roomTypes[].id / qty を `config/kiraku_room_types.yml` に反映して行う。
予約payload(bookings一覧・invoiceItems)には roomName は出現せず roomId のみが入るため、
分類は room_id で行う(room_name は将来payloadに出現した場合の予備として設定に残す)。

revenueは既存の修正済みnormalized price(BookingRecord.gross_revenue。price=0の手動予約は
charge合計フォールバック済み)をそのまま使う。onsite payment加算(現状0)はここでは扱わない
(既存のbeds24_revenue_gross_stay等と混同しないため)。キャンセルは除外。月跨ぎ予約は
対象月に属する泊数分だけ按分する(_prorate_to_month/_nights_in_month を beds24_revenue_logic
と共有)。

注意: 既存の月次 `adr`/`occupancy`(revenue_recon.py)は checkin月バケット・非按分・
config.kiraku.yml の property.rooms(19室)基準。本モジュールの adr_gross/occupancy_rate_month は
月跨ぎ按分・部屋タイプ設定の実室数合計(18室)基準のため、月跨ぎ予約がある月はわずかに
異なりうる(意図的な差。どちらも「速報値」であり会計確定売上ではない)。
"""
from __future__ import annotations

import calendar
from datetime import date
from typing import Dict, List

from .. import config
from ..normalize.schema import BookingRecord
from .beds24_revenue_logic import (_booking_overlaps_month, _nights_in_month,
                                   _prorate_to_month)


def load_room_type_config() -> Dict:
    return config.load_yaml("kiraku_room_types.yml").get("room_types", {})


def _room_id_lookup(room_type_config: Dict) -> Dict[str, str]:
    lookup = {}
    for key, spec in room_type_config.items():
        for rid in (spec.get("match", {}) or {}).get("room_ids") or []:
            lookup[str(rid)] = key
    return lookup


def classify_room_type(booking: BookingRecord, room_type_config: Dict) -> str:
    """booking.room_id を設定のroom_idsと照合し、部屋タイプキーを返す(未一致はunknown)。"""
    lookup = _room_id_lookup(room_type_config)
    return lookup.get(str(booking.room_id or ""), "unknown")


def _days_in_month(month: str) -> int:
    y, m = (int(x) for x in month.split("-"))
    return calendar.monthrange(y, m)[1]


def calculate_room_type_metrics(bookings: List[BookingRecord], target_month: str,
                                room_type_config: Dict, exclude_statuses: List[str]) -> Dict:
    """対象月の部屋タイプ別ADR・日別稼働率・売上構成を計算する。"""
    days = _days_in_month(target_month)
    month_dates = [f"{target_month}-{d:02d}" for d in range(1, days + 1)]

    relevant = [b for b in bookings
               if _booking_overlaps_month(b.checkin_date, b.checkout_date, target_month)]
    active = [b for b in relevant if not b.is_cancelled(exclude_statuses)]

    warnings: List[str] = []
    room_type_of: Dict[str, str] = {}
    for b in active:
        rt = classify_room_type(b, room_type_config)
        room_type_of[b.booking_id] = rt
        if rt == "unknown":
            warnings.append(f"未分類のroomId({b.room_id!r})の予約があります: booking_id={b.booking_id}")

    # ---- revenue mix + sold_room_nights (月跨ぎ按分) ----
    revenue_by_type: Dict[str, float] = {}
    nights_by_type: Dict[str, int] = {}
    for b in active:
        rt = room_type_of[b.booking_id]
        qty = max(b.rooms, 1)
        tm_nights = _nights_in_month(b.checkin_date, b.checkout_date, target_month)
        prorated = _prorate_to_month(b.gross_revenue, b.checkin_date, b.checkout_date, target_month)
        revenue_by_type[rt] = revenue_by_type.get(rt, 0.0) + prorated
        nights_by_type[rt] = nights_by_type.get(rt, 0) + tm_nights * qty

    total_room_revenue = sum(revenue_by_type.values())
    sold_room_nights = sum(nights_by_type.values())
    adr_gross = round(total_room_revenue / sold_room_nights) if sold_room_nights else 0

    capacity_by_type = {k: int(v.get("capacity_rooms", 0) or 0) for k, v in room_type_config.items()}
    room_type_total_rooms = sum(v for k, v in capacity_by_type.items() if k != "unknown")
    available_room_nights = room_type_total_rooms * days
    occupancy_rate_month = (round(sold_room_nights / available_room_nights * 100, 1)
                            if available_room_nights else 0.0)

    configured_total_rooms = config.kiraku().get("property", {}).get("rooms")
    if configured_total_rooms and room_type_total_rooms != configured_total_rooms:
        warnings.append(
            f"config/kiraku_room_types.ymlの部屋タイプ合計({room_type_total_rooms}室)が"
            f"config/kiraku.ymlのproperty.rooms({configured_total_rooms}室)と一致しません。"
            "設定を確認してください。")

    room_type_revenue_mix = []
    for key, spec in room_type_config.items():
        rev_raw = revenue_by_type.get(key, 0.0)
        rn = nights_by_type.get(key, 0)
        if key == "unknown" and rev_raw == 0 and rn == 0:
            continue
        rev = round(rev_raw)
        share = round(rev_raw / total_room_revenue * 100, 1) if total_room_revenue else 0.0
        adr_type = round(rev_raw / rn) if rn else 0
        room_type_revenue_mix.append({
            "room_type": key,
            "room_type_label": spec.get("label", key),
            "revenue": rev,
            "share": share,
            "sold_room_nights": rn,
            "adr": adr_type,
        })
    room_type_revenue_mix.sort(key=lambda r: r["revenue"], reverse=True)

    # ---- daily occupancy by room type (checkin <= date < checkout) ----
    daily_rows = []
    chart_series = []
    display_types = [(k, v) for k, v in room_type_config.items()
                     if k != "unknown" or nights_by_type.get("unknown", 0) > 0]
    for d_str in month_dates:
        d = date.fromisoformat(d_str)
        chart_row = {"date": d_str}
        for key, spec in display_types:
            capacity = capacity_by_type.get(key, 0)
            sold = 0
            for b in active:
                if room_type_of[b.booking_id] != key:
                    continue
                ci = date.fromisoformat(b.checkin_date[:10])
                co = date.fromisoformat(b.checkout_date[:10]) if b.checkout_date else ci
                if ci <= d < co:
                    sold += max(b.rooms, 1)
            occ = round(sold / capacity * 100, 1) if capacity else 0.0
            if capacity and sold > capacity:
                warnings.append(
                    f"{d_str} {spec.get('label', key)}の稼働率が100%を超えています: "
                    f"sold={sold} available={capacity}")
            label = spec.get("label", key)
            daily_rows.append({
                "date": d_str, "room_type": key, "room_type_label": label,
                "sold_rooms": sold, "available_rooms": capacity, "occupancy_rate": occ,
            })
            chart_row[label] = occ
        chart_series.append(chart_row)

    return {
        "adr_gross": adr_gross,
        "sold_room_nights": sold_room_nights,
        "available_room_nights": available_room_nights,
        "occupancy_rate_month": occupancy_rate_month,
        "room_type_daily_occupancy": daily_rows,
        "room_type_occupancy_chart_series": chart_series,
        "room_type_revenue_mix": room_type_revenue_mix,
        "room_type_metrics_warnings": warnings,
    }
