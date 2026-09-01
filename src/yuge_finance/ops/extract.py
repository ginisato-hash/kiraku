"""raw Beds24 予約dict -> StaffBookingRecord（許可リストの明示pick/mapのみ）。

BookingRecord(normalize/schema.py)やbeds24_client.normalize_booking()は一切importせず、
raw dictから直接抽出する。OTA正規化のみ accounting/beds24_revenue_logic.normalize_booking_source()
を再利用する(この関数はOTA表示名の整形のみを行う純粋関数であり、売上フィールドを一切
参照しない。指示により再実装せずimportして使う)。
"""
from __future__ import annotations

from typing import Dict, Optional, Tuple

from .. import config
from ..accounting.beds24_revenue_logic import ONSITE_PAYMENT_TOKENS, normalize_booking_source
from .schema import StaffAddress, StaffBookingRecord

# None/null/undefined/N/A相当の文字列表現（大小文字無視で比較する）。
_SANITIZE_LITERALS = {"none", "null", "undefined", "n/a"}

# Beds24 unitId等の候補フィールド（防御的探索）。実データ確認の結果、これは
# 「客室タイプ内の1始まり連番」であり、実物理客室番号(401等)そのものではない
# ことが判明した(2026-08-30)。実物理客室番号への変換は
# config/kiraku_room_unit_mapping.yml 経由でのみ行う（推測しない）。
_UNIT_ID_KEYS = ("unitId", "roomNumber", "subRoomId", "unit")


def _sanitize_str(v) -> Optional[str]:
    """None・空文字・空白のみ・'None'/'null'/'undefined'/'N/A'相当の文字列表現をNoneへ丸める。"""
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    if s.lower() in _SANITIZE_LITERALS:
        return None
    return s


def _first_present(raw: dict, *keys) -> Optional[str]:
    """先頭から見て最初に非空(サニタイズ後non-None)の値を返す。"""
    for k in keys:
        v = _sanitize_str(raw.get(k))
        if v is not None:
            return v
    return None


def load_room_type_config() -> Dict:
    return config.load_yaml("kiraku_room_types.yml").get("room_types", {})


def _room_id_lookup(room_type_config: Dict) -> Dict[str, str]:
    """room_type_metrics.classify_room_type()と同じroom_id -> typeキー照合ロジックの
    最小限のローカル複製。room_type_metrics.pyはaccounting/beds24_revenue_logic.pyを
    モジュールレベルでimportしており(revenue計算の内部関数を使うため)、ops/パッケージを
    会計コードから完全に切り離すため、ここではroom_id -> typeの照合部分だけを複製する
    (revenue計算部分は一切持ち込まない)。設定ファイルは同じconfig/kiraku_room_types.ymlを読む。
    """
    lookup: Dict[str, str] = {}
    for key, spec in room_type_config.items():
        for rid in (spec.get("match", {}) or {}).get("room_ids") or []:
            lookup[str(rid)] = key
    return lookup


def classify_room_type_key(room_id, room_type_config: Dict) -> str:
    lookup = _room_id_lookup(room_type_config)
    return lookup.get(str(room_id or ""), "unknown")


def _extract_guest_name(raw: dict) -> str:
    """api/beds24_client.normalize_booking()と同じフォールバック順で氏名を組み立てる
    (guest_name優先、無ければfirstName+lastNameを連結)。"""
    guest = _sanitize_str(raw.get("guest_name"))
    if guest:
        return guest
    first = _sanitize_str(raw.get("firstName")) or ""
    last = _sanitize_str(raw.get("lastName")) or ""
    return " ".join(x for x in [first, last] if x).strip()


def _extract_guests(raw: dict) -> Tuple[int, int]:
    """numAdult/numChild を int(float(x or 0)) で整数化する
    (api/beds24_client.normalize_booking()と同じ堅牢化。文字列/None/浮動小数でも壊れない)。
    """
    adults = int(float(raw.get("numAdult", 0) or 0))
    children = int(float(raw.get("numChild", 0) or 0))
    return adults, children


def _extract_phone(raw: dict) -> Optional[str]:
    return _first_present(raw, "phone", "mobile")


def _extract_notes(raw: dict) -> Optional[str]:
    return _first_present(raw, "notes", "comments", "groupNote", "message")


def _extract_address(raw: dict) -> StaffAddress:
    """【要実データ確認・判断コール】Beds24のJP住所は 'state'=都道府県、'city'=市区町村、
    'address'=残りの番地等自由記述、という一般的な国際PMS連携の慣例を仮定してマッピングする。
    この state->prefecture 対応はこのリポジトリ内に実証済みの根拠(テスト・調査ログ)が無い
    推測マッピングであり、実データで別途確認が必要(依頼元へ要フラグ)。
    """
    return StaffAddress(
        postcode=_sanitize_str(raw.get("postcode")),
        prefecture=_sanitize_str(raw.get("state")),
        city=_sanitize_str(raw.get("city")),
        rest=_sanitize_str(raw.get("address")),
    )


def _extract_arrival_time(raw: dict) -> Optional[str]:
    """到着予定時刻フィールドの防御的探索('arriv'+'time'/'eta'/'hour'を含むキー、大小文字無視)。

    このリポジトリのnormalize_booking()/beds24_field_probe.pyのいずれにも到着予定時刻
    フィールドの実証記録は無く、実データにも現時点(2026-08-30)で存在しない。将来出現した
    場合にだけ自動的に拾えるよう防御的に実装するのみで、無ければNoneを返す。
    """
    for k, v in raw.items():
        kl = str(k).lower()
        if "arriv" in kl and any(tok in kl for tok in ("time", "eta", "hour")):
            sv = _sanitize_str(v)
            if sv is not None:
                return sv
    return None


def _extract_unit_index(raw: dict) -> Optional[str]:
    """Beds24 unitId等(客室タイプ内の1始まり連番)の防御的探索。実物理客室番号ではない。"""
    for k in _UNIT_ID_KEYS:
        v = _sanitize_str(raw.get(k))
        if v is not None:
            return v
    return None


def load_room_unit_mapping() -> Dict[str, Dict[str, str]]:
    return config.load_yaml("kiraku_room_unit_mapping.yml").get("room_types", {}) or {}


def resolve_physical_room_number(room_type_key: str, unit_index: Optional[str],
                                  room_unit_mapping: Dict) -> Optional[str]:
    """(room_type_key, unit_index) -> 実物理客室番号(config/kiraku_room_unit_mapping.yml経由)。

    マッピングが未確定/対象キーが無い場合はNoneを返す(推測で埋めない。
    呼び出し側はNoneをUNASSIGNED相当として扱うこと)。
    """
    if unit_index is None:
        return None
    type_map = (room_unit_mapping or {}).get(room_type_key) or {}
    return type_map.get(str(unit_index))


def extract_guest_notice(raw: dict) -> Optional[str]:
    """ゲスト自身が入力した「お客様からのお知らせ」を抽出する。

    ソースは guestComments のみ(2026-09実データ調査で確認済み: Beds24標準の
    ゲスト入力コメントfield。internal note/staff note/groupNote/system message等の
    内部運用メモとは完全に別fieldであり、混在させない)。_extract_notes()が集める
    notes/comments/groupNote/messageは内部メモ用途であり、清掃指示のguest_noticeへは
    絶対に流用しない(意図的に別関数として独立させている)。
    """
    return _sanitize_str(raw.get("guestComments"))


def extract_children_age_7plus(raw: dict) -> Tuple[Optional[int], bool]:
    """7歳以上の子供人数を抽出する。

    2026-09、property 330695、直近9か月657予約（Booking.com+子供ありの実予約10件を
    個別調査）の実データ調査結果: 予約payload・infoItems・guestCommentsのいずれにも
    子供の年齢を示すfieldは一切存在しなかった(全キーを再帰的に走査して'age'/'child'を
    含むキーを確認したが、'age'ヒットは'apiMessage'/'message'の部分一致のみで年齢データ
    ではなく、'child'ヒットは合計人数のnumChildのみ)。よって現時点では常に
    (None, False)を返す(推測しない)。将来Beds24側に年齢fieldが追加された場合のみ、
    ここを更新する。
    """
    return None, False


def _has_onsite_marker(item: dict) -> bool:
    desc = str(item.get("description", "") or "").lower()
    return any(tok.lower() in desc for tok in ONSITE_PAYMENT_TOKENS)


def extract_onsite_payment(raw: dict) -> Tuple[bool, Optional[int]]:
    """現地決済が必要か、現地で回収すべき残額はいくらかを判定する。

    ONSITE_PAYMENT_TOKENS(accounting/beds24_revenue_logic.py、既存の売上ロジックで
    実証済みのトークン一覧)を再利用し、新しい判定器を独自に作らない。

    2026-09、property 330695、直近9か月657予約の実データ調査結果: 現地決済markerを
    持つ予約は2件のみ(いずれも非キャンセル)。両方とも「現地支払い」invoiceItemが
    type=payment/lineTotal=0のマーカー行として存在し、他のpayment行は一切無く、
    charge合計(=price)がそのまま未収残高だった(13,000円/14,500円で実証)。
    トップレベルにbalance/paid/due相当のfieldも存在しなかった。よって:

        outstanding = sum(type=charge の lineTotal) - sum(marker以外のtype=payment の |lineTotal|)

    を残高とする。marker以外に実際の支払い行があるケース(一部前払い等)は実データに
    存在しなかったため未実証だが、その金額の符号(絶対値として扱う)は既存の
    extract_beds24_coupon_discount()等と同じ確立済み規約を踏襲しており、新規に
    推測したものではない。

    表示条件(呼び出し側で使う想定): 明示的なonsite signalがあり、かつ
    outstanding > 0 の場合のみ (True, outstanding) を返す。signalが無ければ
    (False, None)。signalはあるが残高が0以下なら (False, None)(現地決済表示しない)。
    """
    if not raw:
        return False, None
    items = raw.get("invoiceItems") or []
    onsite_items = [it for it in items if _has_onsite_marker(it)]
    if not onsite_items:
        return False, None

    charge_sum = sum(float(it.get("lineTotal", it.get("amount", 0)) or 0)
                     for it in items if it.get("type") == "charge")
    payment_sum_excl_marker = sum(
        abs(float(it.get("lineTotal", it.get("amount", 0)) or 0))
        for it in items if it.get("type") == "payment" and it not in onsite_items)
    outstanding = round(charge_sum - payment_sum_excl_marker)

    if outstanding <= 0:
        return False, None
    return True, outstanding


def extract_cleaning_extra(raw: dict) -> Dict:
    """清掃指示DTO専用の追加項目(guest_notice/children_age_7plus_count/
    children_age_data_available/onsite_payment_required/onsite_payment_amount)を
    まとめて抽出する。StaffBookingRecord(Daily Ops/宿泊者名簿でも使う共有dataclass)
    には一切追加しない — 財務フィールド(onsite_payment_amount)がDaily Ops側の
    出力へ意図せず混入するのを構造的に防ぐため、Cleaning DTO専用の別経路として
    独立させている。
    """
    children_age_7plus_count, children_age_data_available = extract_children_age_7plus(raw)
    onsite_payment_required, onsite_payment_amount = extract_onsite_payment(raw)
    return {
        "guest_notice": extract_guest_notice(raw),
        "children_age_7plus_count": children_age_7plus_count,
        "children_age_data_available": children_age_data_available,
        "onsite_payment_required": onsite_payment_required,
        "onsite_payment_amount": onsite_payment_amount,
    }


def extract_staff_booking(raw: dict, room_types_config: Dict,
                          room_unit_mapping: Optional[Dict] = None) -> StaffBookingRecord:
    """raw Beds24 予約dict -> StaffBookingRecord（許可リストの明示pick/mapのみ）。

    OTA判定の生値候補順は既存 api/beds24_client.normalize_booking() の channel 抽出
    ("refererEditable" -> "channel" -> "apiSource" -> "referer" -> "source") と揃える。
    """
    source_value = (raw.get("refererEditable") or raw.get("channel") or raw.get("apiSource")
                    or raw.get("referer") or raw.get("source"))
    ota_name, booking_source_raw = normalize_booking_source(source_value)

    room_id = raw.get("roomId")
    room_type_key = classify_room_type_key(room_id, room_types_config)
    room_type_label = room_types_config.get(room_type_key, {}).get("label", room_type_key)

    unit_index = _extract_unit_index(raw)
    room_number = resolve_physical_room_number(room_type_key, unit_index, room_unit_mapping or {})

    adults, children = _extract_guests(raw)

    return StaffBookingRecord(
        booking_id=str(raw.get("id") if raw.get("id") is not None else (raw.get("booking_id") or "")),
        guest_name=_extract_guest_name(raw),
        ota_name=ota_name,
        booking_source_raw=booking_source_raw,
        room_type_key=room_type_key,
        room_type_label=room_type_label,
        room_number=room_number,
        adults=adults,
        children=children,
        total_guests=adults + children,
        checkin_date=str(raw.get("arrival") or "")[:10],
        checkout_date=str(raw.get("departure") or "")[:10],
        arrival_time=_extract_arrival_time(raw),
        phone=_extract_phone(raw),
        notes=_extract_notes(raw),
        address=_extract_address(raw),
        status=str(raw.get("status") if raw.get("status") is not None else ""),
    )
