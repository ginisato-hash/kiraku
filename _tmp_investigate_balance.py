"""ONE-TIME diagnostic (temporary, removed after investigation).

Investigates Beds24's official Invoice Balance concept for the "amount due
at property" cleaning-sheet feature:
  1. Does GET /bookings/invoices exist, and what shape does it return?
  2. Does GET /bookings (with includeInvoiceItems=true) carry a direct
     booking-level balance field anywhere (top-level or nested)?
  3. What info-item codes appear across real bookings (channel-collect
     signals: BOOKINGCOMVIRTCARD, BOOKINGCOMBANKTRANS, EXPEDIACOLLECT,
     AGODACOLLECT, VIRTUALCARD, HOTELCOLLECT, and anything else)?
  4. For real bookings across several channels/payment situations, dump
     invoiceItems structure (type/description/lineTotal/qty/status) plus
     any balance-like field found, so a proposed calculation can be
     verified against Beds24's own computed value before implementation.

Prints ONLY: booking ids, field names/structure, invoiceItem
type/description/lineTotal (system-generated labels), info-item codes,
and small numeric values. NEVER prints: guest name, email, phone, address,
or the API token.
"""
from __future__ import annotations

import json
import sys

import requests

from yuge_finance.api.beds24_client import Beds24Client
from yuge_finance.reports.beds24_field_probe import PII_KEYS

PII_LOWER = {k.lower() for k in PII_KEYS} | {"guestname", "firstname", "lastname"}


def redact(d: dict) -> dict:
    return {k: v for k, v in d.items() if k.lower() not in PII_LOWER and not isinstance(v, (dict, list))}


def main():
    client = Beds24Client()
    headers = client._headers()

    print("=" * 60)
    print("STEP 1: GET /bookings/invoices existence + shape")
    print("=" * 60)
    resp = requests.get(f"{client.base}/bookings/invoices", headers=headers,
                        params={"propertyId": client.property_ids}, timeout=30)
    print(f"status={resp.status_code}")
    if resp.status_code == 200:
        body = resp.json()
        print(f"top-level type: {type(body).__name__}")
        if isinstance(body, dict):
            print(f"top-level keys: {sorted(body.keys())}")
            data = body.get("data", [])
        else:
            data = body
        print(f"item count: {len(data) if isinstance(data, list) else 'n/a'}")
        if isinstance(data, list) and data:
            print("first item (PII-redacted, top-level only):")
            print(json.dumps(redact(data[0]) if isinstance(data[0], dict) else data[0],
                             ensure_ascii=False, indent=2, default=str))
            if isinstance(data[0], dict):
                print(f"first item ALL keys: {sorted(data[0].keys())}")
    else:
        print(f"body: {resp.text[:500]}")

    print("\n" + "=" * 60)
    print("STEP 2: GET /bookings with includeInvoiceItems + includeInfoItems,"
          " look for a direct balance field + info-item code distribution")
    print("=" * 60)
    months = sys.argv[1:] or ["2026-06", "2026-07", "2026-08"]
    all_bookings = []
    for month in months:
        import calendar
        year, mon = (int(x) for x in month.split("-"))
        last = calendar.monthrange(year, mon)[1]
        params = {
            "arrivalFrom": f"{month}-01", "arrivalTo": f"{month}-{last:02d}",
            "includeInvoiceItems": "true", "includeInfoItems": "true",
            "status": ["new", "request", "confirmed", "cancelled", "black"],
            "propertyId": client.property_ids,
        }
        page = 1
        while True:
            params["page"] = page
            r = requests.get(f"{client.base}/bookings", headers=headers, params=params, timeout=30)
            if r.status_code != 200:
                print(f"  [warn] {month} page {page}: HTTP {r.status_code}")
                break
            b = r.json()
            data = b.get("data", b if isinstance(b, list) else [])
            if not data:
                break
            all_bookings.extend(data)
            if len(data) < 100:
                break
            page += 1
    print(f"total bookings fetched: {len(all_bookings)}")

    balance_field_candidates = set()
    for b in all_bookings:
        for k in b.keys():
            kl = k.lower()
            if "balance" in kl or "owing" in kl or "due" in kl:
                balance_field_candidates.add(k)
    print(f"top-level candidate balance-like fields across all bookings: {sorted(balance_field_candidates)}")

    info_code_counts = {}
    for b in all_bookings:
        for item in (b.get("infoItems") or []):
            code = item.get("code") or item.get("type") or "?"
            info_code_counts[code] = info_code_counts.get(code, 0) + 1
    print(f"\ninfoItems code distribution across all bookings: {info_code_counts}")

    channel_collect_tokens = ["VIRTCARD", "VIRTUALCARD", "BANKTRANS", "COLLECT", "HOTELCOLLECT"]
    print("\ninfo codes matching channel-collect-ish tokens:")
    for code in sorted(info_code_counts):
        if any(tok in code.upper() for tok in channel_collect_tokens):
            print(f"  {code}: {info_code_counts[code]} occurrences")

    print("\n" + "=" * 60)
    print("STEP 3: per-booking invoiceItems + balance-field dump for a spread of real cases")
    print("=" * 60)

    def is_booking_com(b):
        ref = str(b.get("refererEditable") or b.get("channel") or "").lower()
        return "booking" in ref and "kiraku" not in ref

    def has_code(b, code):
        return any((it.get("code") or "").upper() == code for it in (b.get("infoItems") or []))

    def has_onsite_marker(b):
        toks = ("現地決済", "現地払い", "現地支払", "施設払い", "宿払い", "pay at property", "hotel collect", "onsite payment")
        return any(any(t.lower() in str(it.get("description") or "").lower() for t in toks)
                  for it in (b.get("invoiceItems") or []))

    non_cancelled = [b for b in all_bookings if str(b.get("status", "")).lower() != "cancelled"]

    buckets = {
        "onsite_marker": [b for b in non_cancelled if has_onsite_marker(b)][:3],
        "booking_com_virtcard": [b for b in non_cancelled if has_code(b, "BOOKINGCOMVIRTCARD")][:3],
        "booking_com_banktrans": [b for b in non_cancelled if has_code(b, "BOOKINGCOMBANKTRANS")][:3],
        "hotelcollect": [b for b in non_cancelled if has_code(b, "HOTELCOLLECT")][:3],
        "other_booking_com": [b for b in non_cancelled if is_booking_com(b)
                              and not has_code(b, "BOOKINGCOMVIRTCARD")
                              and not has_code(b, "BOOKINGCOMBANKTRANS")][:3],
        "direct": [b for b in non_cancelled if not is_booking_com(b)][:3],
    }

    for label, bookings in buckets.items():
        print(f"\n--- bucket: {label} ({len(bookings)} sampled) ---")
        for b in bookings:
            price = b.get("price")
            items = b.get("invoiceItems") or []
            charge_sum = sum(float(it.get("lineTotal", it.get("amount", 0)) or 0) for it in items if it.get("type") == "charge")
            payment_sum = sum(float(it.get("lineTotal", it.get("amount", 0)) or 0) for it in items if it.get("type") == "payment")
            balance_fields = {k: b.get(k) for k in balance_field_candidates if k in b}
            print(f"booking_id={b.get('id')} status={b.get('status')} price={price} "
                  f"charge_sum={charge_sum} payment_sum={payment_sum} "
                  f"naive_balance(charge-payment)={charge_sum - payment_sum} "
                  f"direct_balance_fields={balance_fields}")
            for it in items:
                print(f"    item: type={it.get('type')} desc={it.get('description')!r} "
                      f"lineTotal={it.get('lineTotal')} amount={it.get('amount')} "
                      f"qty={it.get('qty')} status={it.get('status')}")
            for it in (b.get("infoItems") or []):
                print(f"    infoItem: code={it.get('code')} type={it.get('type')}")
            print("  ---")

    print("\n=== END DIAGNOSTIC ===")


if __name__ == "__main__":
    main()
