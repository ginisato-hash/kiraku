"""Beds24 速報売上ロジック v2（クーポン加算・キャンセル除外の明確化）。

Phase 0調査の結論（喜らく実データ187件で実証・推測ではない）:
  - キャンセル判定: raw `status` フィールドが `cancelTime` の有無と100%相関しており、
    キャンセル判定に信頼できる（cancelled 26件、全件cancelTime有り。非cancelledでの
    cancelTime有りは0件）。
  - クーポン: invoiceItems上の "coupon" はすべて type="payment"（決済手段の一つ。
    事前払い/BankTransfer/pointと同列）であり、type="charge"（室料本体）には
    一切出現しない（0件）。つまり `price`（室料charge）はクーポン精算の有無に関わらず
    既に室料全額を反映しており、独立して加算すべき「クーポン収入」フィールドは
    実データ上に存在しない。そのため beds24_coupon_revenue_included は既定0とし、
    beds24_revenue_logic_status に "coupon_field_missing" を含める。
    将来、type="charge"にcoupon/voucher等の説明を持つ行が現れた場合のみ、
    extract_beds24_coupon_revenue() が自動的にそれを収入として拾う設計にしてある。

本ロジックはキャンセル除外を既存(revenue_recon)の集計と同じ判定基準に揃えることで
二重控除を避け、クーポン加算のみを新規に追加する。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional

from ..normalize.schema import BookingRecord

REVENUE_LOGIC_VERSION = "beds24_revenue_v2"

CANCEL_TOKENS = ("cancelled", "canceled", "キャンセル")
# 室料charge行にこれらの語が含まれる場合のみ、収入として加算する候補にする。
# type=paymentの決済手段(coupon等)は対象外（既にcharge=室料本体で計上済みのため）。
COUPON_CHARGE_TOKENS = ("coupon", "voucher", "クーポン", "補助", "助成", "割引補填")


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


def extract_beds24_coupon_revenue(raw: dict) -> float:
    """raw Beds24 booking dict から『施設収入として加算できる』クーポン金額を抽出する。

    invoiceItems の type="charge" に coupon/voucher/クーポン/補助/助成 等の説明を
    持つ行があれば、その金額を収入として加算する。type="payment" のcoupon(決済手段)は
    室料charge自体が既に全額計上されているため加算しない（二重計上防止）。
    金額がマイナスの場合（値引き系）は収入として扱わない。
    """
    if not raw:
        return 0.0
    total = 0.0
    for item in raw.get("invoiceItems") or []:
        if item.get("type") != "charge":
            continue
        desc = str(item.get("description", "") or "").lower()
        if any(tok.lower() in desc for tok in COUPON_CHARGE_TOKENS):
            amt = item.get("lineTotal", item.get("amount", 0)) or 0
            if amt > 0:
                total += amt
    return total


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
    """月次のクーポン加算額・件数・ロジック状態を算出する（キャンセル/gross金額は呼び出し側の
    既存集計をそのまま使い、ここでは二重控除しない）。
    """
    in_month = [b for b in bookings if (b.checkin_date or "")[:7] == month]
    raw_json_path = next((b.raw_json_path for b in in_month if b.raw_json_path), None)
    raw_index = _load_raw_index(raw_json_path)

    coupon_revenue = 0.0
    coupon_count = 0
    cancelled_count = 0
    cancel_field_missing = False

    for b in in_month:
        is_cancelled = b.is_cancelled(exclude_statuses)
        raw = raw_index.get(b.booking_id)
        if raw and not has_cancel_signal_fields(raw):
            cancel_field_missing = True
        if is_cancelled:
            cancelled_count += 1
            continue  # キャンセル分にはクーポンも計上しない
        if raw:
            amt = extract_beds24_coupon_revenue(raw)
            if amt > 0:
                coupon_revenue += amt
                coupon_count += 1

    status_flags = []
    if not raw_index:
        status_flags.append("raw_payload_unavailable")
    elif coupon_revenue == 0:
        status_flags.append("coupon_field_missing")
    else:
        status_flags.append("coupon_included")
    if cancel_field_missing:
        status_flags.append("cancel_status_field_missing")
    logic_status = ",".join(status_flags)

    note = ("クーポンはBeds24上で決済手段(invoiceItems type=payment)としてのみ出現し、"
            "室料charge自体は既に全額計上済みのため、独立収入としては加算していません"
            "（type=chargeにcoupon等の説明がある行が見つかった場合のみ自動加算）。"
            "キャンセル済み予約はstatusフィールドで判定し、速報売上・クーポンともに除外しています。")

    return {
        "beds24_coupon_revenue_included": round(coupon_revenue),
        "beds24_revenue_logic_version": REVENUE_LOGIC_VERSION,
        "beds24_revenue_logic_status": logic_status,
        "beds24_revenue_logic_note": note,
        "beds24_cancelled_booking_count": cancelled_count,
        "beds24_coupon_booking_count": coupon_count,
    }
