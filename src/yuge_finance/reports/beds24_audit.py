"""Beds24 売上フィールド監査（喜らく単体）。

会計処理を一切変更せず、対象月のBeds24 raw JSONを解析して
「どの予約のどの金額フィールドを売上として使っているか」を完全可視化する。
出力は data/output/<month>/debug/ 。
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Dict, List

from .. import config, csvio
from ..api.beds24_client import normalize_booking

EXCLUDE_STATUSES = ["cancelled", "canceled", "black"]


def _num(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _invoice_by_type(b: dict) -> Dict[str, float]:
    agg: Dict[str, float] = defaultdict(float)
    for it in b.get("invoiceItems", []) or []:
        agg[str(it.get("type", "unknown"))] += _num(it.get("lineTotal"))
    return agg


def _exclusion(b: dict, month: str) -> str:
    status = str(b.get("status", "")).lower()
    reasons = []
    if status in EXCLUDE_STATUSES:
        reasons.append(f"status={status}(除外)")
    arrival = str(b.get("arrival", ""))[:7]
    if arrival != month:
        reasons.append(f"checkin={arrival}(対象月外)")
    if _num(b.get("price")) <= 0:
        reasons.append("price<=0")
    return " / ".join(reasons)


def raw_path(month: str) -> Path:
    return config.DATA_DIR / "raw" / "beds24" / month / f"{month}.json"


def build_audit_rows(raw: List[dict], month: str) -> List[dict]:
    rows = []
    for b in raw:
        inv = _invoice_by_type(b)
        rec = normalize_booking(b, config.property_name())  # 現在の採用ロジック
        excl = _exclusion(b, month)
        rows.append({
            "booking_id": b.get("id"),
            "status": b.get("status"),
            "subStatus": b.get("subStatus"),
            "channel_field": b.get("channel"),          # 不安定（direct/booking）
            "refererEditable": b.get("refererEditable"),  # 実OTA名
            "apiSource": b.get("apiSource"),
            "guest_name": (f"{b.get('firstName','')} {b.get('lastName','')}").strip(),
            "booking_date": str(b.get("bookingTime", ""))[:10],
            "checkin_date": b.get("arrival"),
            "checkout_date": b.get("departure"),
            "stay_nights": rec.stay_nights,
            "roomId": b.get("roomId"),
            "rooms_roomQty": b.get("roomQty"),
            "guests": rec.guests,
            # --- API上の全金額候補フィールド ---
            "api_price": _num(b.get("price")),
            "api_tax": _num(b.get("tax")),
            "api_commission": _num(b.get("commission")),
            "api_deposit": _num(b.get("deposit")),
            "inv_charge": round(inv.get("charge", 0.0), 2),
            "inv_payment": round(inv.get("payment", 0.0), 2),
            "inv_other": round(sum(v for k, v in inv.items()
                                   if k not in ("charge", "payment")), 2),
            # --- 現在採用している値 ---
            "current_gross_revenue": rec.gross_revenue,
            "current_net_revenue": rec.net_revenue,
            "current_ota_commission": rec.ota_commission,
            # --- 判定 ---
            "売上計上対象": "除外" if excl else "対象",
            "除外理由": excl,
        })
    return rows


def write_all(month: str, out_dir: Path = None) -> Dict:
    rp = raw_path(month)
    if not rp.exists():
        raise FileNotFoundError(
            f"Beds24 raw JSONが見つかりません: {rp}  先に `yuge-finance fetch-beds24 --month {month}` を実行してください。")
    raw = json.loads(rp.read_text(encoding="utf-8"))
    out_dir = out_dir or (config.output_dir(month) / "debug")
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = build_audit_rows(raw, month)

    # 1. 予約別 監査CSV
    cols = list(rows[0].keys()) if rows else ["booking_id"]
    csvio.write_rows(out_dir / "beds24_revenue_field_audit.csv", rows, cols)

    # 2. status別サマリ
    by_status = defaultdict(lambda: {"count": 0, "price": 0.0, "commission": 0.0})
    for r in rows:
        s = by_status[str(r["status"]).lower()]
        s["count"] += 1
        s["price"] += r["api_price"]
        s["commission"] += r["api_commission"]
    status_rows = []
    for st, agg in sorted(by_status.items()):
        status_rows.append({"status": st, "count": agg["count"],
                            "sum_price": round(agg["price"]),
                            "sum_commission": round(agg["commission"])})
    # 集計ライン
    def _sum(pred):
        return round(sum(r["api_price"] for r in rows if pred(r)))
    conf = _sum(lambda r: str(r["status"]).lower() == "confirmed")
    confnew = _sum(lambda r: str(r["status"]).lower() in ("confirmed", "new"))
    cancelled = _sum(lambda r: str(r["status"]).lower() in EXCLUDE_STATUSES)
    all_total = _sum(lambda r: True)
    status_rows += [
        {"status": "—— 集計 ——", "count": "", "sum_price": "", "sum_commission": ""},
        {"status": "全status合計(=Beds24画面)", "count": "", "sum_price": all_total, "sum_commission": ""},
        {"status": "認識売上(キャンセル除外)", "count": "", "sum_price": confnew, "sum_commission": ""},
        {"status": "  内 confirmedのみ", "count": "", "sum_price": conf, "sum_commission": ""},
        {"status": "キャンセル等(計上除外/分析保存)", "count": "", "sum_price": cancelled, "sum_commission": ""},
    ]
    csvio.write_rows(out_dir / "beds24_revenue_summary_by_status.csv", status_rows,
                     ["status", "count", "sum_price", "sum_commission"])

    # 3. channel別サマリ（実OTA = refererEditable）。channel_fieldも併記。
    by_ch = defaultdict(lambda: {"count": 0, "price": 0.0, "conf": 0.0, "confnew": 0.0})
    for r in rows:
        key = r["refererEditable"] or r["channel_field"] or "(不明)"
        c = by_ch[key]
        c["count"] += 1
        c["price"] += r["api_price"]
        st = str(r["status"]).lower()
        if st == "confirmed":
            c["conf"] += r["api_price"]
        if st in ("confirmed", "new"):
            c["confnew"] += r["api_price"]
    ch_rows = [{"channel(refererEditable)": k, "count": v["count"],
                "sum_price_all": round(v["price"]),
                "sum_price_confirmed_only": round(v["conf"]),
                "sum_price_confirmed_plus_new": round(v["confnew"])}
               for k, v in sorted(by_ch.items())]
    csvio.write_rows(out_dir / "beds24_revenue_summary_by_channel.csv", ch_rows,
                     ["channel(refererEditable)", "count", "sum_price_all",
                      "sum_price_confirmed_only", "sum_price_confirmed_plus_new"])

    # 4. raw フィールドキー インベントリ
    top_keys = set()
    inv_keys = set()
    inv_types = defaultdict(int)
    inv_subtypes = defaultdict(int)
    for b in raw:
        top_keys |= set(b.keys())
        for it in b.get("invoiceItems", []) or []:
            inv_keys |= set(it.keys())
            inv_types[str(it.get("type"))] += 1
            inv_subtypes[f"{it.get('type')}/{it.get('subType')}/{it.get('description','')[:20]}"] += 1
    inventory = {
        "month": month,
        "booking_count": len(raw),
        "top_level_keys": sorted(top_keys),
        "money_candidate_fields": ["price", "tax", "commission", "deposit",
                                   "invoiceItems[].lineTotal", "invoiceItems[].amount"],
        "current_logic": {
            "gross_revenue": "price → totalPrice → gross_revenue の最初の非空値",
            "ota_commission": "commission → ota_commission",
            "net_revenue": "net_revenue指定が無ければ gross - commission",
            "multiplication": "泊数・部屋数の乗算なし（priceは総額をそのまま採用）",
            "channel": "channel → referer → apiSource（※channelは不安定）",
        },
        "invoice_item_keys": sorted(inv_keys),
        "invoice_item_type_counts": dict(inv_types),
        "invoice_item_subtype_samples": dict(sorted(inv_subtypes.items())),
        "status_counts": {st: agg["count"] for st, agg in sorted(by_status.items())},
        "totals": {"sum_price_all": round(sum(r["api_price"] for r in rows)),
                   "sum_price_confirmed_only": conf,
                   "sum_price_confirmed_plus_new": confnew,
                   "sum_commission": round(sum(r["api_commission"] for r in rows))},
    }
    (out_dir / "beds24_raw_field_keys.json").write_text(
        json.dumps(inventory, ensure_ascii=False, indent=2), encoding="utf-8")

    return {"out_dir": str(out_dir), "bookings": len(raw),
            "sum_price_all": inventory["totals"]["sum_price_all"],
            "sum_price_confirmed_only": conf,
            "sum_price_confirmed_plus_new": confnew}
