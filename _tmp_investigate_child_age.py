"""ONE-TIME diagnostic (temporary, removed after investigation).

Investigates real Beds24 guestComments content for Booking.com bookings
with children, to find the actual child-age text pattern(s) so a parser
can be built against real evidence (not guessed).

Privacy note: guestComments can mix Beds24/Booking.com system-generated
child-age metadata with a guest-authored free-text message. This script
does NOT print the full raw comment (that could leak guest-typed PII —
contact details, names written in a note, etc.). It only prints a small
bounded snippet around a match for the word "aged" (the pattern the user
already confirmed exists), plus booleans describing whether other text is
also present in the comment (without printing that other text). Booking
IDs are printed for cross-reference; guest name/email/phone/address are
never read.
"""
from __future__ import annotations

import re
import sys

import requests

from yuge_finance.api.beds24_client import Beds24Client


def main():
    client = Beds24Client()
    headers = client._headers()
    months = sys.argv[1:] or ["2026-04", "2026-05", "2026-06", "2026-07", "2026-08", "2026-09"]

    all_bookings = []
    for month in months:
        import calendar
        year, mon = (int(x) for x in month.split("-"))
        last = calendar.monthrange(year, mon)[1]
        params = {
            "arrivalFrom": f"{month}-01", "arrivalTo": f"{month}-{last:02d}",
            "includeInvoiceItems": "false", "includeInfoItems": "false",
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

    print("\n--- redacted guestComments scan (booking_id + numAdult/numChild + bounded snippet) ---")
    age_word_re = re.compile(r"aged", re.IGNORECASE)
    for b in bcom_with_children:
        comments = str(b.get("guestComments") or "")
        print(f"booking_id={b.get('id')} status={b.get('status')} "
              f"numAdult={b.get('numAdult')} numChild={b.get('numChild')} "
              f"comment_length={len(comments)}")
        matches = list(age_word_re.finditer(comments))
        if not matches:
            print("  no 'aged' match found in guestComments")
        for m in matches:
            start = max(0, m.start() - 25)
            end = min(len(comments), m.end() + 15)
            snippet = comments[start:end]
            print(f"  snippet around 'aged' match: {snippet!r}")
        # report only whether there is text OUTSIDE all matched windows, not what it is
        covered = set()
        for m in matches:
            covered.update(range(max(0, m.start() - 25), min(len(comments), m.end() + 15)))
        other_text_len = len(comments) - len(covered)
        print(f"  characters outside the snippet windows (not printed): {other_text_len}")
        print("  ---")

    print("\n=== END DIAGNOSTIC ===")


if __name__ == "__main__":
    main()
