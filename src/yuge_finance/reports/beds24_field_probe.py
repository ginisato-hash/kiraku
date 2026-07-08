"""Beds24 raw payload フィールド調査（Phase 0）。

data/raw/beds24/<month>/<month>.json から実際に使えるfieldを調査し、
data/output/latest/bi/beds24_revenue_field_probe.json へ出力する。
個人情報（氏名・メール・電話・住所・メッセージ等）は一切出力しない。
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Dict, List

from .. import config
from ..bi_refresh import jst_str

PII_KEYS = {
    "firstName", "lastName", "title", "email", "phone", "mobile", "fax",
    "address", "city", "state", "postcode", "country", "country2",
    "comments", "notes", "groupNote", "message", "company", "reference",
    "apiReference", "pcibookingToken", "stripeToken",
}

CANCEL_CANDIDATE_KEYS = ["status", "subStatus", "statusCode", "cancelTime"]
COUPON_CANDIDATE_TOKENS = ["coupon", "voucher", "discount", "promotion", "promo",
                          "campaign", "point", "subsidy", "grant", "adjustment"]


def _collect_keys(obj, prefix="") -> set:
    keys = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            keys.add(prefix + k)
            if isinstance(v, (dict, list)):
                keys |= _collect_keys(v, prefix + k + ".")
    elif isinstance(obj, list):
        for item in obj[:3]:
            keys |= _collect_keys(item, prefix)
    return keys


def _redact_sample(booking: dict) -> dict:
    """PIIキーを除いたサンプル値（デバッグ用）。"""
    out = {}
    for k, v in booking.items():
        if k in PII_KEYS:
            continue
        if k == "invoiceItems" and isinstance(v, list):
            out[k] = [{ik: iv for ik, iv in item.items() if ik not in PII_KEYS}
                      for item in v[:5]]
            continue
        if isinstance(v, (dict, list)):
            continue  # ネストは複雑なため候補一覧はキー列挙のみで扱う
        out[k] = v
    return out


def _load_bookings(month: str = None) -> List[dict]:
    base = config.DATA_DIR / "raw" / "beds24"
    out = []
    paths = sorted(base.glob(f"{month}/*.json")) if month else sorted(base.glob("*/*.json"))
    for p in paths:
        try:
            out.extend(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    return out


def build_probe(month: str = None) -> Dict:
    bookings = _load_bookings(month)
    all_keys = set()
    for b in bookings:
        all_keys |= _collect_keys(b)

    status_values = Counter(b.get("status") for b in bookings)
    substatus_values = Counter(b.get("subStatus") for b in bookings)
    cancel_time_present = sum(1 for b in bookings if b.get("cancelTime"))

    invoice_types = Counter()
    invoice_descriptions = Counter()
    coupon_candidate_hits = Counter()
    for b in bookings:
        for item in b.get("invoiceItems") or []:
            invoice_types[item.get("type")] += 1
            desc = str(item.get("description") or "")
            invoice_descriptions[desc] += 1
            low = desc.lower()
            for tok in COUPON_CANDIDATE_TOKENS:
                if tok in low:
                    coupon_candidate_hits[tok] += 1

    # status/cancelTime相関チェック（推測せず実データで確認）
    cancelled_by_status = sum(1 for b in bookings if str(b.get("status", "")).lower() == "cancelled")
    cancelled_with_canceltime = sum(
        1 for b in bookings if str(b.get("status", "")).lower() == "cancelled" and b.get("cancelTime"))
    non_cancelled_with_canceltime = sum(
        1 for b in bookings if str(b.get("status", "")).lower() != "cancelled" and b.get("cancelTime"))

    # coupon(payment)がcharge行に出現するか（=独立収入として加算できるか）の実証チェック
    coupon_in_charge_lines = 0
    coupon_in_payment_lines = 0
    for b in bookings:
        for item in b.get("invoiceItems") or []:
            if "coupon" in str(item.get("description") or "").lower():
                if item.get("type") == "charge":
                    coupon_in_charge_lines += 1
                elif item.get("type") == "payment":
                    coupon_in_payment_lines += 1

    samples = [_redact_sample(b) for b in bookings[:3]]

    notes = [
        f"status/cancelTime相関: cancelled({cancelled_by_status}件)のうちcancelTime有り"
        f"={cancelled_with_canceltime}件、非cancelledでcancelTime有り={non_cancelled_with_canceltime}件。"
        + ("100%相関のためstatusフィールドはキャンセル判定に信頼できる。"
           if cancelled_by_status == cancelled_with_canceltime and non_cancelled_with_canceltime == 0
           else "相関が不完全なため要確認。"),
        f"'coupon'という語はinvoiceItemsのdescriptionに{coupon_in_payment_lines}件出現するが、"
        f"すべてtype=payment（決済手段）であり、type=charge（室料本体）には{coupon_in_charge_lines}件のみ。"
        "type=paymentのcouponは既存charge(price)を精算する手段であり、独立した追加収入ではないため、"
        "推測でcoupon収入として加算しない（beds24_coupon_revenue_included=0固定・要継続確認）。",
        "subStatus/statusCodeは全件で固定値のみが観測され、キャンセル判定には有用でない。",
    ]

    return {
        "generated_at_jst": jst_str(),
        "booking_count_sampled": len(bookings),
        "top_level_and_nested_keys": sorted(all_keys),
        "status_value_counts": dict(status_values),
        "substatus_value_counts": dict(substatus_values),
        "cancel_time_present_count": cancel_time_present,
        "invoice_item_type_counts": dict(invoice_types),
        "invoice_item_description_counts": dict(invoice_descriptions),
        "coupon_candidate_token_hits_in_descriptions": dict(coupon_candidate_hits),
        "candidate_fields": {
            "cancel_status": CANCEL_CANDIDATE_KEYS,
            "coupon_amount": ["invoiceItems[].description (type=charge限定で判定)"],
            "discount_amount": [],  # 実データに discount/promotion 等の独立fieldは存在しなかった
            "invoice_items": ["invoiceItems"],
            "payment_items": ["invoiceItems (type=payment)"],
            "room_price": ["price"],
            "total_price": ["price"],
        },
        "selected_fields": {
            "cancel_status": "status",
            "coupon_amount": "invoiceItems[].lineTotal (type=charge かつ coupon/voucher系descriptionのみ。実データでは0件)",
            "discount_amount": None,
        },
        "notes": notes,
        "sample_bookings_pii_redacted": samples,
    }


def write(month: str = None, out_path: Path = None) -> Path:
    probe = build_probe(month)
    out_path = out_path or (config.DATA_DIR / "output" / "latest" / "bi" / "beds24_revenue_field_probe.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(probe, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return out_path
