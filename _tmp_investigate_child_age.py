"""ONE-TIME diagnostic (temporary, removed after investigation).

Investigates real Beds24 data for Booking.com bookings with children, to
find the actual child-age text pattern(s) so a parser can be built against
real evidence (not guessed). Broadened after round 1 found guestComments
empty for all 10 real matches: this round scans ALL string-valued fields
(top-level and inside infoItems) on those bookings for an "aged" match, not
just guestComments.

Privacy note: does NOT print full raw field values (could leak guest-typed
PII in a free-text field). Only prints a small bounded snippet around each
"aged" match, the field name it was found in, and a length count of any
other text (never its content). Booking IDs are printed for cross-
reference; guest name/email/phone/address are never read.
"""
from __future__ import annotations

import calendar
import re
import sys

import requests

from yuge_finance.api.beds24_client import Beds24Client
from yuge_finance.reports.beds24_field_probe import PII_KEYS

PII_LOWER = {k.lower() for k in PII_KEYS} | {"guestname", "firstname", "lastname"}
AGE_RE = re.compile(r"aged", re.IGNORECASE)


def scan_value(field_path, value, results):
    if isinstance(value, str):
        for m in AGE_RE.finditer(value):
            start = max(0, m.start() - 25)
            end = min(len(value), m.end() + 15)
            results.append((field_path, value[start:end], len(value)))
    elif isinstance(value, dict):
        for k, v in value.items():
            if k.lower() in PII_LOWER:
                continue
            scan_value(f"{field_path}.{k}", v, results)
    elif isinstance(value, list):
        for i, item in enumerate(value[:20]):
            scan_value(f"{field_path}[{i}]", item, results)


def main():
    client = Beds24Client()
    headers = client._headers()
    months = sys.argv[1:] or ["2026-01", "2026-02", "2026-03", "2026-04", "2026-05",
                              "2026-06", "2026-07", "2026-08", "2026-09"]

    all_bookings = []
    for month in months:
        year, mon = (int(x) for x in month.split("-"))
        last = calendar.monthrange(year, mon)[1]
        params = {
            "arrivalFrom": f"{month}-01", "arrivalTo": f"{month}-{last:02d}",
            "includeInvoiceItems": "false", "includeInfoItems": "true",
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

    def is_booking_com(b):
        ref = str(b.get("refererEditable") or b.get("channel") or "").lower()
        return "booking" in ref and "kiraku" not in ref

    bcom_with_children = [b for b in all_bookings
                          if is_booking_com(b) and float(b.get("numChild") or 0) > 0]
    print(f"\nBooking.com bookings with numChild > 0: {len(bcom_with_children)}")

    print("\n--- ALL top-level + infoItems string fields scanned for 'aged' (not just guestComments) ---")
    any_match_anywhere = False
    for b in bcom_with_children:
        print(f"booking_id={b.get('id')} status={b.get('status')} "
              f"numAdult={b.get('numAdult')} numChild={b.get('numChild')}")
        results = []
        for k, v in b.items():
            if k.lower() in PII_LOWER:
                continue
            scan_value(k, v, results)
        if not results:
            print("  no 'aged' match in any field")
        for field_path, snippet, total_len in results:
            any_match_anywhere = True
            print(f"  MATCH field={field_path} total_len={total_len} snippet={snippet!r}")
        print("  ---")

    print(f"\nany 'aged' match found anywhere across all {len(bcom_with_children)} bookings: {any_match_anywhere}")

    # Also report guestComments length distribution (structure only, not content)
    lengths = [len(str(b.get("guestComments") or "")) for b in bcom_with_children]
    print(f"guestComments lengths across these bookings: {lengths}")

    print("\n=== END DIAGNOSTIC ===")


if __name__ == "__main__":
    main()
