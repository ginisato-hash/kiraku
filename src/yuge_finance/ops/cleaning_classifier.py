"""日付×部屋タイプ単位の清掃状態分類（会計・売上ロジックとは完全に独立）。

既知の制約（推測で解消しようとしない）: Beds24はroom TYPE(タイプ単位のqty)のみを公開し、
物理的な部屋番号を持たない。そのため、同一タイプでキャンセルが実稼働(チェックイン/
チェックアウト/連泊)と共存する場合、キャンセルされたのがどの物理室だったのかは判別
できない。よってキャンセルは「他に何も無い場合にのみCANCELLEDとして可視化し、実稼働と
共存する場合は単に無視する」という保守的な扱いにとどめる。
"""
from __future__ import annotations

from typing import Dict, List

from .. import config
from .schema import CleaningRoomState, StaffBookingRecord

_DEFAULT_CANCELLED_STATUSES = ["cancelled", "canceled", "black"]


def _cancelled_statuses() -> List[str]:
    """config/kiraku.yml の revenue.exclude_statuses と同じ判定基準を再利用する。"""
    return config.kiraku().get("revenue", {}).get(
        "exclude_statuses", _DEFAULT_CANCELLED_STATUSES)


def _is_cancelled(status: str, cancelled_statuses: List[str]) -> bool:
    return str(status or "").strip().lower() in {str(s).lower() for s in cancelled_statuses}


def _empty_bucket() -> Dict[str, list]:
    return {"checkout": [], "checkin": [], "stayover": [], "cancelled": []}


def classify_cleaning_for_date(bookings: List[StaffBookingRecord], target_date: str,
                               room_types_config: Dict) -> List[CleaningRoomState]:
    """target_date(YYYY-MM-DD)について、部屋タイプごとの清掃状態一覧を返す。

    capacity_rooms > 1 で同一タイプに複数の予約イベントが同日に重なる場合は、
    1タイプ1行に集約せず、イベント単位(予約単位)で個別の行を返す
    (housekeepingが実際に「何件の清掃が必要か」を数えられるようにするため)。
    """
    cancelled_statuses = _cancelled_statuses()
    by_type: Dict[str, Dict[str, list]] = {}

    for b in bookings:
        touches_checkout = b.checkout_date == target_date
        touches_checkin = b.checkin_date == target_date
        touches_stayover = bool(b.checkin_date) and bool(b.checkout_date) and \
            b.checkin_date < target_date < b.checkout_date
        if not (touches_checkout or touches_checkin or touches_stayover):
            continue

        bucket = by_type.setdefault(b.room_type_key, _empty_bucket())
        if _is_cancelled(b.status, cancelled_statuses):
            bucket["cancelled"].append(b)
            continue
        if touches_checkout:
            bucket["checkout"].append(b)
        elif touches_checkin:
            bucket["checkin"].append(b)
        elif touches_stayover:
            bucket["stayover"].append(b)

    rows: List[CleaningRoomState] = []

    for rt_key in sorted(set(room_types_config.keys()) | set(by_type.keys())):
        spec = room_types_config.get(rt_key, {})
        label = spec.get("label", rt_key)
        capacity = int(spec.get("capacity_rooms", 0) or 0)
        data = by_type.get(rt_key, _empty_bucket())

        if rt_key == "unknown":
            # 未分類(room_idがどの部屋タイプにも一致しない)予約は、清掃対象から
            # サイレントに消さず、必ずUNASSIGNEDとして個別可視化する。
            for b in data["checkout"] + data["checkin"] + data["stayover"]:
                rows.append(CleaningRoomState(
                    room_type_key="unknown", room_type_label=label, room_number=None,
                    state="UNASSIGNED",
                    checkout_booking_id=b.booking_id if b.checkout_date == target_date else None,
                    checkin_booking_id=b.booking_id if b.checkin_date == target_date else None,
                    adults=b.adults, children=b.children, total_guests=b.total_guests,
                    notes=b.notes,
                ))
            continue

        if capacity == 0 and not any(data.values()):
            # 設定上capacity=0(未設定/廃止タイプ)かつ何のイベントも無いタイプは出力しない。
            continue

        checkouts, checkins, stayovers, cancelled = (
            data["checkout"], data["checkin"], data["stayover"], data["cancelled"])

        if not checkouts and not checkins and not stayovers:
            if cancelled:
                # このタイプ・この日には他に実稼働イベントが無く、キャンセルだけがある。
                for b in cancelled:
                    rows.append(CleaningRoomState(
                        room_type_key=rt_key, room_type_label=label, room_number=None,
                        state="CANCELLED",
                        notes=f"キャンセル済み予約 booking_id={b.booking_id}",
                    ))
            else:
                rows.append(CleaningRoomState(
                    room_type_key=rt_key, room_type_label=label, room_number=None,
                    state="VACANT",
                ))
            continue

        # TURNOVER: チェックアウト予約とチェックイン予約を同数分だけペアリングする。
        # 余った側はそれぞれ単独のCHECKOUT/CHECKINとして扱う。
        pair_count = min(len(checkouts), len(checkins))
        for i in range(pair_count):
            co, ci = checkouts[i], checkins[i]
            rows.append(CleaningRoomState(
                room_type_key=rt_key, room_type_label=label, room_number=None,
                state="TURNOVER",
                checkout_booking_id=co.booking_id, checkin_booking_id=ci.booking_id,
                # 部屋を準備する対象は「これから入居する」ゲスト側の人数を使う。
                adults=ci.adults, children=ci.children, total_guests=ci.total_guests,
                notes=ci.notes,
            ))
        for co in checkouts[pair_count:]:
            rows.append(CleaningRoomState(
                room_type_key=rt_key, room_type_label=label, room_number=None,
                state="CHECKOUT", checkout_booking_id=co.booking_id,
                adults=co.adults, children=co.children, total_guests=co.total_guests,
                notes=co.notes,
            ))
        for ci in checkins[pair_count:]:
            rows.append(CleaningRoomState(
                room_type_key=rt_key, room_type_label=label, room_number=None,
                state="CHECKIN", checkin_booking_id=ci.booking_id,
                adults=ci.adults, children=ci.children, total_guests=ci.total_guests,
                notes=ci.notes,
            ))
        for so in stayovers:
            rows.append(CleaningRoomState(
                room_type_key=rt_key, room_type_label=label, room_number=None,
                state="STAYOVER",
                adults=so.adults, children=so.children, total_guests=so.total_guests,
                notes=so.notes,
            ))

    return rows
