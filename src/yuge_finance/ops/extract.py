"""raw Beds24 予約dict -> StaffBookingRecord（許可リストの明示pick/mapのみ）。

BookingRecord(normalize/schema.py)やbeds24_client.normalize_booking()は一切importせず、
raw dictから直接抽出する。OTA正規化のみ accounting/beds24_revenue_logic.normalize_booking_source()
を再利用する(この関数はOTA表示名の整形のみを行う純粋関数であり、売上フィールドを一切
参照しない。指示により再実装せずimportして使う)。
"""
from __future__ import annotations

from typing import Dict, Optional, Tuple

from .. import config
from ..accounting.beds24_revenue_logic import normalize_booking_source
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
