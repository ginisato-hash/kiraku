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


# ゲスト入力コメントの実フィールド名は Beds24 v2 の `comments`。
#
# 【2026-09-02 実データで確定】anchor booking 91673623(Booking.com, numAdult=2,
# numChild=1)をBeds24 v2 APIから直接GETし、Beds24 UI「ゲストからのコメント」に
# 表示されている "1 child aged 10" が booking record の `comments`(長さ142)に
# 実在することを確認した。include parameterは一切不要(plain GET /bookings?id=…で
# 取得可能)で、booking group/master経由でもない(masterId=None/bookingGroup=None)。
# 通常の15分refresh経路(Beds24Client.fetch_raw)が返すrecordにも同じ`comments`が
# 含まれることを同じ実行内で確認済み — 取得経路の変更は不要。
#
# 【前実装のバグ】この直前まで参照していた `guestComments` はBeds24 v2 payloadに
# 存在しないキーであり(実recordの全71キーに無い)、raw.get("guestComments")は常に
# Noneを返していた。「guestCommentsは全件空」という前回の調査結論は、存在しない
# キーの長さを測っていたにすぎない。前回の全field走査も
# beds24_field_probe.PII_KEYS(comments/notes/message/groupNoteを含む)をskip-listに
# 流用していたため、値が入っている当の`comments`だけを走査対象から外していた。
GUEST_COMMENT_FIELD = "comments"

# Booking.comの子供年齢metadata。
#
# 【2026-09-02 実データで確定】property 330695・2026-01〜2026-12・723予約
# (`comments`非空354件)を走査した結果、実在する表記は "N child aged N" の1形
# のみ(7件/6予約、digitsをマスクしたtemplateは '# child aged #' の1種類だけ)。
# 子供が2名の予約(92008803, numChild=2)は "1 child aged 6" と "1 child aged 8" の
# 2行に分かれて出現する — 「N children aged A, B」というカンマ区切り形式は実データに
# 一度も存在しない(前実装はこの推測形式を前提に単一matchしか見ていなかったため、
# 複数児童の年齢を取りこぼす実バグがあった)。よってここでは finditer で全出現を
# 収集する。楽天トラベル(12予約)・じゃらんnet(24予約)の子供あり予約には年齢表記は
# 一件も無い(=国内OTAは年齢を見ない既存ルールと整合)。
_CHILD_AGE_PATTERN = re.compile(
    r"\d+\s+child(?:ren)?\s+aged\s+(\d+)",
    re.IGNORECASE,
)

# OTA(主にBooking.com)が`comments`へ自動生成する定型行。ゲスト自身が書いた
# 「お知らせ」ではないため、清掃指示書のguest_noticeへは出さない(要件15)。
# 2026-09-02、実データで「2予約以上に byte一致で出現する行template」だけを
# 根拠として採用した(人間が入力した文章が別々の予約間で完全一致することは
# 無いため、繰り返し出現 = 機械生成の証拠になる)。推測で語を増やさないこと。
_SYSTEM_LINE_EXACT = {
    "** this reservation has been pre-paid **",   # 実データ176予約
    "non smoking requested",                      # 実データ139予約
    "こちらは「スマート・フレックス予約」の対象予約です。",   # 実データ5予約
}
_SYSTEM_LINE_PREFIXES = (
    "booking note",                # "BOOKING NOTE : Payment charge is JPY …" 実データ141予約
    "approximate time of arrival:",  # Booking.com自動生成の到着時間帯 実データ46予約
    "booked rate:",                # "booked rate: Non-refundable Rate (…)" 実データ41+18+2予約
    "reservation has a cancellation grace period",  # 実データ26予約
    "bed preference:",             # "BED PREFERENCE:… futon mats" 実データ8+2予約
    "アップグレード後のポリシー：",    # 実データ5予約
    "company:",                    # OTAの請求先会社名(定型field) 実データ2予約
)
# Booking.com管理画面へのリンク行(実データ5予約)。
_SYSTEM_LINE_CONTAINS = ("admin.booking.com",)

# Booking.comの部屋設定コードだけで構成される行(実データ: "NonSmoke" 9予約、
# "LargeBed, NonSmoke" 4予約、"NonSmoke, TwinBeds" 3予約)。カンマ区切りの
# token全てがこの集合に含まれる行だけを落とす(1つでも未知のtokenがあれば
# ゲスト入力が混ざっている可能性があるため落とさない)。
_ROOM_PREFERENCE_TOKENS = {
    "nonsmoke", "smoke", "nonsmoking", "largebed", "twinbeds", "quietroom",
    "singlebed", "doublebed",
}
# "NonSmoke, QuietRoom AdditionalNotes: <ゲストが書いた本文>"(実データ2予約)の形。
# AdditionalNotes: 以降だけがゲスト入力なので、そこから後ろを残す。
_ADDITIONAL_NOTES_RE = re.compile(r"additional\s*notes\s*:", re.IGNORECASE)
# 楽天トラベル/じゃらんの室料prefix "[室料:12000円＝12000円]"(実データ計約110予約)。
# prefixだけを外し、後続のゲスト記述(「禁煙かつ静かな部屋希望します。」等)は残す。
_ROOM_CHARGE_PREFIX_RE = re.compile(r"^\[室料[^\]]*\]")


def guest_comment_text(raw: dict) -> Optional[str]:
    """Beds24 booking recordからゲスト入力コメント原文を取り出す(GUEST_COMMENT_FIELD)。"""
    if not raw:
        return None
    return _sanitize_str(raw.get(GUEST_COMMENT_FIELD))


def parse_booking_com_child_ages(guest_comments) -> List[int]:
    """`comments`からBooking.comの子供年齢を全件抽出する(_CHILD_AGE_PATTERN参照)。

    実データは1行に1名ずつ("1 child aged 6" / "1 child aged 8")現れるため、
    finditerで全出現を収集する。一致しなければ空listを返す(推測しない)。
    numChildより少ない年齢しか取得できない場合でも、確認できた分だけを返す —
    残りを7歳以上/未満どちらにも推測で埋めない(要件16)。
    """
    if not guest_comments:
        return []
    return [int(m.group(1)) for m in _CHILD_AGE_PATTERN.finditer(str(guest_comments))]


def _strip_child_age_metadata(text: str) -> str:
    """guest_noticeからBooking.comのchild-age system metadata部分だけを除去する。
    一致しない場合は元の文字列をそのまま返す(人間が書いた文章を誤って削除しない)。
    """
    return _CHILD_AGE_PATTERN.sub("", text).strip()


def _is_room_preference_only(line: str) -> bool:
    tokens = [t.strip().lower() for t in line.split(",")]
    return bool(tokens) and all(t in _ROOM_PREFERENCE_TOKENS for t in tokens if t)


def _clean_guest_notice_line(line: str) -> Optional[str]:
    """`comments`の1行を、ゲスト入力部分だけへ整える。system行はNoneを返す。"""
    s = _ROOM_CHARGE_PREFIX_RE.sub("", line).strip()
    if not s:
        return None
    s = _strip_child_age_metadata(s)
    if not s:
        return None
    m = _ADDITIONAL_NOTES_RE.search(s)
    if m:
        # 前半(部屋設定コード)は捨て、ゲストが書いた本文だけを残す。
        s = s[m.end():].strip()
        return s or None
    low = s.lower()
    if low in _SYSTEM_LINE_EXACT:
        return None
    if low.startswith(_SYSTEM_LINE_PREFIXES):
        return None
    if any(tok in low for tok in _SYSTEM_LINE_CONTAINS):
        return None
    if _is_room_preference_only(s):
        return None
    return s


def extract_guest_notice(raw: dict) -> Optional[str]:
    """ゲスト自身が入力した「お客様からのお知らせ」を抽出する。

    ソースはBeds24 v2の`comments`(GUEST_COMMENT_FIELD)。この1フィールドに
    「ゲストが書いた文章」と「OTAが自動生成した定型文/child-age metadata」が
    混在するため、行単位で後者だけを取り除いた残りを返す(要件13・15)。
    残りが無ければNone(「客:」行自体を出さない)。internal note(notes/groupNote/
    message)は_extract_notes()の担当であり、guest_noticeへは絶対に流用しない。
    """
    raw_comments = guest_comment_text(raw)
    if raw_comments is None:
        return None
    kept = []
    for line in raw_comments.splitlines():
        cleaned = _clean_guest_notice_line(line)
        if cleaned:
            kept.append(cleaned)
    return "\n".join(kept) or None


def extract_child_age_info(raw: dict) -> Tuple[int, int]:
    """(年齢が判明した子供の人数, そのうち7歳以上の人数)を返す。"""
    ages = parse_booking_com_child_ages(guest_comment_text(raw) if raw else None)
    return len(ages), sum(1 for age in ages if age >= 7)


def extract_children_age_7plus(raw: dict) -> Tuple[Optional[int], bool]:
    """7歳以上の子供人数と、年齢データが取得できたかどうかを返す。

    年齢が一件も取れなければ(None, False)を返し、既存の安全なfallbackへ流す。
    """
    known_count, age_7plus = extract_child_age_info(raw)
    if not known_count:
        return None, False
    return age_7plus, True


def compute_bedding_guest_count(ota_name: str, adults: int, children: int,
                                children_age_7plus_count: Optional[int],
                                children_age_data_available: bool,
                                children_age_known_count: Optional[int] = None) -> int:
    """清掃スタッフが布団を用意すべき人数(要件2・10・12・16)。

    Booking.comのみ、年齢が判明している子供については7歳以上だけを加算する
    (0〜6歳は布団人数に含めない)。年齢が判明していない子供は推測せず、そのまま
    布団人数に含める安全側の扱いとする(布団不足を避けるため)。年齢が一件も
    取得できない場合は従来どおりadults+children。
    楽天トラベル/じゃらん/Direct等はそもそも年齢を参照せず常にadults+children
    (要件13、既存の安全なfallbackを壊さない。OTA判定は既存の
    normalize_booking_source()由来のcanonical nameをそのまま使う)。

    children_age_known_count: 年齢が判明した子供の人数。省略時は「全員分の年齢が
    判明している」とみなす(従来の呼び出し互換)。
    """
    if ota_name != "Booking.com" or not children_age_data_available:
        return adults + children
    known = children if children_age_known_count is None else children_age_known_count
    unknown_age_children = max(children - known, 0)
    return adults + (children_age_7plus_count or 0) + unknown_age_children


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
    children_age_known_count, _age_7plus = extract_child_age_info(raw)
    children_age_7plus_count, children_age_data_available = extract_children_age_7plus(raw)
    payment_due_at_property, amount_due_at_property = extract_amount_due_at_property(raw)
    adults, children = _extract_guests(raw)
    source_value = (raw.get("refererEditable") or raw.get("channel") or raw.get("apiSource")
                    or raw.get("referer") or raw.get("source"))
    ota_name, _ = normalize_booking_source(source_value)
    bedding_guest_count = compute_bedding_guest_count(
        ota_name, adults, children, children_age_7plus_count, children_age_data_available,
        children_age_known_count)
    return {
        "guest_notice": extract_guest_notice(raw),
        "children_age_7plus_count": children_age_7plus_count,
        "children_age_known_count": children_age_known_count,
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
