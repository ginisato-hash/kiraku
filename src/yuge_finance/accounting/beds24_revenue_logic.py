"""Beds24 速報売上ロジック v3（point加算・coupon直割引の明確化）。

Phase 0調査の結論（喜らく実データ187件で実証・推測ではない）:
  - キャンセル判定: raw `status` フィールドが `cancelTime` の有無と100%相関しており、
    キャンセル判定に信頼できる（cancelled 26件、全件cancelTime有り。非cancelledでの
    cancelTime有りは0件）。
  - coupon（直割引・売上加算しない）: invoiceItems上の "coupon" はすべて type="payment"
    （決済手段の一つ。事前払い/BankTransfer/pointと同列）であり、type="charge"
    （室料本体）には一切出現しない（0件）。price（室料charge）はクーポン精算の有無に
    関わらず既に室料全額を反映しているため、couponは売上に加算せず「直割引」として
    beds24_coupon_discount_amount に金額のみ保持する。
  - point（売上加算候補）: 実データでは point もすべて type="payment" としてのみ出現し
    （47件）、type="charge" には一切出現しない。point を含む予約47件のうち47件で
    charge合計=price（pointの有無に関わらずroom chargeが既に全額計上済み）。
    よって現状 beds24_point_revenue_included は0固定
    （status=point_already_included_in_price）とし、二重計上を避ける。
    将来 type="charge" にpoint起因の追加行（施設側が別途受け取る収入）が見つかった
    場合のみ、extract_beds24_point_revenue() が自動的にそれを収入として拾う設計。
  - 現地決済/現地払い（売上加算候補。2026-05〜2026-11 実データ226件で実証）: "現地支払い"
    invoiceItemが2件出現。いずれも type="payment"（決済手段。lineTotal=0）であり、
    room chargeはtype="charge"として既にbooking.priceに全額計上済み（price一致）。
    type="charge"として現地決済起因の別行が見つかった場合のみ加算候補とする設計
    （extract_beds24_onsite_payment_revenue()）。他の探索語（cash/onsite/pay at property等）
    は実データ上に一件も出現しなかった。

本ロジックはキャンセル除外を既存(revenue_recon)の集計と同じ判定基準に揃えることで
二重控除を避け、point加算・coupon直割引・現地決済確認の情報のみを新規に追加する。
"""
from __future__ import annotations

import json
from calendar import monthrange
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

from ..normalize.schema import BookingRecord

REVENUE_LOGIC_VERSION = "beds24_revenue_v5_gross_price_coupon_reference"
# 売上算定基準: price(price=0はcharge fallback済み)そのまま。coupon/point/banktransfer/
# 事前決済/現地決済はすべてpriceの決済チャネル内訳に過ぎず、売上へ別途加算も控除もしない。
# 【2026-07-11 再訂正】v4でcouponを売上控除する仕様にしたが、ユーザーの最終判断により
# 「couponは売上から控除しない。総額(price)をそのまま売上として扱い、couponは月次参考額として
# 別表示するのみ」に戻す。詳細はcalculate_recognized_booking_revenue参照。
REVENUE_BASIS = "price_gross_including_coupon_point_payments"
JST = timezone(timedelta(hours=9))

CANCEL_TOKENS = ("cancelled", "canceled", "キャンセル")

# coupon/discount系（直割引。売上には加算しない。情報表示専用）
COUPON_TOKENS = ("coupon", "voucher", "クーポン", "割引")
# point/reward系（施設収入として加算可能な候補）
POINT_TOKENS = ("point", "points", "reward", "rewards", "loyalty", "楽天ポイント", "ポイント")
# 現地決済/現地払い系（実データで確認できた具体的な表現のみ。cash/due/collect等の
# 汎用語は誤検出リスクが高く実データにも出現しなかったため含めない）
ONSITE_PAYMENT_TOKENS = (
    "現地決済", "現地払い", "現地支払", "施設払い", "宿払い",
    "pay at property", "hotel collect", "onsite payment", "on-site payment", "pay onsite",
)

# 予約経路(OTA)表示名の正規化。実データ確認済みの refererEditable 値
# (じゃらんnet/Booking.com/楽天トラベル/Zaokiraku。2026-07-10、238予約で全件確認)を
# 正としてキー化する。BookingRecord.channel は normalize_booking() で既に
# refererEditable優先(_first(raw,"refererEditable","channel","apiSource","referer",...))
# で設定済みのため、ここではその値を表示名へ整形するだけでよい。
OTA_DISPLAY_NAMES = {
    "じゃらんnet": "じゃらん",
    "jalannet": "じゃらん",
    "jalan": "じゃらん",
    "楽天トラベル": "楽天トラベル",
    "rakuten": "楽天トラベル",
    "楽天": "楽天トラベル",
    "booking.com": "Booking.com",
    "booking": "Booking.com",
    "zaokiraku": "Direct",  # 自社直販ブランドサイト名(実データ確認済み)
    "direct": "Direct",
    "直販": "Direct",
}

ROOM_CHANGE_HISTORY_STATUS_NOT_AVAILABLE = "not_available"


def normalize_booking_source(source_value: Optional[str]):
    """予約経路(OTA)の生値を表示名へ正規化する。戻り値: (display_name, raw_value)。

    未知の値は生値をそのまま表示名として使う(「不明」に丸めない。実データに
    今後別OTAが増えても情報を失わないため)。
    """
    raw = str(source_value).strip() if source_value else ""
    if not raw:
        return "Direct", "Direct"
    display = OTA_DISPLAY_NAMES.get(raw.lower(), raw)
    return display, raw


def extract_room_change_history(booking: "BookingRecord") -> Dict:
    """Beds24の部屋変更履歴(room movement history)を抽出する。

    実データ調査の結論(2026-07-10、229予約・5か月分・includeInfoItems=true含む):
      - roomIdは予約ごとに単一の現在値のみで、予約時点の部屋IDと現在の部屋IDを
        区別できるfieldはBeds24 v2 bookings payloadに存在しない。
      - infoItems(Beds24の予約イベント通知。includeInfoItems=trueで取得可能)にも
        部屋変更を示すcode/textは1件も無かった(実際に出現したcodeはOTA通知
        Jalan/Rakuten、決済通知BOOKINGCOMBANKTRANS、INVALIDEMAIL/CHECKIN/CHECKOUTのみ)。
      - modifiedTimeはbookingTimeとほぼ全予約(236/238件)で異なり、通常の自動処理でも
        更新されるため、部屋変更特有のシグナルとしては使えない(ノイズが多すぎる)。
      よって現状は取得不可(not_available)固定。将来Beds24側に監査ログ相当のfieldや
      別endpointが確認できた場合のみ、この関数を更新する(推測で実装しない)。
    """
    return {
        "status": ROOM_CHANGE_HISTORY_STATUS_NOT_AVAILABLE,
        "original_room_id": None,
        "current_room_id": booking.room_id or None,
        "changes": [],
    }


def is_beds24_cancelled_booking(raw: dict) -> bool:
    """raw Beds24 booking dict からキャンセル判定する。

    判定順:
      1. status フィールドがキャンセル相当の文字列
      2. cancelTime フィールドが存在する（非null/非空）
      3. status文字列にcancel系トークンを含む
      4. いずれも無ければ False（呼び出し側で cancel_status_field_missing を記録する）
    """
    if not raw:
        return False
    status = str(raw.get("status", "") or "").strip().lower()
    if status in CANCEL_TOKENS:
        return True
    if raw.get("cancelTime"):
        return True
    if status and any(tok in status for tok in CANCEL_TOKENS):
        return True
    return False


def has_cancel_signal_fields(raw: dict) -> bool:
    """raw payload にキャンセル判定へ使える field が存在するか（status or cancelTime）。"""
    return bool(raw) and ("status" in raw or "cancelTime" in raw)


def _total_nights(checkin: str, checkout: str) -> int:
    """宿泊期間[checkin, checkout)の総泊数。BookingRecord.stay_nightsに依存しない
    （正規化元で未設定/0のままの場合があるため、日付から直接算出する）。
    """
    if not checkin or not checkout:
        return 0
    try:
        ci = date.fromisoformat(checkin[:10])
        co = date.fromisoformat(checkout[:10])
    except ValueError:
        return 0
    return max((co - ci).days, 0)


def _nights_in_month(checkin: str, checkout: str, month: str) -> int:
    """宿泊期間[checkin, checkout)のうち month(YYYY-MM) に属する泊数。"""
    if not checkin or not checkout:
        return 0
    try:
        ci = date.fromisoformat(checkin[:10])
        co = date.fromisoformat(checkout[:10])
    except ValueError:
        return 0
    y, m = (int(x) for x in month.split("-"))
    month_start = date(y, m, 1)
    month_end_exclusive = date(y, m, monthrange(y, m)[1]) + timedelta(days=1)
    overlap_start = max(ci, month_start)
    overlap_end = min(co, month_end_exclusive)
    return max((overlap_end - overlap_start).days, 0)


def _prorate_to_month(amount: float, checkin: str, checkout: str, month: str) -> float:
    """宿泊月按分。予約の全泊数のうち対象月に属する泊数の割合で按分する。"""
    if amount == 0 or not checkin or not checkout:
        return amount
    try:
        ci = date.fromisoformat(checkin[:10])
        co = date.fromisoformat(checkout[:10])
    except ValueError:
        return amount
    total_nights = max((co - ci).days, 1)
    target_nights = _nights_in_month(checkin, checkout, month)
    if target_nights >= total_nights:
        return amount
    return amount * target_nights / total_nights


def extract_beds24_point_revenue(raw: dict) -> float:
    """raw Beds24 booking dict から『施設収入として加算できる』point金額を抽出する。

    invoiceItems の type="charge" に point/ポイント/reward 等の説明を持つ行があれば、
    その金額を収入として加算する。type="payment" のpoint(決済手段)は室料charge自体が
    既に全額計上されているため加算しない（二重計上防止）。
    金額がマイナスの場合、施設側入金と確認できないため加算しない。
    """
    if not raw:
        return 0.0
    total = 0.0
    for item in raw.get("invoiceItems") or []:
        if item.get("type") != "charge":
            continue
        desc = str(item.get("description", "") or "").lower()
        if any(tok.lower() in desc for tok in POINT_TOKENS):
            amt = item.get("lineTotal", item.get("amount", 0)) or 0
            if amt > 0:
                total += amt
    return total


def extract_beds24_coupon_discount(raw: dict) -> float:
    """raw Beds24 booking dict からクーポン利用額を抽出する。

    invoiceItems の type="payment" に coupon/クーポン/割引 等の説明を持つ行の金額（絶対値）を返す。

    【2026-07-11 再訂正・確定】一時的に「クーポンは施設実質負担のため売上から減算する」仕様
    (v4)にしたが、ユーザーの最終判断によりv5で撤回。price(=室料charge合計)をそのまま総額として
    売上に計上し、couponはBI上の参考情報(月次合計)としてのみ表示する。売上からは控除しない
    （calculate_recognized_booking_revenue() は現在このamountを使わず、priceをそのまま返す）。
    point/banktransfer/事前決済/現地決済も同様に、priceの決済チャネル内訳に過ぎないため
    売上へ別途加算・控除しない。
    """
    if not raw:
        return 0.0
    total = 0.0
    for item in raw.get("invoiceItems") or []:
        if item.get("type") != "payment":
            continue
        desc = str(item.get("description", "") or "").lower()
        if any(tok.lower() in desc for tok in COUPON_TOKENS):
            amt = item.get("lineTotal", item.get("amount", 0)) or 0
            total += abs(amt)
    return total


def calculate_recognized_booking_revenue(booking: "BookingRecord", raw_index: Dict[str, dict]) -> float:
    """予約1件の施設側認識売上額 = price(price=0はcharge fallback済み)そのまま。

    実データ調査(2026-07-11、予約89646497およびpoint/coupon signalのある181予約中178件)の結論:
    invoiceItems の type="charge"(室料) と type="payment"(point/coupon/事前決済/現地決済等)の
    合計は常に相殺する。つまりpriceは決済チャネル(coupon/point/banktransfer/事前決済/現地決済)を
    問わない室料総額。

    【2026-07-11 v5・ユーザー最終判断で確定】couponを含め、どの決済チャネルも売上へ別途
    加算・控除しない。売上 = price(総額)のみ。couponの月次合計額はBI上の参考情報として
    別途表示する(extract_beds24_coupon_discount)が、この関数の戻り値には反映しない。
    raw_indexは将来の拡張(現状未使用)のために引数として残す。
    """
    return max(booking.gross_revenue, 0.0)


def extract_beds24_coupon_revenue(raw: dict) -> float:
    """DEPRECATED: couponは直割引であり売上には加算しない。常に0.0を返す。

    互換性のため関数は残すが、収入としては扱わない。金額が必要な場合は
    extract_beds24_coupon_discount() を使うこと。
    """
    return 0.0


def _has_onsite_payment_signal(item: dict) -> bool:
    desc = str(item.get("description", "") or "").lower()
    return any(tok.lower() in desc for tok in ONSITE_PAYMENT_TOKENS)


def extract_beds24_onsite_payment_revenue(raw: dict) -> Dict:
    """raw Beds24 booking dict から現地決済/現地払い相当の収入を判定する。

    ルール（推測ではなく実データ実証に基づく）:
      1. 現地決済/現地払い系のinvoiceItemが無ければ field_missing。
      2. type="payment"（決済手段）としてのみ出現する場合、room chargeは既に
         type="charge"としてbooking.priceに全額計上済みのため加算しない
         （payment_method_only_not_revenue）。
      3. type="charge"として現地決済起因の別行がある場合のみ加算候補とする。
         ただし、その行を含めてもcharge合計がbooking.price以内であれば、
         既にprice側に反映済みとみなし加算しない（already_included_in_price）。
      4. charge合計がbooking.priceを上回る場合のみ、その差分相当を加算する
         （added_from_separate_charge）。
      5. 金額が0以下の候補は値引き/返金の可能性があるため加算しない
         （candidate_not_selected）。
    """
    empty = {"candidate_amount": 0.0, "candidate_count": 0, "added_amount": 0.0,
            "added_count": 0, "status": "field_missing"}
    if not raw:
        return empty

    items = raw.get("invoiceItems") or []
    onsite_items = [it for it in items if _has_onsite_payment_signal(it)]
    if not onsite_items:
        return empty

    payment_items = [it for it in onsite_items if it.get("type") == "payment"]
    charge_items = [it for it in onsite_items if it.get("type") == "charge"]

    payment_amount = sum(abs(float(it.get("lineTotal", it.get("amount", 0)) or 0))
                        for it in payment_items)
    charge_amount = sum(float(it.get("lineTotal", it.get("amount", 0)) or 0)
                       for it in charge_items)
    candidate_amount = round(payment_amount + max(charge_amount, 0.0), 2)
    candidate_count = len(payment_items) + len(charge_items)

    if not charge_items:
        # 現地決済は決済手段(type=payment)としてのみ出現。room chargeは既にtype=chargeで
        # booking.priceに計上済みのため、追加加算すると二重計上になる。
        return {"candidate_amount": candidate_amount, "candidate_count": candidate_count,
                "added_amount": 0.0, "added_count": 0, "status": "payment_method_only_not_revenue"}

    if charge_amount <= 0:
        return {"candidate_amount": candidate_amount, "candidate_count": candidate_count,
                "added_amount": 0.0, "added_count": 0, "status": "candidate_not_selected"}

    charge_sum_all = sum(float(it.get("lineTotal", it.get("amount", 0)) or 0)
                        for it in items if it.get("type") == "charge")
    price = float(raw.get("price") or 0)
    if charge_sum_all <= price + 0.5:
        # 現地決済起因のcharge行を含めても合計がprice以内 = 既にprice側に反映済み。
        return {"candidate_amount": candidate_amount, "candidate_count": candidate_count,
                "added_amount": 0.0, "added_count": 0, "status": "already_included_in_price"}

    return {"candidate_amount": candidate_amount, "candidate_count": candidate_count,
            "added_amount": round(charge_amount, 2), "added_count": len(charge_items),
            "status": "added_from_separate_charge"}


def _load_raw_index(raw_json_path: Optional[str]) -> Dict[str, dict]:
    """1つのraw JSONファイル(月次全予約)を読み、booking_idでインデックスする。"""
    if not raw_json_path:
        return {}
    try:
        data = json.loads(Path(raw_json_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {str(item.get("id") or item.get("booking_id") or ""): item for item in data}


def _load_raw_index_multi(bookings: List["BookingRecord"]) -> Dict[str, dict]:
    """複数raw_json_pathにまたがる予約群に対応するindex。

    【2026-07-11 バグ修正】月をまたぐ予約(例: 6月チェックイン→7月滞在)が対象月の
    "relevant"リストに混ざると、raw_json_pathは月ごとに別ファイルのため、従来の
    「最初に見つかった1件のraw_json_pathだけ読む」実装では、その月にしかデータが無い
    予約(例: 7月チェックインの予約)がindexから漏れ、coupon/point/onsite抽出が
    サイレントに0扱いになっていた(booking_id 89646497で発覚)。point/onsiteは元々
    実質0だったため影響が見えなかったが、coupon控除を導入したことで実害が顕在化した。
    出現する全てのraw_json_pathを読み込みマージすることで解消する。
    """
    merged: Dict[str, dict] = {}
    seen_paths = set()
    for b in bookings:
        if b.raw_json_path and b.raw_json_path not in seen_paths:
            seen_paths.add(b.raw_json_path)
            merged.update(_load_raw_index(b.raw_json_path))
    return merged


def compute(month: str, bookings: List[BookingRecord], exclude_statuses: List[str]) -> Dict:
    """月次のpoint加算額・coupon直割引額・件数・ロジック状態を算出する。

    キャンセル/gross金額は呼び出し側(revenue_recon)の既存集計をそのまま使い、
    ここでは二重控除しない。
    """
    in_month = [b for b in bookings if (b.checkin_date or "")[:7] == month]
    raw_json_path = next((b.raw_json_path for b in in_month if b.raw_json_path), None)
    raw_index = _load_raw_index(raw_json_path)

    point_revenue = 0.0
    point_count = 0
    coupon_discount = 0.0
    coupon_count = 0
    cancelled_count = 0
    cancel_field_missing = False
    point_signal_seen = False  # point系語がinvoiceItemsのどこかに出現したか（type問わず）

    onsite_revenue = 0.0
    onsite_booking_count = 0
    onsite_candidate_amount = 0.0
    onsite_candidate_count = 0
    onsite_statuses_seen = set()

    for b in in_month:
        is_cancelled = b.is_cancelled(exclude_statuses)
        raw = raw_index.get(b.booking_id)
        if raw and not has_cancel_signal_fields(raw):
            cancel_field_missing = True
        if raw and any(
                any(tok in str(item.get("description", "") or "").lower() for tok in POINT_TOKENS)
                for item in raw.get("invoiceItems") or []):
            point_signal_seen = True
        if is_cancelled:
            cancelled_count += 1
            continue  # キャンセル分にはpoint/coupon/現地決済も計上しない
        if raw:
            pt = extract_beds24_point_revenue(raw)
            if pt > 0:
                pt = _prorate_to_month(pt, b.checkin_date, b.checkout_date, month)
                point_revenue += pt
                point_count += 1
            cp = extract_beds24_coupon_discount(raw)
            if cp > 0:
                coupon_discount += cp
                coupon_count += 1
            onsite = extract_beds24_onsite_payment_revenue(raw)
            if onsite["status"] != "field_missing":
                onsite_statuses_seen.add(onsite["status"])
            if onsite["candidate_count"]:
                onsite_candidate_amount += _prorate_to_month(
                    onsite["candidate_amount"], b.checkin_date, b.checkout_date, month)
                onsite_candidate_count += 1
            if onsite["added_amount"] > 0:
                onsite_revenue += _prorate_to_month(
                    onsite["added_amount"], b.checkin_date, b.checkout_date, month)
                onsite_booking_count += 1

    status_flags = []
    if not raw_index:
        status_flags.append("raw_payload_unavailable")
    elif point_revenue > 0:
        status_flags.append("point_added_from_invoice_items")
    elif point_signal_seen:
        status_flags.append("point_already_included_in_price")
    else:
        status_flags.append("point_field_missing")
    if cancel_field_missing:
        status_flags.append("cancel_status_field_missing")
    logic_status = ",".join(status_flags)

    note = ("クーポン利用額は売上からは控除せず、月次合計をBI上の参考情報として表示のみします"
            "（2026-07-11、ユーザー最終判断で確定。beds24_coupon_discount_amount/"
            "beds24_coupon_reference_amountが参考額）。pointも同様にpriceの決済チャネル内訳の"
            "一つに過ぎず、売上へは加算しません（現状の実データではpointもinvoiceItems "
            "type=payment（決済手段）としてのみ出現し、室料charge(price)に既に全額含まれています。"
            "type=chargeにpoint起因の行が見つかった場合のみ従来通り自動加算）。"
            "キャンセル済み予約はstatusフィールドで判定し、速報売上からは除外しています"
            "（point/couponの参考額もキャンセル分は含めません）。")

    onsite_priority = ["added_from_separate_charge", "payment_method_only_not_revenue",
                      "already_included_in_price", "candidate_not_selected", "field_missing"]
    onsite_logic_status = next(
        (s for s in onsite_priority if s in onsite_statuses_seen), "field_missing")
    onsite_note = {
        "field_missing": "現地決済/現地払いに該当するinvoiceItemが見つかりませんでした。",
        "payment_method_only_not_revenue": (
            "現地決済/現地払いはinvoiceItems type=payment（決済手段）としてのみ出現し、"
            "room chargeは既にtype=chargeでbooking.priceに全額計上済みのため加算していません。"),
        "already_included_in_price": (
            "現地決済起因のcharge行がありますが、charge合計がbooking.price以内のため"
            "既に売上に反映済みとみなし加算していません。"),
        "candidate_not_selected": "現地決済候補はありますが金額が0以下等のため加算していません。",
        "added_from_separate_charge": (
            "現地決済起因の別建てcharge行をbooking.priceの追加分として売上に加算しました。"),
    }[onsite_logic_status]

    return {
        # --- point（売上加算対象）---
        "beds24_point_revenue_included": round(point_revenue),
        "beds24_point_booking_count": point_count,
        # --- coupon（売上には加算も控除もしない。月次合計をBI参考情報として表示のみ）---
        "beds24_coupon_discount_detected": coupon_count > 0,
        "beds24_coupon_discount_amount": round(coupon_discount),
        "beds24_coupon_reference_amount": round(coupon_discount),  # 同値。売上非控除であることを明示する別名
        "beds24_coupon_discount_booking_count": coupon_count,
        # --- 現地決済/現地払い（原則priceに含まれているため既定は0。実データで別建てcharge
        #     が見つかった場合のみ加算候補となる）---
        "beds24_onsite_payment_revenue_included": round(onsite_revenue),
        "beds24_onsite_payment_booking_count": onsite_booking_count,
        "beds24_onsite_payment_candidate_amount": round(onsite_candidate_amount),
        "beds24_onsite_payment_candidate_count": onsite_candidate_count,
        "beds24_onsite_payment_logic_status": onsite_logic_status,
        "beds24_onsite_payment_logic_note": onsite_note,
        # --- 旧field（意味が誤っていたためdeprecated。互換性のため0で残す）---
        "beds24_coupon_revenue_included": 0,
        "beds24_coupon_booking_count": 0,
        # --- ロジック状態 ---
        "beds24_revenue_logic_version": REVENUE_LOGIC_VERSION,
        "beds24_revenue_logic_status": logic_status,
        "beds24_revenue_logic_note": note,
        "beds24_cancelled_booking_count": cancelled_count,
    }


def jst_today() -> date:
    return datetime.now(timezone.utc).astimezone(JST).date()


def _created_datetime_jst(created_at_raw: str) -> Optional[datetime]:
    """Beds24 bookingTime(UTC ISO8601, 例: 2026-07-07T12:01:31Z) をJST datetimeへ変換する。"""
    if not created_at_raw:
        return None
    s = str(created_at_raw).strip()
    try:
        if s.endswith("Z"):
            dt = datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        else:
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(JST)
    except ValueError:
        return None


def _created_date_jst(created_at_raw: str) -> Optional[date]:
    """Beds24 bookingTime(UTC ISO8601) をJST日付へ変換する。"""
    dt = _created_datetime_jst(created_at_raw)
    return dt.date() if dt else None


def _booking_overlaps_month(checkin: str, checkout: str, month: str) -> bool:
    """予約の宿泊期間[checkin, checkout)がmonth(YYYY-MM)に1泊以上かかるか。"""
    if not checkin:
        return False
    if not checkout or checkout <= checkin:
        return checkin[:7] == month
    try:
        ci = date.fromisoformat(checkin[:10])
        co = date.fromisoformat(checkout[:10])
    except ValueError:
        return checkin[:7] == month
    y, m = (int(x) for x in month.split("-"))
    month_start = date(y, m, 1)
    month_end_exclusive = date(y, m, monthrange(y, m)[1]) + timedelta(days=1)
    return ci < month_end_exclusive and co > month_start


def calculate_today_new_bookings_for_month(bookings: List[BookingRecord], target_month: str,
                                           today_jst: date, exclude_statuses: List[str]) -> Dict:
    """選択中の対象月について、JST今日Beds24上で新規作成された予約の件数・金額を計算する。

    - 対象: 宿泊期間が target_month に1泊以上かかるbooking（月またぎは既存の宿泊月按分を再利用）。
    - count/revenueはbooking単位（既存システムがroom night単位ではなくbooking単位のため）。
    - 非キャンセルのみをcount/revenueに含める。同日作成・同日キャンセルは除外件数/除外額に計上する。
    - created_at_raw が無い/解析できないbookingは判定不可として logic_status に反映する
      （created_at_field_missing。推測で当日扱いにしない）。
    """
    relevant = [b for b in bookings if _booking_overlaps_month(b.checkin_date, b.checkout_date, target_month)]
    raw_index = _load_raw_index_multi(relevant)

    count = 0
    gross_stay_revenue = 0.0
    point_revenue = 0.0
    onsite_payment_revenue = 0.0
    cancelled_revenue_excluded = 0.0
    cancelled_count = 0
    sample_ids: List[str] = []
    details: List[Dict] = []
    any_created_at_present = False

    for b in relevant:
        created_dt = _created_datetime_jst(b.created_at_raw)
        if created_dt is None:
            continue
        any_created_at_present = True
        if created_dt.date() != today_jst:
            continue

        is_cancelled = b.is_cancelled(exclude_statuses)
        recognized_booking_revenue = calculate_recognized_booking_revenue(b, raw_index)
        prorated_gross = _prorate_to_month(recognized_booking_revenue, b.checkin_date, b.checkout_date, target_month)
        if is_cancelled:
            # 同日作成・同日キャンセルのみを除外件数/除外額として計上する（原則除外対象は少ない想定）。
            # detailsには出さない（一覧は非キャンセル予約のみが対象）。
            cancelled_count += 1
            cancelled_revenue_excluded += prorated_gross
            continue

        count += 1
        gross_stay_revenue += prorated_gross
        prorated_point = 0.0
        prorated_onsite = 0.0
        raw = raw_index.get(b.booking_id)
        if raw:
            pt = extract_beds24_point_revenue(raw)
            if pt > 0:
                prorated_point = _prorate_to_month(pt, b.checkin_date, b.checkout_date, target_month)
                point_revenue += prorated_point
            onsite = extract_beds24_onsite_payment_revenue(raw)
            if onsite["added_amount"] > 0:
                prorated_onsite = _prorate_to_month(
                    onsite["added_amount"], b.checkin_date, b.checkout_date, target_month)
                onsite_payment_revenue += prorated_onsite
        if len(sample_ids) < 5:
            sample_ids.append(b.booking_id)

        # 一覧表示用の予約単位詳細。PII(email/phone/address/message等)は含めない。
        # guest_nameは既存BookingRecord.guest_name(氏名のみ。BedsClient側で既に住所等を除外済み)を使う。
        detail_revenue = round(prorated_gross + prorated_point + prorated_onsite)
        ota_name, booking_source_raw = normalize_booking_source(b.channel)
        room_change = extract_room_change_history(b)
        details.append({
            "booking_id": b.booking_id,
            "checkin": b.checkin_date,
            "checkout": b.checkout_date,
            "guest_name": b.guest_name or "氏名未取得",
            "revenue_for_target_month": detail_revenue,
            "onsite_payment_revenue_for_target_month": round(prorated_onsite),
            "total_booking_revenue": round(recognized_booking_revenue),
            "target_month_nights": _nights_in_month(b.checkin_date, b.checkout_date, target_month),
            "total_nights": _total_nights(b.checkin_date, b.checkout_date),
            "room_name": b.room_name or None,
            "status": b.status,
            "created_at_jst": created_dt.isoformat(timespec="seconds"),
            # --- 予約経路(OTA)。room_type系は室タイプ設定を持つmonthly.py側で付与する ---
            "ota_name": ota_name,
            "booking_source_raw": booking_source_raw,
            "room_id": b.room_id or None,
            "room_change_history_status": room_change["status"],
            "room_change_history": room_change["changes"],
        })

    # today_new_booking_revenue は details の合計と必ず一致させる(丸め誤差防止のため
    # detailsの丸め済み値をそのまま合計する。gross/point/onsiteから再計算しない)。
    # cancelled_revenue_excluded は表示専用の除外額。cancelled分はcontinueで既に
    # gross_stay_revenueへ未加算のため、ここで再度引くと二重控除になる（引かない）。
    revenue = sum(d["revenue_for_target_month"] for d in details)

    if not any_created_at_present:
        logic_status = "created_at_field_missing"
        note = ("Beds24予約に作成日時field(bookingTime)が見つからないため、"
               "本日の新規予約を判定できません。")
    else:
        logic_status = "ok"
        note = ("JST今日(bookingTime基準)に作成され、対象月に1泊以上かかる非キャンセル予約を集計。"
               "金額はprice(総額。coupon/point/banktransfer/事前決済/現地決済いずれも別途"
               "加算・控除しない)を宿泊月按分ロジックで対象月に按分したうえで現地決済加算(現状0)。"
               "couponの月次合計はBI上の参考情報として別途表示する。"
               "同日作成・同日キャンセルは除外件数/除外額に計上。")

    return {
        "today_new_booking_count": count,
        "today_new_booking_revenue": round(revenue),
        "today_new_booking_gross_stay_revenue": round(gross_stay_revenue),
        "today_new_booking_point_revenue": round(point_revenue),
        "today_new_booking_onsite_payment_revenue": round(onsite_payment_revenue),
        "today_new_booking_cancelled_revenue_excluded": round(cancelled_revenue_excluded),
        "today_new_booking_cancelled_count": cancelled_count,
        "today_new_booking_ids_sample": sample_ids,
        "today_new_booking_details": details,
        "today_new_booking_logic_status": logic_status,
        "today_new_booking_logic_note": note,
        "today_new_booking_calculated_at_jst": datetime.now(timezone.utc).astimezone(JST).isoformat(
            timespec="seconds"),
    }


def _global_booking_detail(b: BookingRecord, recognized_revenue: float,
                           created_dt: Optional[datetime]) -> Dict:
    """月選択に依らないグローバルサマリー用の予約単位詳細（PII非含有）。"""
    ota_name, booking_source_raw = normalize_booking_source(b.channel)
    room_change = extract_room_change_history(b)
    return {
        "booking_id": b.booking_id,
        "checkin": b.checkin_date,
        "checkout": b.checkout_date,
        "guest_name": b.guest_name or "氏名未取得",
        "revenue": round(recognized_revenue),
        "room_name": b.room_name or None,
        "room_id": b.room_id or None,
        "status": b.status,
        "created_at_jst": created_dt.isoformat(timespec="seconds") if created_dt else None,
        "ota_name": ota_name,
        "booking_source_raw": booking_source_raw,
        "room_change_history_status": room_change["status"],
        "room_change_history": room_change["changes"],
    }


_NEW_BOOKING_NOTE_OK = (
    "JST{label}(bookingTime基準)に作成された非キャンセル予約を、チェックイン月を問わず"
    "予約単位の総売上額(price)で合算。月按分は行わない。")
_NEW_BOOKING_NOTE_MISSING = (
    "Beds24予約に作成日時field(bookingTime)が見つからないため、{label}の新規予約を判定できません。")
_CHECKIN_NOTE_OK = (
    "チェックイン日がJST{label}の非キャンセル予約を、作成日を問わず予約単位の総売上額(price)で"
    "合算。月按分は行わない。")


def _daily_bucket(count: int, revenue: float, details: List[Dict], date_iso: str,
                  status: str, note: str) -> Dict:
    """1カード分の正規化済み集計結果（single source of truthのdaily_global_summary用）。

    正常に計算できた0件は status="ok" とする（"判定不可"はschema欠損・作成日時field自体が
    見つからない等、本当に判定不能な場合のみ）。count/revenue/detailsの整合性
    (count == len(details), revenue == sum(details[].revenue)) は呼び出し元で保証済み。
    """
    return {"date_jst": date_iso, "status": status, "count": count,
            "revenue": round(revenue), "details": details, "note": note}


def calculate_today_global_summary(bookings: List[BookingRecord], today_jst: date,
                                   exclude_statuses: List[str]) -> Dict:
    """月選択に依らない、グローバルな「日次」サマリー（BI画面上段の共通表示用）。

    月別ダッシュボード(calculate_today_new_bookings_for_month)とは異なり、対象月による
    フィルタ・按分を一切行わない。3つの独立した集計を返す:
      - 本日の新規予約: JST今日Beds24上で作成された全予約（チェックイン月を問わない）。
      - 前日の新規予約: JST前日に作成された全予約（チェックイン月を問わない）。
      - 本日チェックイン: チェックイン日がJST今日の全予約（作成日を問わない）。
    いずれも金額は予約単位の総売上額(calculate_recognized_booking_revenue、price総額)を
    月按分せずそのまま合算し、非キャンセルのみを対象とする（is_cancelled(exclude_statuses)）。
    created_at_raw が無い/解析できない予約は新規予約側の判定から除外し、全予約で
    判定不可の場合のみ status="created_at_field_missing" にする（推測で当日扱いにしない）。
    正常に該当0件の場合はstatus="ok"のまま(count=0/revenue=0/details=[])とし、
    「判定不可」とは区別する。

    戻り値には新設の `daily_global_summary`(single source of truth) と、既存フロント
    互換のためのflat fields(`*_global`)の両方を含む。
    """
    raw_index = _load_raw_index_multi(bookings)
    yesterday_jst = today_jst - timedelta(days=1)
    today_iso = today_jst.isoformat()
    yesterday_iso = yesterday_jst.isoformat()

    new_count = 0
    new_revenue = 0.0
    new_details: List[Dict] = []
    yesterday_count = 0
    yesterday_revenue = 0.0
    yesterday_details: List[Dict] = []
    any_created_at_present = False

    checkin_count = 0
    checkin_revenue = 0.0
    checkin_details: List[Dict] = []

    seen_booking_ids = set()

    for b in bookings:
        # 同一booking_idが複数raw_json_pathにまたがって重複読込されるケースの排除。
        if b.booking_id in seen_booking_ids:
            continue
        seen_booking_ids.add(b.booking_id)

        if b.is_cancelled(exclude_statuses):
            continue
        recognized = calculate_recognized_booking_revenue(b, raw_index)
        created_dt = _created_datetime_jst(b.created_at_raw)
        if created_dt is not None:
            any_created_at_present = True
            created_date = created_dt.date()
            if created_date == today_jst:
                new_count += 1
                new_revenue += recognized
                new_details.append(_global_booking_detail(b, recognized, created_dt))
            elif created_date == yesterday_jst:
                yesterday_count += 1
                yesterday_revenue += recognized
                yesterday_details.append(_global_booking_detail(b, recognized, created_dt))

        if (b.checkin_date or "")[:10] == today_iso:
            checkin_count += 1
            checkin_revenue += recognized
            checkin_details.append(_global_booking_detail(b, recognized, created_dt))

    new_logic_status = "ok" if any_created_at_present else "created_at_field_missing"
    new_logic_note = (_NEW_BOOKING_NOTE_OK.format(label="今日") if new_logic_status == "ok"
                      else _NEW_BOOKING_NOTE_MISSING.format(label="本日"))
    yesterday_logic_status = new_logic_status
    yesterday_logic_note = (_NEW_BOOKING_NOTE_OK.format(label="前日") if yesterday_logic_status == "ok"
                            else _NEW_BOOKING_NOTE_MISSING.format(label="前日"))
    checkin_logic_status = "ok"
    checkin_logic_note = _CHECKIN_NOTE_OK.format(label="今日")

    now_jst = datetime.now(timezone.utc).astimezone(JST).isoformat(timespec="seconds")

    daily_global_summary = {
        "today_new_bookings": _daily_bucket(
            new_count, new_revenue, new_details, today_iso, new_logic_status, new_logic_note),
        "yesterday_new_bookings": _daily_bucket(
            yesterday_count, yesterday_revenue, yesterday_details, yesterday_iso,
            yesterday_logic_status, yesterday_logic_note),
        "today_checkins": _daily_bucket(
            checkin_count, checkin_revenue, checkin_details, today_iso,
            checkin_logic_status, checkin_logic_note),
    }

    return {
        "daily_global_summary": daily_global_summary,
        # --- 既存フロント互換のためのflat fields ---
        "today_new_booking_count_global": new_count,
        "today_new_booking_revenue_global": round(new_revenue),
        "today_new_booking_details_global": new_details,
        "today_new_booking_logic_status_global": new_logic_status,
        "today_new_booking_logic_note_global": new_logic_note,
        "yesterday_new_booking_count_global": yesterday_count,
        "yesterday_new_booking_revenue_global": round(yesterday_revenue),
        "yesterday_new_booking_details_global": yesterday_details,
        "yesterday_new_booking_logic_status_global": yesterday_logic_status,
        "yesterday_new_booking_logic_note_global": yesterday_logic_note,
        "today_checkin_count_global": checkin_count,
        "today_checkin_revenue_global": round(checkin_revenue),
        "today_checkin_details_global": checkin_details,
        "today_global_jst": today_iso,
        "yesterday_global_jst": yesterday_iso,
        "today_global_calculated_at_jst": now_jst,
    }
