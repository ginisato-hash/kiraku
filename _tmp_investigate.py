"""ONE-TIME diagnostic (temporary, removed after investigation).

Investigates two real-Beds24-data questions for the cleaning sheet feature:
  1. Do Booking.com bookings with children carry per-child AGE data anywhere
     (top-level fields, infoItems, guestComments)?
  2. What is the correct "outstanding onsite balance" calculation, verified
     against real invoiceItems (charge vs payment, onsite-payment marker)?

Prints ONLY: field names/structure, invoiceItem type/description/lineTotal
(system-generated labels, not guest-typed text), small integer counts/ages.
NEVER prints: guest name, email, phone, address, or the API token.
"""
from __future__ import annotations

import calendar
import json
import sys

import requests

from yuge_finance.api.beds24_client import Beds24Client
from yuge_finance.accounting.beds24_revenue_logic import ONSITE_PAYMENT_TOKENS
from yuge_finance.reports.beds24_field_probe import _collect_keys, PII_KEYS

PII_LOWER = {k.lower() for k in PII_KEYS} | {"guestname", "firstname", "lastname"}


def fetch_with_info_items(client: Beds24Client, month: str) -> list[dict]:
    headers = client._headers()
    year, mon = (int(x) for x in month.split("-"))
    last = calendar.monthrange(year, mon)[1]
    params = {
        "arrivalFrom": f"{month}-01",
        "arrivalTo": f"{month}-{last:02d}",
        "includeInvoiceItems": "true",
        "includeInfoItems": "true",
        "status": ["new", "request", "confirmed", "cancelled", "black"],
    }
    if client.property_ids:
        params["propertyId"] = client.property_ids
    out = []
    page = 1
    while True:
        params["page"] = page
        resp = requests.get(f"{client.base}/bookings", headers=headers, params=params, timeout=30)
        if resp.status_code != 200:
            print(f"  [warn] {month} page {page}: HTTP {resp.status_code}")
            break
        body = resp.json()
        data = body.get("data", body if isinstance(body, list) else [])
        if not data:
            break
        out.extend(data)
        if len(data) < 100:
            break
        page += 1
    return out


def redact(d: dict) -> dict:
    return {k: v for k, v in d.items() if k.lower() not in PII_LOWER and not isinstance(v, (dict, list))}


def main():
    months = sys.argv[1:] or []
    if not months:
        print("usage: _tmp_investigate.py YYYY-MM [YYYY-MM ...]")
        sys.exit(1)

    client = Beds24Client()
    all_bookings = []
    for m in months:
        b = fetch_with_info_items(client, m)
        print(f"month={m} bookings_fetched={len(b)}")
        all_bookings.extend(b)

    print(f"\n=== TOTAL bookings fetched across {len(months)} months: {len(all_bookings)} ===\n")

    # ---------------- 1. Booking.com child-age investigation ----------------
    print("\n" + "=" * 60)
    print("SECTION 1: Booking.com child-age field investigation")
    print("=" * 60)

    def is_booking_com(b: dict) -> bool:
        ref = str(b.get("refererEditable") or b.get("channel") or b.get("apiSource") or "").lower()
        return "booking" in ref and "kiraku" not in ref

    bcom_with_children = [b for b in all_bookings if is_booking_com(b) and float(b.get("numChild") or 0) > 0]
    print(f"Booking.com bookings with numChild > 0: {len(bcom_with_children)}")

    all_keys = set()
    for b in bcom_with_children:
        all_keys |= _collect_keys(b)
    age_like_keys = sorted(k for k in all_keys if "age" in k.lower())
    child_like_keys = sorted(k for k in all_keys if "child" in k.lower())
    print(f"Keys (any depth) containing 'age': {age_like_keys}")
    print(f"Keys (any depth) containing 'child': {child_like_keys}")

    print("\n--- infoItems structure across Booking.com+children bookings ---")
    info_item_codes = {}
    for b in bcom_with_children:
        for item in (b.get("infoItems") or []):
            code = item.get("code") or item.get("type") or "?"
            info_item_codes[code] = info_item_codes.get(code, 0) + 1
    print(f"infoItems code/type distribution: {info_item_codes}")

    print("\n--- guestComments presence (structure only, content redacted) ---")
    gc_present = sum(1 for b in bcom_with_children if b.get("guestComments"))
    gc_types = {type(b.get("guestComments")).__name__ for b in bcom_with_children if b.get("guestComments")}
    print(f"guestComments non-empty: {gc_present}/{len(bcom_with_children)}, types seen: {gc_types}")

    print("\n--- per-booking redacted top-level sample (first 5 Booking.com+children) ---")
    for b in bcom_with_children[:5]:
        print(json.dumps(redact(b), ensure_ascii=False, indent=2, default=str))
        print(f"  numAdult={b.get('numAdult')} numChild={b.get('numChild')}")
        print(f"  infoItems count={len(b.get('infoItems') or [])}")
        for it in (b.get("infoItems") or [])[:10]:
            print(f"    infoItem: {redact(it)}")
        print("  ---")

    # ---------------- 2. Onsite payment balance investigation ----------------
    print("\n" + "=" * 60)
    print("SECTION 2: Onsite payment / outstanding balance investigation")
    print("=" * 60)

    def has_onsite_marker(b: dict) -> bool:
        for item in (b.get("invoiceItems") or []):
            desc = str(item.get("description") or "").lower()
            if any(tok.lower() in desc for tok in ONSITE_PAYMENT_TOKENS):
                return True
        return False

    onsite_bookings = [b for b in all_bookings if has_onsite_marker(b)]
    print(f"Bookings with an onsite-payment marker (any status): {len(onsite_bookings)}")

    non_cancelled_onsite = [b for b in onsite_bookings if str(b.get("status", "")).lower() != "cancelled"]
    print(f"...of which non-cancelled: {len(non_cancelled_onsite)}")

    balance_field_candidates = set()
    for b in onsite_bookings:
        balance_field_candidates |= {k for k in b.keys() if
                                     "balance" in k.lower() or "paid" in k.lower() or "due" in k.lower()}
    print(f"Top-level candidate balance/paid/due fields on onsite bookings: {sorted(balance_field_candidates)}")

    print("\n--- per-booking invoiceItems breakdown (first 10 non-cancelled onsite bookings) ---")
    for b in non_cancelled_onsite[:10]:
        price = b.get("price")
        items = b.get("invoiceItems") or []
        charge_sum = sum(float(it.get("lineTotal", it.get("amount", 0)) or 0) for it in items if it.get("type") == "charge")
        payment_sum_all = sum(float(it.get("lineTotal", it.get("amount", 0)) or 0) for it in items if it.get("type") == "payment")
        onsite_marker_items = [it for it in items if any(
            tok.lower() in str(it.get("description") or "").lower() for tok in ONSITE_PAYMENT_TOKENS)]
        payment_sum_excl_onsite_marker = sum(
            float(it.get("lineTotal", it.get("amount", 0)) or 0)
            for it in items if it.get("type") == "payment" and it not in onsite_marker_items)
        print(f"booking_id={b.get('id')} status={b.get('status')} price={price} "
              f"charge_sum={charge_sum} payment_sum_all={payment_sum_all} "
              f"payment_sum_excl_onsite_marker={payment_sum_excl_onsite_marker} "
              f"outstanding_candidate(charge-payment_excl_marker)={charge_sum - payment_sum_excl_onsite_marker}")
        for it in items:
            print(f"    item: type={it.get('type')} desc={it.get('description')!r} "
                  f"lineTotal={it.get('lineTotal')} amount={it.get('amount')} "
                  f"qty={it.get('qty')} status={it.get('status')}")
        for extra_field in sorted(balance_field_candidates):
            if extra_field in b:
                print(f"    top-level {extra_field}={b.get(extra_field)}")
        print("  ---")

    print("\n=== END DIAGNOSTIC ===")


if __name__ == "__main__":
    main()
