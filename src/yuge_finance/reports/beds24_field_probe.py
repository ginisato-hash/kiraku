"""Beds24 raw payload フィールド調査（Phase 0）。

data/raw/beds24/<month>/<month>.json から実際に使えるfieldを調査し、
data/output/latest/bi/beds24_revenue_field_probe.json へ出力する。
個人情報（氏名・メール・電話・住所・メッセージ等）は一切出力しない。

point/couponの分類方針（実データで実証。推測ではない）:
  - coupon: 直割引扱い。売上には加算しない（classification.coupon = direct_discount_not_revenue）。
  - point: 施設側収入として加算可能な候補（classification.point = revenue_addition_candidate）。
    ただし実データでは point もすべて invoiceItems type=payment（決済手段）としてのみ出現し、
    type=charge（室料本体）には一切出現しない。price は既にpoint精算の有無に関わらず
    室料全額を反映しているため、現状は point_already_included_in_price として0円加算とする。
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

# coupon/discount系（直割引。売上加算しない）
COUPON_TOKENS = ["coupon", "voucher", "discount", "promotion", "promo", "campaign",
                "クーポン", "割引"]
# point/reward系（施設収入として加算可能な候補）
POINT_TOKENS = ["point", "points", "reward", "rewards", "loyalty", "楽天ポイント",
               "ポイント", "usepoint", "usedpoint", "pointamount", "pointsamount",
               "pointpayment"]
# その他の収入加算候補語（要継続調査）
OTHER_CANDIDATE_TOKENS = ["subsidy", "grant", "adjustment"]


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


def _classify_hits(bookings: List[dict], tokens: List[str]):
    """invoiceItemsのdescriptionからtoken一致行を type=charge / type=payment 別に集計する。"""
    in_charge = 0
    in_payment = 0
    token_hits = Counter()
    for b in bookings:
        for item in b.get("invoiceItems") or []:
            desc_low = str(item.get("description") or "").lower()
            for tok in tokens:
                if tok in desc_low:
                    token_hits[tok] += 1
                    if item.get("type") == "charge":
                        in_charge += 1
                    elif item.get("type") == "payment":
                        in_payment += 1
                    break
    return in_charge, in_payment, token_hits


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
    for b in bookings:
        for item in b.get("invoiceItems") or []:
            invoice_types[item.get("type")] += 1
            invoice_descriptions[str(item.get("description") or "")] += 1

    # status/cancelTime相関チェック（推測せず実データで確認）
    cancelled_by_status = sum(1 for b in bookings if str(b.get("status", "")).lower() == "cancelled")
    cancelled_with_canceltime = sum(
        1 for b in bookings if str(b.get("status", "")).lower() == "cancelled" and b.get("cancelTime"))
    non_cancelled_with_canceltime = sum(
        1 for b in bookings if str(b.get("status", "")).lower() != "cancelled" and b.get("cancelTime"))

    # coupon: type=charge / type=payment 別出現数
    coupon_in_charge, coupon_in_payment, coupon_hits = _classify_hits(bookings, COUPON_TOKENS)
    # point: type=charge / type=payment 別出現数
    point_in_charge, point_in_payment, point_hits = _classify_hits(bookings, POINT_TOKENS)
    _, _, other_hits = _classify_hits(bookings, OTHER_CANDIDATE_TOKENS)

    # point payment行が予約のprice(charge合計)と重複していないかの実証チェック
    point_bookings_checked = 0
    point_matches_price = 0
    for b in bookings:
        has_point = any(any(tok in str(it.get("description") or "").lower() for tok in POINT_TOKENS)
                        for it in (b.get("invoiceItems") or []) if it.get("type") == "payment")
        if not has_point:
            continue
        point_bookings_checked += 1
        charge_sum = sum(float(it.get("lineTotal", 0) or 0)
                         for it in (b.get("invoiceItems") or []) if it.get("type") == "charge")
        if charge_sum == float(b.get("price") or 0):
            point_matches_price += 1

    samples = [_redact_sample(b) for b in bookings[:3]]

    point_status = ("point_already_included_in_price"
                    if point_in_charge == 0 and point_in_payment > 0
                    else "point_added_from_invoice_items" if point_in_charge > 0
                    else "point_field_missing")

    notes = [
        f"status/cancelTime相関: cancelled({cancelled_by_status}件)のうちcancelTime有り"
        f"={cancelled_with_canceltime}件、非cancelledでcancelTime有り={non_cancelled_with_canceltime}件。"
        + ("100%相関のためstatusフィールドはキャンセル判定に信頼できる。"
           if cancelled_by_status == cancelled_with_canceltime and non_cancelled_with_canceltime == 0
           else "相関が不完全なため要確認。"),
        f"[coupon=直割引・売上加算しない] 'coupon'系語はinvoiceItemsのdescriptionに"
        f"type=payment側{coupon_in_payment}件、type=charge側{coupon_in_charge}件出現。"
        "type=paymentのcouponは既存charge(price)を精算する決済手段であり、売上ではなく"
        "直割引として扱う（beds24_coupon_discount_amountに金額を保持。売上には加算しない）。",
        f"[point=売上加算候補] 'point'系語はinvoiceItemsのdescriptionにtype=payment側"
        f"{point_in_payment}件、type=charge側{point_in_charge}件出現。"
        f"pointを含む予約{point_bookings_checked}件のうち{point_matches_price}件で"
        "charge合計=price（pointの有無に関わらずroom chargeが既に全額計上済み）。"
        + ("よって現状 point は price に既に含まれており、追加加算すると二重計上になるため"
           "beds24_point_revenue_included=0固定（status=point_already_included_in_price）。"
           "将来type=chargeにpoint起因の追加行が見つかった場合のみ自動加算する。"
           if point_status == "point_already_included_in_price" else ""),
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
        "coupon_candidate_token_hits_in_descriptions": dict(coupon_hits),
        "point_candidate_token_hits_in_descriptions": dict(point_hits),
        "other_candidate_token_hits_in_descriptions": dict(other_hits),
        "candidate_fields": {
            "cancel_status": CANCEL_CANDIDATE_KEYS,
            "point_amount": ["invoiceItems[].lineTotal (description に point/ポイント等を含む行)"],
            "point_invoice_items": ["invoiceItems (type=payment, description~point)"],
            "coupon_discount_amount": ["invoiceItems[].lineTotal (description に coupon/クーポン等を含む行)"],
            "coupon_invoice_items": ["invoiceItems (type=payment, description~coupon)"],
            "invoice_items": ["invoiceItems"],
            "payment_items": ["invoiceItems (type=payment)"],
            "room_price": ["price"],
            "total_price": ["price"],
        },
        "selected_fields": {
            "cancel_status": "status",
            "point_amount": ("invoiceItems[].lineTotal (type=charge限定。実データでは0件→"
                             "point_already_included_in_price)"),
            "point_invoice_items": "invoiceItems (type=payment, description='point')",
            "coupon_discount_amount": "invoiceItems[].lineTotal (type=payment, description='coupon')",
        },
        "classification": {
            "coupon": "direct_discount_not_revenue",
            "point": "revenue_addition_candidate",
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
