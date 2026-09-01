"""ONE-TIME diagnostic (temporary; removed after the investigation).

Anchor booking: Beds24 booking 91673623 (Booking.com, numAdult=2, numChild=1).
The Beds24 UI shows, under 「ゲストからのコメント」:  "1 child aged 10".

Goal: prove WHICH API request + WHICH booking record + WHICH field carries that
text, instead of concluding "the data does not exist" (the previous round's
wrong conclusion).

Privacy rules enforced here:
  * The Beds24 token is never printed.
  * Identity fields (name/email/phone/address/...) are never read or printed.
  * Free-text fields are NEVER printed in full. For every string field only
    (field path, length, matched?) is printed; content is printed ONLY as a
    bounded window around a child-age metadata match.
"""
from __future__ import annotations

import re
import sys

import requests

from yuge_finance.api.beds24_client import Beds24Client

BOOKING_ID = "91673623"

# Never scanned, never printed. (Deliberately does NOT include comments/notes/
# message/groupNote — excluding those is exactly what made the previous
# diagnostic blind.)
IDENTITY_KEYS = {
    "firstname", "lastname", "title", "email", "phone", "mobile", "fax",
    "address", "city", "state", "postcode", "country", "country2", "company",
    "guestname", "pcibookingtoken", "stripetoken",
}

# Bounded child-age metadata match only.
CHILD_RE = re.compile(r"aged?\b|child(?:ren)?\b", re.IGNORECASE)
WINDOW = 40

VARIANTS = [
    ("plain", {}),
    ("infoItems", {"includeInfoItems": "true"}),
    ("infoItemsConverted", {"includeInfoItems": "true", "includeInfoItemsConverted": "true"}),
    ("bookingGroup", {"includeBookingGroup": "true"}),
    ("guests", {"includeGuests": "true"}),
    ("invoiceItems", {"includeInvoiceItems": "true"}),
    ("all", {"includeInfoItems": "true", "includeInfoItemsConverted": "true",
             "includeBookingGroup": "true", "includeInvoiceItems": "true",
             "includeGuests": "true"}),
]


def walk_strings(node, path, out):
    """Collect (path, value) for every string leaf, skipping identity keys."""
    if isinstance(node, str):
        out.append((path, node))
    elif isinstance(node, dict):
        for k, v in node.items():
            if str(k).lower() in IDENTITY_KEYS:
                continue
            walk_strings(v, f"{path}.{k}" if path else str(k), out)
    elif isinstance(node, list):
        for i, item in enumerate(node[:50]):
            walk_strings(item, f"{path}[{i}]", out)


def report_record(label, rec):
    bid = rec.get("id")
    print(f"  record booking_id={bid} status={rec.get('status')} "
          f"numAdult={rec.get('numAdult')} numChild={rec.get('numChild')} "
          f"referer_editable={rec.get('refererEditable')!r} apiSource={rec.get('apiSource')!r}")
    print(f"  key inventory (names only): {sorted(rec.keys())}")

    # group/master/linked structure
    group_keys = [k for k in rec.keys()
                  if re.search(r"group|master|linked|sub", str(k), re.IGNORECASE)]
    for k in group_keys:
        v = rec.get(k)
        if isinstance(v, str):
            print(f"  group-ish key {k}: type=str len={len(v)} (content withheld)")
        else:
            print(f"  group-ish key {k}: {v!r}")

    leaves = []
    walk_strings(rec, "", leaves)
    nonempty = [(p, v) for p, v in leaves if v.strip()]
    print(f"  string leaves: total={len(leaves)} nonempty={len(nonempty)}")
    for p, v in nonempty:
        matches = list(CHILD_RE.finditer(v))
        flag = "MATCH" if matches else "-"
        print(f"    field={p} len={len(v)} {flag}")
        for m in matches:
            s = max(0, m.start() - WINDOW)
            e = min(len(v), m.end() + WINDOW)
            print(f"      child_pattern_window={v[s:e]!r}")
    return [p for p, v in nonempty if CHILD_RE.search(v)]


def main():
    client = Beds24Client()
    headers = client._headers()
    base = client.base

    print("=== ANCHOR BOOKING DIAGNOSTIC ===")
    print(f"booking_id={BOOKING_ID}")

    matched_fields_by_variant = {}
    group_ids = set()

    for label, extra in VARIANTS:
        params = {"id": BOOKING_ID}
        params.update(extra)
        # status must be explicit or some statuses are filtered out
        params["status"] = ["new", "request", "confirmed", "cancelled", "black"]
        resp = requests.get(f"{base}/bookings", headers=headers, params=params, timeout=30)
        print(f"\n--- variant={label} params={sorted(extra.keys())} http={resp.status_code}")
        if resp.status_code != 200:
            print(f"  ERROR body (first 200 chars, no token): {resp.text[:200]!r}")
            continue
        body = resp.json()
        if isinstance(body, dict):
            print(f"  envelope keys: {sorted(body.keys())} success={body.get('success')}")
            data = body.get("data", [])
        else:
            data = body
        print(f"  records returned: {len(data)}")
        hits = []
        for rec in data:
            hits += report_record(label, rec)
            bg = rec.get("bookingGroup")
            if isinstance(bg, dict):
                print(f"  bookingGroup detail: {bg!r}")
                for k in ("master", "masterId", "ids", "bookingIds", "roomIds"):
                    v = bg.get(k)
                    if isinstance(v, list):
                        group_ids.update(str(x) for x in v)
                    elif v not in (None, ""):
                        group_ids.add(str(v))
        matched_fields_by_variant[label] = hits

    print("\n=== child-pattern matching fields per variant ===")
    for label, hits in matched_fields_by_variant.items():
        print(f"  {label}: {hits}")

    print(f"\n=== related group/master booking ids discovered: {sorted(group_ids)} ===")
    for gid in sorted(group_ids - {BOOKING_ID}):
        resp = requests.get(f"{base}/bookings", headers=headers,
                            params={"id": gid, "includeInfoItems": "true",
                                    "includeBookingGroup": "true",
                                    "status": ["new", "request", "confirmed",
                                               "cancelled", "black"]},
                            timeout=30)
        print(f"\n--- related booking {gid} http={resp.status_code}")
        if resp.status_code == 200:
            body = resp.json()
            data = body.get("data", []) if isinstance(body, dict) else body
            for rec in data:
                report_record(f"group:{gid}", rec)

    # --- what the NORMAL 15-min refresh path currently retrieves ---
    print("\n=== NORMAL fetch_raw('2026-08') path check ===")
    raw = client.fetch_raw("2026-08")
    print(f"  bookings fetched: {len(raw)}")
    target = [b for b in raw if str(b.get("id")) == BOOKING_ID]
    print(f"  anchor booking present in normal fetch: {bool(target)}")
    for rec in target:
        hits = report_record("normal_fetch", rec)
        print(f"  normal_fetch child-pattern fields: {hits}")

    print("\n=== END DIAGNOSTIC ===")


if __name__ == "__main__":
    sys.exit(main())
