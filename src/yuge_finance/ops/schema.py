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
class CleaningGuestInfo:
    """清掃指示書向けallow-list（住所・電話・メール・パスポート・国籍は含めない）。

    children_age_7plus_count/children_age_data_available: extract.extract_children_age_7plus()
    参照。現時点は実データに年齢fieldが存在しないため常に(None, False)。
    guest_notice: extract.extract_guest_notice()参照。ソースはguestCommentsのみ
    (内部メモ用のnotes/comments/groupNote/messageとは別経路)。
    onsite_payment_required/onsite_payment_amount: 2026-09にユーザー要件で明示的に
    許可された、Cleaning DTOで唯一許容される財務フィールド(「現地で回収すべき金額」の
    operational data)。CLEANING_FORBIDDEN_KEYSのブラックリストには元々このキー名は
    含まれない(revenue/price/commission等とは別語彙)ため技術的な変更は不要だが、
    ここに明示的に記録しておく。それ以外の財務フィールド(revenue/ADR/RevPAR/
    commission/invoice detail等)は引き続き禁止。
    """
    booking_id: str = ""
    guest_name: str = ""
    adults: int = 0
    children: int = 0
    total_guests: int = 0
    check_in: str = ""
    check_out: str = ""
    arrival_time: Optional[str] = None
    source: str = ""  # normalize_booking_source() による正規化後のOTA表示名
    children_age_7plus_count: Optional[int] = None
    children_age_data_available: bool = False
    guest_notice: Optional[str] = None
    onsite_payment_required: bool = False
    onsite_payment_amount: Optional[int] = None


@dataclass
class CleaningRoomState:
    """日付×物理客室番号(KIRAKU_ROOM_ORDER)単位の清掃状態（canonical Cleaning DTO）。

    override(instruction上書き)はCloudflare KV側でrequest時にmergeする
    （既存アーキテクチャと同じ。Python側はsource_instructionのみ生成し、
    effective_instruction/has_override/updated_atはWorker層の責務）。
    """
    date: str = ""
    room_number: Optional[str] = None  # KIRAKU_ROOM_ORDER内の値、またはUNASSIGNEDならNone
    status: str = ""  # CHECKIN/CHECKOUT/STAYOVER/TURNOVER/VACANT/UNASSIGNED/CANCELLED
    departing_guest: Optional[CleaningGuestInfo] = None
    arriving_guest: Optional[CleaningGuestInfo] = None
    staying_guest: Optional[CleaningGuestInfo] = None
    current_night_index: Optional[int] = None
    total_nights: Optional[int] = None
    source_instruction: str = ""


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


# 清掃指示（cleaning）出力専用の禁止キー: 財務系に加え、清掃業務に不要なPII
# (住所/電話/メール/パスポート/国籍等)も禁止する。Daily Ops側(StaffBookingRecord)は
# phone/addressを意図的に許可しているため、この禁止リストはCleaning DTO専用。
#
# 2026-09の例外(明示的に許可): onsite_payment_required / onsite_payment_amount のみ。
# 「現地で回収すべき金額」というoperational dataであり、revenue/ADR/RevPAR/commission/
# invoice detail等の禁止語彙とは重ならないため、このセット自体に変更は不要
# (元々ブロックしていない)。それ以外の財務フィールドは引き続きこのリストで禁止する。
CLEANING_FORBIDDEN_KEYS = FINANCIAL_KEYS | {
    "phone", "mobile", "address", "postcode", "prefecture", "city", "rest",
    "email", "passport", "nationality", "country", "notes", "rate", "amount",
}


def assert_no_forbidden_cleaning_keys(d) -> None:
    """清掃指示データに財務キー・不要PII(住所/電話/メール/パスポート/国籍等)が
    一切含まれないことを表明する。見つかった場合はAssertionErrorを送出する。
    """
    hits = sorted({
        k for k, _ in _iter_dict_values(d)
        if isinstance(k, str) and k.lower() in CLEANING_FORBIDDEN_KEYS
    })
    assert not hits, f"禁止キーが清掃データに混入しています: {hits}"
