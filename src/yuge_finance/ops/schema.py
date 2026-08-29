"""スタッフ日次オペレーション用データモデル（許可リスト方式・売上フィールドなし）。

BookingRecord(normalize/schema.py)は revenue と operational な項目が混在しているため、
本パッケージでは絶対にimportしない。ここで定義するdataclassは明示的なpick/mapでのみ
構築し（spreadしてからdeleteする実装は禁止）、フィールドを後から追加する際も
売上系キー(price/revenue/commission/tax/adr/revpar/payment_status等)を絶対に含めないこと。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# assert_no_financial_keys() が検出する禁止キー（大小文字を無視して比較）。
FINANCIAL_KEYS = {
    "revenue", "gross_revenue", "net_revenue", "price", "ota_commission",
    "commission", "tax_amount", "adr", "revpar", "payment_status",
    "invoice_status", "invoiceitems", "cash_operating_breakeven_revenue",
    "beds24_revenue_net_for_bi",
}


@dataclass
class StaffAddress:
    postcode: Optional[str] = None
    prefecture: Optional[str] = None
    city: Optional[str] = None
    rest: Optional[str] = None  # 都道府県/市区町村を除いた残りの住所文字列


@dataclass
class StaffBookingRecord:
    booking_id: str = ""
    guest_name: str = ""
    ota_name: str = ""            # normalize_booking_source() による正規化後の表示名
    booking_source_raw: str = ""  # normalize_booking_source() の生値
    room_type_key: str = ""       # config/kiraku_room_types.yml のキー（例: "twin_toilet"）
    room_type_label: str = ""     # config/kiraku_room_types.yml の label
    room_number: Optional[str] = None   # 現状のBeds24データモデルには物理部屋番号が無いためNone想定
    adults: int = 0
    children: int = 0
    total_guests: int = 0
    checkin_date: str = ""   # YYYY-MM-DD
    checkout_date: str = ""  # YYYY-MM-DD
    arrival_time: Optional[str] = None  # HH:MM（実データに該当fieldが見つかれば）
    phone: Optional[str] = None
    notes: Optional[str] = None
    address: StaffAddress = field(default_factory=StaffAddress)
    status: str = ""  # confirmed/cancelled/new/black/request 等、raw値そのまま


@dataclass
class CleaningRoomState:
    room_type_key: str = ""
    room_type_label: str = ""
    room_number: Optional[str] = None
    state: str = ""  # TURNOVER / STAYOVER / CHECKIN / CHECKOUT / VACANT / CANCELLED / UNASSIGNED
    checkout_booking_id: Optional[str] = None
    checkin_booking_id: Optional[str] = None
    adults: Optional[int] = None
    children: Optional[int] = None
    total_guests: Optional[int] = None
    notes: Optional[str] = None


def _iter_dict_values(obj):
    """dict/listをネストして辿り、(key, value)ペアをdictの箇所だけ再帰的にyieldする。"""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k, v
            yield from _iter_dict_values(v)
    elif isinstance(obj, list):
        for item in obj:
            yield from _iter_dict_values(item)


def assert_no_financial_keys(d) -> None:
    """dict/listをネストして走査し、売上系キーが一切含まれないことを表明する。

    見つかった場合は AssertionError を送出する（サイレントに無視しない）。
    キー比較は大小文字を無視する。d自体がdict/listでない場合は何もしない。
    """
    hits = sorted({
        k for k, _ in _iter_dict_values(d)
        if isinstance(k, str) and k.lower() in FINANCIAL_KEYS
    })
    assert not hits, f"財務系キーがスタッフ運用データに混入しています: {hits}"
