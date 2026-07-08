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

本ロジックはキャンセル除外を既存(revenue_recon)の集計と同じ判定基準に揃えることで
二重控除を避け、point加算・coupon直割引の情報のみを新規に追加する。
"""
from __future__ import annotations

import json
from calendar import monthrange
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional

from ..normalize.schema import BookingRecord

REVENUE_LOGIC_VERSION = "beds24_revenue_v3"

CANCEL_TOKENS = ("cancelled", "canceled", "キャンセル")

# coupon/discount系（直割引。売上には加算しない。情報表示専用）
COUPON_TOKENS = ("coupon", "voucher", "クーポン", "割引")
# point/reward系（施設収入として加算可能な候補）
POINT_TOKENS = ("point", "points", "reward", "rewards", "loyalty", "楽天ポイント", "ポイント")


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
    y, m = (int(x) for x in month.split("-"))
    month_start = date(y, m, 1)
    month_end_exclusive = date(y, m, monthrange(y, m)[1]) + timedelta(days=1)
    overlap_start = max(ci, month_start)
    overlap_end = min(co, month_end_exclusive)
    target_nights = max((overlap_end - overlap_start).days, 0)
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
    """raw Beds24 booking dict から『直割引』couponの金額を抽出する（情報表示専用）。

    invoiceItems の type="payment" に coupon/クーポン/割引 等の説明を持つ行の金額（絶対値）を
    「割引額」として返す。売上には加算しない（呼び出し側で net revenue に含めないこと）。
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


def extract_beds24_coupon_revenue(raw: dict) -> float:
    """DEPRECATED: couponは直割引であり売上には加算しない。常に0.0を返す。

    互換性のため関数は残すが、収入としては扱わない。金額が必要な場合は
    extract_beds24_coupon_discount() を使うこと。
    """
    return 0.0


def _load_raw_index(raw_json_path: Optional[str]) -> Dict[str, dict]:
    """1つのraw JSONファイル(月次全予約)を読み、booking_idでインデックスする。"""
    if not raw_json_path:
        return {}
    try:
        data = json.loads(Path(raw_json_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {str(item.get("id") or item.get("booking_id") or ""): item for item in data}


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
            continue  # キャンセル分にはpoint/couponも計上しない
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

    note = ("couponは直割引扱いのため売上加算しません。pointは施設収入として扱えるため"
            "売上加算対象ですが、現状の実データではpointもinvoiceItems type=payment"
            "（決済手段）としてのみ出現し、室料charge(price)に既に全額含まれているため、"
            "二重計上防止のため加算額は0円としています"
            "（type=chargeにpoint起因の行が見つかった場合のみ自動加算）。"
            "キャンセル済み予約はstatusフィールドで判定し、速報売上・point・couponともに除外しています。")

    return {
        # --- point（売上加算対象）---
        "beds24_point_revenue_included": round(point_revenue),
        "beds24_point_booking_count": point_count,
        # --- coupon（直割引。売上には加算しない。情報表示専用）---
        "beds24_coupon_discount_detected": coupon_count > 0,
        "beds24_coupon_discount_amount": round(coupon_discount),
        "beds24_coupon_discount_booking_count": coupon_count,
        # --- 旧field（意味が誤っていたためdeprecated。互換性のため0で残す）---
        "beds24_coupon_revenue_included": 0,
        "beds24_coupon_booking_count": 0,
        # --- ロジック状態 ---
        "beds24_revenue_logic_version": REVENUE_LOGIC_VERSION,
        "beds24_revenue_logic_status": logic_status,
        "beds24_revenue_logic_note": note,
        "beds24_cancelled_booking_count": cancelled_count,
    }
