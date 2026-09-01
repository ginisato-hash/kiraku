"""raw Beds24 予約dict -> StaffBookingRecord（許可リストの明示pick/mapのみ）。

BookingRecord(normalize/schema.py)やbeds24_client.normalize_booking()は一切importせず、
raw dictから直接抽出する。OTA正規化のみ accounting/beds24_revenue_logic.normalize_booking_source()
を再利用する(この関数はOTA表示名の整形のみを行う純粋関数であり、売上フィールドを一切
参照しない。指示により再実装せずimportして使う)。
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

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


# Booking.comの子供年齢表記("1 child aged 10"等)を抽出するpattern。
#
# 【重要・実データ未検証】2026-09、property 330695の実データ調査(3回、9か月分
# 664予約、Booking.com+子供ありの実予約10件全件)では、guestCommentsが全件
# 空文字列(長さ0)であり、このpatternはおろか"aged"という単語自体が
# guestComments/infoItems/その他全field(id/氏名等PII除く)のどこにも一件も
# 出現しなかった。ユーザーからは「実確認済み」との説明を受けたが、本リポジトリが
# アクセスできる実Beds24データではこの調査結果どおり一切確認できていない
# (この矛盾はレポートで明示報告済み)。
# よってこの実装は、ユーザーが提示した具体例("1 child aged 10"、複数児童は
# "N children aged A, B, ...")どおりに構築したものであり、実データによる
# 検証は現時点でできていない。一致する実データが将来現れれば自動的に
# 有効化される安全な設計とし、一致しない限り(=現状の全実予約を含む)
# children_age_data_availableは常にFalseのまま、既存の安全なfallback
# (子供全員を布団人数に含める)へ通常どおり流れる — 誤動作のリスクはない。
_CHILD_AGE_PATTERN = re.compile(
    r"\d+\s+child(?:ren)?\s+aged\s+\d+(?:\s*(?:,|and)\s*\d+)*",
    re.IGNORECASE,
)


def parse_booking_com_child_ages(guest_comments) -> List[int]:
    """guestCommentsからBooking.comの子供年齢を抽出する(_CHILD_AGE_PATTERN参照。
    実データ未検証の防御的実装)。一致しなければ空listを返す(推測しない)。
    numChildより少ない年齢しか見つからない場合(一部の子供の年齢が不明)でも、
    確認できた分だけをそのまま返す — 残りを7歳以上/未満どちらにも推測で
    埋めない(要件6)。
    """
    if not guest_comments:
        return []
    m = _CHILD_AGE_PATTERN.search(str(guest_comments))
    if not m:
        return []
    ages_part = m.group(0).split("aged", 1)[-1]
    return [int(a) for a in re.findall(r"\d+", ages_part)]


def _strip_child_age_metadata(text: str) -> str:
    """guest_noticeからBooking.comのchild-age system metadata部分だけを除去する。
    実データにこのpatternの出現例が無いため未検証(_CHILD_AGE_PATTERN参照)。
    一致しない場合は元の文字列をそのまま返す(人間が書いた文章を誤って
    削除しない)。
    """
    return _CHILD_AGE_PATTERN.sub("", text).strip()


def extract_guest_notice(raw: dict) -> Optional[str]:
    """ゲスト自身が入力した「お客様からのお知らせ」を抽出する。

    ソースは guestComments のみ(2026-09実データ調査で確認済み: Beds24標準の
    ゲスト入力コメントfield。internal note/staff note/groupNote/message等の
    内部運用メモとは完全に別fieldであり、混在させない)。_extract_notes()が集める
    notes/comments/groupNote/messageは内部メモ用途であり、清掃指示のguest_noticeへは
    絶対に流用しない(意図的に別関数として独立させている)。
    Booking.comのchild-age system metadata(_CHILD_AGE_PATTERN)が同じ
    guestComments内に混在する場合は、それを除去した残りのテキストだけを返す
    (要件13。実データでの出現例は無く未検証 — 一致しなければ何も変更されない)。
    """
    raw_comments = _sanitize_str(raw.get("guestComments"))
    if raw_comments is None:
        return None
    stripped = _strip_child_age_metadata(raw_comments)
    return stripped or None


def extract_children_age_7plus(raw: dict) -> Tuple[Optional[int], bool]:
    """7歳以上の子供人数を抽出する(parse_booking_com_child_ages参照。実データ
    未検証の防御的実装 — 現状の全実予約ではguestCommentsが空のため常に
    (None, False)を返し、既存の安全なfallbackへ通常どおり流れる)。
    """
    ages = parse_booking_com_child_ages(raw.get("guestComments") if raw else None)
    if not ages:
        return None, False
    return sum(1 for age in ages if age >= 7), True


def compute_bedding_guest_count(ota_name: str, adults: int, children: int,
                                children_age_7plus_count: Optional[int],
                                children_age_data_available: bool) -> int:
    """清掃スタッフが布団を用意すべき人数(要件2・10)。

    Booking.comのみ、確認できた子供の年齢が7歳以上の場合だけ布団人数へ加算する。
    年齢が一部/全く確認できない場合は、推測せず子供全員を布団人数に含める
    安全側のfallback(布団不足より多めのほうが運用上安全なため — 前回要件の
    「取得不能なら通常の子供総数へfallback」を踏襲)。楽天/じゃらん/Direct等
    その他は年齢を一切参照せず常にadults+children(要件7-8、既存の安全な
    fallbackを壊さない — OTA判定は既存のnormalize_booking_source()由来の
    canonical nameをそのまま使い、新しい文字列判定ロジックを重複実装しない)。
    """
    if ota_name == "Booking.com" and children_age_data_available:
        return adults + (children_age_7plus_count or 0)
    return adults + children


# Beds24公式Info Code(予約payload内のinfoItems[].code)のうち、OTAチャネル自身が
# 顧客から代金を回収済み/回収するため「喜らくフロントでは回収しない」ことを示す
# ものだけを列挙する。2026-09、property 330695・5か月分の実データ調査で
# BOOKINGCOMBANKTRANS(154件)のみ実在を確認済み。他は同ユーザー要件で明示された
# Beds24公式のchannel-collect系Info Codeを防御的に含めるが、この物件の実データには
# 一度も出現していない(将来出現した場合に備えるのみで、推測で追加した挙動はしない
# — あくまでコード名の一致判定のみで金額計算には一切関与しない)。
# HOTELCOLLECTはExpediaのhotel collect(施設側が回収する)を意味し、OTA collectでは
# ないため意図的に含めない(誤って除外シグナルにしないこと — 要件A-4)。
CHANNEL_COLLECT_INFO_CODES = frozenset({
    "BOOKINGCOMBANKTRANS",  # 実データで確認済み(154件)
    "BOOKINGCOMVIRTCARD", "EXPEDIACOLLECT", "AGODACOLLECT", "VIRTUALCARD",  # 未実証・防御的
})


def _is_channel_collect(raw: dict) -> bool:
    for item in raw.get("infoItems") or []:
        code = str(item.get("code") or "").upper()
        if code in CHANNEL_COLLECT_INFO_CODES:
            return True
    return False


def extract_invoice_balance(raw: dict) -> int:
    """Beds24公式のbooking単位 Invoice Balance([INVOICEBALANCE1]相当)を計算する。

    Beds24 API v2にはbooking-level balanceを直接返すfieldが存在しない
    (2026-09、GET /bookings/invoicesは実データで0件、GET /bookingsのpayloadにも
    balance/due/owing相当のtop-level fieldは一切無いことを実証済み)。よって
    invoiceItemsから計算するが、符号は必ず実データで確認済みの規約を使う:

        invoice_balance = sum(type=charge の lineTotal) + sum(type=payment の lineTotal)

    (payment側は絶対値化しない — lineTotalは既に実額入金時は負数で入っている。
    2026-09、11件の実予約(現地支払いmarker/BankTransfer/coupon+point+事前払いの
    組み合わせ/無支払いのBooking.com等)で全件この式がBeds24の実態と一致することを
    確認済み。以前のバージョンはpayment側をabs()していたが、これは支払い済み予約の
    残高を「charge+|payment|」という誤った加算にしてしまう実害があった
    (例: BankTransferで全額入金済みの予約が誤って残高2倍表示になっていた)。
    """
    if not raw:
        return 0
    items = raw.get("invoiceItems") or []
    charge_sum = sum(float(it.get("lineTotal", it.get("amount", 0)) or 0)
                     for it in items if it.get("type") == "charge")
    payment_sum = sum(float(it.get("lineTotal", it.get("amount", 0)) or 0)
                      for it in items if it.get("type") == "payment")
    return round(charge_sum + payment_sum)


def extract_amount_due_at_property(raw: dict) -> Tuple[bool, Optional[int]]:
    """その予約について、喜らくフロントが現地でゲストから回収すべき残額を判定する。

    ルール(要件A-8、実データで検証済み):
        invoice_balance <= 0        -> 現地決済なし(支払い済み/charge無し)
        channel collectが明確       -> 現地決済なし(OTAが既に回収/回収予定)
        それ以外でinvoice_balance>0 -> その金額が現地回収額

    「現地支払い」等のmarker(旧ONSITE_PAYMENT_TOKENS)はsource of truthではない
    (要件A-9で補助signalへ降格)。現時点ではこの関数はそれを一切参照しない
    — 参照すると「markerが無い実予約」を誤って除外していた旧バグを再発するため。
    """
    if not raw:
        return False, None
    balance = extract_invoice_balance(raw)
    if balance <= 0:
        return False, None
    if _is_channel_collect(raw):
        return False, None
    return True, balance


def extract_cleaning_extra(raw: dict) -> Dict:
    """清掃指示DTO専用の追加項目(guest_notice/children_age_7plus_count/
    children_age_data_available/bedding_guest_count/payment_due_at_property/
    amount_due_at_property)をまとめて抽出する。StaffBookingRecord(Daily Ops/
    宿泊者名簿でも使う共有dataclass)には一切追加しない — 財務フィールド
    (amount_due_at_property)がDaily Ops側の出力へ意図せず混入するのを構造的に
    防ぐため、Cleaning DTO専用の別経路として独立させている。
    """
    children_age_7plus_count, children_age_data_available = extract_children_age_7plus(raw)
    payment_due_at_property, amount_due_at_property = extract_amount_due_at_property(raw)
    adults, children = _extract_guests(raw)
    source_value = (raw.get("refererEditable") or raw.get("channel") or raw.get("apiSource")
                    or raw.get("referer") or raw.get("source"))
    ota_name, _ = normalize_booking_source(source_value)
    bedding_guest_count = compute_bedding_guest_count(
        ota_name, adults, children, children_age_7plus_count, children_age_data_available)
    return {
        "guest_notice": extract_guest_notice(raw),
        "children_age_7plus_count": children_age_7plus_count,
        "children_age_data_available": children_age_data_available,
        "bedding_guest_count": bedding_guest_count,
        "payment_due_at_property": payment_due_at_property,
        "amount_due_at_property": amount_due_at_property,
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
