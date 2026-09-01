"""日付×物理客室番号(KIRAKU_ROOM_ORDER)単位の清掃状態分類（会計・売上ロジックとは完全に独立）。

18室の物理客室マスターを基準に、対象日1件ごとに必ず18行を生成する
（「予約のある客室だけ」ではなく「実在する客室すべて」）。room_numberが
KIRAKU_ROOM_ORDERで解決できない予約(マッピング未確定/unitId不明)は、
18室表には混ぜずUNASSIGNEDとして別途保持する(推測で客室へ割り当てない)。
"""
from __future__ import annotations

from datetime import date as _date
from typing import Dict, List, Optional, Tuple

from .. import config
from .room_master import KIRAKU_ROOM_ORDER
from .schema import CleaningGuestInfo, CleaningRoomState, StaffBookingRecord

_DEFAULT_CANCELLED_STATUSES = ["cancelled", "canceled", "black"]


def _cancelled_statuses() -> List[str]:
    """config/kiraku.yml の revenue.exclude_statuses と同じ判定基準を再利用する。"""
    return config.kiraku().get("revenue", {}).get(
        "exclude_statuses", _DEFAULT_CANCELLED_STATUSES)


def _is_cancelled(status: str, cancelled_statuses: List[str]) -> bool:
    return str(status or "").strip().lower() in {str(s).lower() for s in cancelled_statuses}


def compute_night_progress(checkin: str, checkout: str,
                           target_date: str) -> Tuple[Optional[int], Optional[int]]:
    """(current_night_index, total_nights)を返す。target_dateがcheckin<=d<=checkoutの
    範囲外、または日付parse不可の場合は(None, None)。

    例: checkin=8/29, checkout=8/31 (2泊) -> 8/29:(1,2) 8/30:(2,2) 8/31(checkout日):(2,2)
    （チェックアウト日は「直前に完了した泊数」を表示し、宿泊進捗と矛盾させない）。
    """
    try:
        ci = _date.fromisoformat(checkin)
        co = _date.fromisoformat(checkout)
        d = _date.fromisoformat(target_date)
    except (ValueError, TypeError):
        return None, None
    total = (co - ci).days
    if total <= 0:
        return None, None
    if d == ci:
        return 1, total
    if ci < d < co:
        return (d - ci).days + 1, total
    if d == co:
        return total, total
    return None, total


_EMPTY_CLEANING_EXTRA = {
    "guest_notice": None,
    "children_age_7plus_count": None,
    "children_age_data_available": False,
    "payment_due_at_property": False,
    "amount_due_at_property": None,
}


def _guest_info(b: StaffBookingRecord, cleaning_extra_by_booking_id: Optional[Dict] = None) -> CleaningGuestInfo:
    extra = (cleaning_extra_by_booking_id or {}).get(b.booking_id, _EMPTY_CLEANING_EXTRA)
    return CleaningGuestInfo(
        booking_id=b.booking_id,
        guest_name=b.guest_name,
        adults=b.adults,
        children=b.children,
        total_guests=b.total_guests,
        check_in=b.checkin_date,
        check_out=b.checkout_date,
        arrival_time=b.arrival_time,
        source=b.ota_name,
        children_age_7plus_count=extra["children_age_7plus_count"],
        children_age_data_available=extra["children_age_data_available"],
        guest_notice=extra["guest_notice"],
        payment_due_at_property=extra["payment_due_at_property"],
        amount_due_at_property=extra["amount_due_at_property"],
    )


def classify_cleaning_for_date(bookings: List[StaffBookingRecord], target_date: str,
                               cleaning_extra_by_booking_id: Optional[Dict] = None) -> List[CleaningRoomState]:
    """target_date(YYYY-MM-DD, Asia/Tokyo基準)について、KIRAKU_ROOM_ORDERの18室
    それぞれの清掃状態を1行ずつ、かつ解決できなかった予約をUNASSIGNED行として返す。

    cleaning_extra_by_booking_id: extract.extract_cleaning_extra()の結果を
    booking_idでindexしたdict(省略時は全項目None/False相当のデフォルト)。
    """
    cancelled_statuses = _cancelled_statuses()

    by_room: Dict[str, Dict[str, List[StaffBookingRecord]]] = {}
    unassigned: List[StaffBookingRecord] = []

    for b in bookings:
        touches_checkout = b.checkout_date == target_date
        touches_checkin = b.checkin_date == target_date
        touches_stayover = bool(b.checkin_date) and bool(b.checkout_date) and \
            b.checkin_date < target_date < b.checkout_date
        if not (touches_checkout or touches_checkin or touches_stayover):
            continue
        if _is_cancelled(b.status, cancelled_statuses):
            # CANCELLEDはoccupancy/cleaning判定に含めない(通常rowを埋めない)。
            continue

        if not b.room_number or b.room_number not in KIRAKU_ROOM_ORDER:
            unassigned.append(b)
            continue

        bucket = by_room.setdefault(b.room_number, {"checkout": [], "checkin": [], "stayover": []})
        if touches_checkout:
            bucket["checkout"].append(b)
        elif touches_checkin:
            bucket["checkin"].append(b)
        elif touches_stayover:
            bucket["stayover"].append(b)

    rows: List[CleaningRoomState] = []

    for room_number in KIRAKU_ROOM_ORDER:
        data = by_room.get(room_number, {"checkout": [], "checkin": [], "stayover": []})
        checkouts, checkins, stayovers = data["checkout"], data["checkin"], data["stayover"]

        if checkouts and checkins:
            # 同一物理客室でのTURNOVER: CHECKOUT行+CHECKIN行に分割せず1行に統合する。
            # 複数該当は想定外(物理1室=同時1予約のはず)だが、防御的に先頭のみ使用。
            departing, arriving = checkouts[0], checkins[0]
            idx, total = compute_night_progress(arriving.checkin_date, arriving.checkout_date, target_date)
            rows.append(CleaningRoomState(
                date=target_date, room_number=room_number, status="TURNOVER",
                departing_guest=_guest_info(departing, cleaning_extra_by_booking_id),
                arriving_guest=_guest_info(arriving, cleaning_extra_by_booking_id),
                staying_guest=None, current_night_index=idx, total_nights=total,
                source_instruction="入替",
            ))
        elif checkouts:
            departing = checkouts[0]
            idx, total = compute_night_progress(departing.checkin_date, departing.checkout_date, target_date)
            rows.append(CleaningRoomState(
                date=target_date, room_number=room_number, status="CHECKOUT",
                departing_guest=_guest_info(departing, cleaning_extra_by_booking_id), arriving_guest=None, staying_guest=None,
                current_night_index=idx, total_nights=total, source_instruction="",
            ))
        elif checkins:
            arriving = checkins[0]
            idx, total = compute_night_progress(arriving.checkin_date, arriving.checkout_date, target_date)
            rows.append(CleaningRoomState(
                date=target_date, room_number=room_number, status="CHECKIN",
                departing_guest=None, arriving_guest=_guest_info(arriving, cleaning_extra_by_booking_id), staying_guest=None,
                current_night_index=idx, total_nights=total, source_instruction="",
            ))
        elif stayovers:
            staying = stayovers[0]
            idx, total = compute_night_progress(staying.checkin_date, staying.checkout_date, target_date)
            rows.append(CleaningRoomState(
                date=target_date, room_number=room_number, status="STAYOVER",
                departing_guest=None, arriving_guest=None, staying_guest=_guest_info(staying, cleaning_extra_by_booking_id),
                current_night_index=idx, total_nights=total, source_instruction="",
            ))
        else:
            rows.append(CleaningRoomState(date=target_date, room_number=room_number, status="VACANT"))

    # UNASSIGNED: room_numberが未解決(マッピング未確定/unitId不明)の予約。
    # 18室表には混ぜず、その日に関係する分だけ個別に可視化する(サイレントに消さない)。
    for b in unassigned:
        rows.append(CleaningRoomState(
            date=target_date, room_number=None, status="UNASSIGNED",
            arriving_guest=_guest_info(b, cleaning_extra_by_booking_id) if b.checkin_date == target_date else None,
            departing_guest=_guest_info(b, cleaning_extra_by_booking_id) if b.checkout_date == target_date else None,
            staying_guest=_guest_info(b, cleaning_extra_by_booking_id) if (b.checkin_date and b.checkout_date
                                             and b.checkin_date < target_date < b.checkout_date) else None,
        ))

    return rows
