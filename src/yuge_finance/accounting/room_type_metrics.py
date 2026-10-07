"""喜らく単体 部屋タイプ別KPI(ADR/日別稼働率/売上構成)。

部屋タイプの判定はBeds24 `/properties?includeAllRooms=true`(実API、2026-07-10確認)で
取得した roomTypes[].id / qty を `config/kiraku_room_types.yml` に反映して行う。
予約payload(bookings一覧・invoiceItems)には roomName は出現せず roomId のみが入るため、
分類は room_id で行う(room_name は将来payloadに出現した場合の予備として設定に残す)。

revenueは beds24_revenue_logic.calculate_recognized_booking_revenue() で算出する
「price(price=0の手動予約はcharge合計フォールバック済み)そのまま(総額)」を使う
(2026-07-11 v5、ユーザー最終判断で確定: coupon/point/banktransfer/事前決済/現地決済は
すべてpriceの決済チャネル内訳に過ぎず、売上へ別途加算・控除しない)。onsite payment加算
(現状0)はここでは扱わない(既存のbeds24_revenue_gross_stay等と混同しないため)。
キャンセルは除外。月跨ぎ予約は対象月に属する泊数分だけ按分する(_prorate_to_month/
_nights_in_month を beds24_revenue_logic と共有)。

注意: 既存の月次 `adr`/`occupancy`(revenue_recon.py)は checkin月バケット・非按分・
config/kiraku.yml の property.rooms(19室)基準。本モジュールの adr_gross/occupancy_rate_month は
月跨ぎ按分・部屋タイプ設定の宿泊日別有効capacity(旧18室/休館0室/K27 14室)基準のため、月跨ぎ予約がある月はわずかに
異なりうる(意図的な差。どちらも「速報値」であり会計確定売上ではない)。
"""
from __future__ import annotations

import calendar
from datetime import date
from typing import Dict, List

from .. import config
from ..normalize.schema import BookingRecord
from .beds24_revenue_logic import (REVENUE_BASIS, _booking_overlaps_month, _load_raw_index_multi,
                                   _nights_in_month, _prorate_to_month,
                                   calculate_recognized_booking_revenue)


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


def _in_period(spec: Dict, d: str) -> bool:
    """宿泊日dが spec の effective_from(含む)〜effective_to(含まない) に入るか。省略は無制限。"""
    start, end = spec.get("effective_from"), spec.get("effective_to")
    return (not start or d >= str(start)) and (not end or d < str(end))


def capacity_on(spec: Dict, d: str) -> int:
    """宿泊日d(YYYY-MM-DD)にその部屋タイプが持つ客室数。期間外は0(休館・未開業を含む)。"""
    return int(spec.get("capacity_rooms", 0) or 0) if _in_period(spec, d) else 0


def expected_total_rooms_on(d: str) -> int:
    """config/kiraku.yml の room_inventory_profiles(無ければ property.rooms)による宿泊日dの総室数。"""
    prop = config.kiraku().get("property", {})
    for p in prop.get("room_inventory_profiles") or []:
        if _in_period(p, d):
            return int(p.get("rooms", 0) or 0)
    return int(prop.get("rooms") or 0)


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
    # 月跨ぎ予約はraw_json_pathが月ごとに別ファイルになるため、単一ファイル決め打ちだと
    # その月にしかデータの無い予約でcoupon抽出が漏れる(2026-07-11発覚。詳細は
    # beds24_revenue_logic._load_raw_index_multi のdocstring参照)。全ファイルをマージする。
    raw_index = _load_raw_index_multi(relevant)

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
        recognized_revenue = calculate_recognized_booking_revenue(b, raw_index)
        prorated = _prorate_to_month(recognized_revenue, b.checkin_date, b.checkout_date, target_month)
        revenue_by_type[rt] = revenue_by_type.get(rt, 0.0) + prorated
        nights_by_type[rt] = nights_by_type.get(rt, 0) + tm_nights * qty

    # 表示対象: 月内に1日でもcapacity>0、または実予約(泊数/売上)がある部屋タイプ。unknownは実予約時のみ。
    active_types = {k for k, v in room_type_config.items()
                    if k != "unknown" and any(capacity_on(v, d) for d in month_dates)}
    active_types |= {k for k in room_type_config
                     if nights_by_type.get(k, 0) > 0 or revenue_by_type.get(k, 0.0) != 0}

    total_room_revenue = sum(revenue_by_type.values())
    sold_room_nights = sum(nights_by_type.values())
    adr_gross = round(total_room_revenue / sold_room_nights) if sold_room_nights else 0

    # capacityは宿泊日×部屋タイプごとの有効期間で解決する(旧4/休館/K27新5を混算しない)。
    capacity_on_date = {d: {k: capacity_on(v, d) for k, v in room_type_config.items()
                            if k != "unknown"} for d in month_dates}
    total_rooms_on_date = {d: sum(c.values()) for d, c in capacity_on_date.items()}
    available_room_nights = sum(total_rooms_on_date.values())
    occupancy_rate_month = (round(sold_room_nights / available_room_nights * 100, 1)
                            if available_room_nights else 0.0)

    mismatches = [(d, total_rooms_on_date[d], expected_total_rooms_on(d)) for d in month_dates
                  if expected_total_rooms_on(d) and total_rooms_on_date[d] != expected_total_rooms_on(d)]
    if mismatches:
        d, got, want = mismatches[0]
        warnings.append(
            f"config/kiraku_room_types.ymlの部屋タイプ合計({got}室)が"
            f"config/kiraku.ymlの客室数({want}室、{d}時点)と一致しません。"
            "設定を確認してください。")

    room_type_revenue_mix = []
    for key, spec in room_type_config.items():
        rev_raw = revenue_by_type.get(key, 0.0)
        rn = nights_by_type.get(key, 0)
        if key not in active_types and rev_raw == 0 and rn == 0:
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
    display_types = [(k, v) for k, v in room_type_config.items() if k in active_types]
    for d_str in month_dates:
        d = date.fromisoformat(d_str)
        chart_row = {"date": d_str}
        for key, spec in display_types:
            capacity = capacity_on_date[d_str].get(key, 0)
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
        "adr_basis": REVENUE_BASIS,
        "revpar_basis": REVENUE_BASIS,
        "sold_room_nights": sold_room_nights,
        "available_room_nights": available_room_nights,
        "occupancy_rate_month": occupancy_rate_month,
        "room_type_daily_occupancy": daily_rows,
        "room_type_occupancy_chart_series": chart_series,
        "room_type_revenue_mix": room_type_revenue_mix,
        "room_type_metrics_warnings": warnings,
    }
