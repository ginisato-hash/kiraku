"""ONE-TIME diagnostic round 3 (temporary; removed after the investigation).

Audits real Beds24 `comments` structure for two open items:
  (A) which OTA/system lines still survive the current extract_guest_notice()
      filter (the "Guest name: …" leak seen on the 2026-09-03 production sheet),
  (B) the real shape of the Booking.com arrival-window line, plus how many
      bookings would actually benefit from an arrival fallback (arrival window
      present in `comments` while Beds24's explicit arrivalTime is empty).

Privacy design — guest-authored free text is never printed:
  * A line of the form "<Label>: <value>" prints as 'Label: <redacted>' (the
    label is a field name, the value never appears).
  * Any other surviving line prints ONLY when its digit-masked template occurs
    in >= 3 DISTINCT bookings (machine-generated with high confidence);
    otherwise just length + sha1 prefix.
  * Arrival-window lines print in full: they are a fixed system label plus two
    clock times, and the exact format must be verified for the parser.
  * Identity fields are never read. The token is never printed.
"""
from __future__ import annotations

import hashlib
import re
from collections import defaultdict

from yuge_finance.api.beds24_client import Beds24Client
from yuge_finance.ops.extract import extract_guest_notice, guest_comment_text

MONTHS = ["2026-%02d" % m for m in range(1, 13)]

LABEL_RE = re.compile(r"^\s*([A-Za-z][A-Za-z0-9 ./&\-]{0,40})\s*:")
APPROX_ARRIVAL_RE = re.compile(r"^\s*approximate time of arrival\s*:", re.IGNORECASE)
ARRIVAL_WORD_RE = re.compile(r"arrival|arrive|チェックイン|到着", re.IGNORECASE)
GUEST_NAME_RE = re.compile(r"^\s*guest\s+name\s*:", re.IGNORECASE)


def tmpl(line: str) -> str:
    return re.sub(r"\d+", "#", line.strip())


def main():
    client = Beds24Client()
    bookings = []
    for month in MONTHS:
        bookings.extend(client.fetch_raw(month))
    print(f"total bookings fetched ({MONTHS[0]}..{MONTHS[-1]}): {len(bookings)}")

    with_comments = [b for b in bookings if guest_comment_text(b)]
    print(f"bookings with non-empty `comments`: {len(with_comments)}")

    # template -> distinct booking ids (for the >=3 print threshold)
    tmpl_bookings = defaultdict(set)
    for b in with_comments:
        for line in guest_comment_text(b).splitlines():
            if line.strip():
                tmpl_bookings[tmpl(line)].add(str(b.get("id")))

    def is_bcom(b):
        return "booking.com" in str(b.get("refererEditable") or b.get("apiSource") or "").lower()

    # ---------- (A) what survives the CURRENT filter ----------
    print("\n=== (A) lines still surviving extract_guest_notice() ===")
    surviving_labels = defaultdict(int)      # label -> distinct bookings
    surviving_other = defaultdict(int)       # template -> distinct bookings
    bookings_with_notice = 0
    for b in with_comments:
        notice = extract_guest_notice(b)
        if not notice:
            continue
        bookings_with_notice += 1
        for line in notice.splitlines():
            s = line.strip()
            if not s:
                continue
            m = LABEL_RE.match(s)
            if m:
                surviving_labels[m.group(1).strip().lower()] += 1
            else:
                surviving_other[tmpl(s)] += 1
    print(f"bookings whose guest_notice is non-empty today: {bookings_with_notice}")
    print("\n  surviving LABEL-form lines ('<Label>: <redacted>'):")
    for label, n in sorted(surviving_labels.items(), key=lambda kv: -kv[1]):
        print(f"    bookings={n:4d} label={label!r}: <redacted>")
    print("\n  surviving non-label lines:")
    for t, n in sorted(surviving_other.items(), key=lambda kv: -kv[1]):
        total = len(tmpl_bookings.get(t, ()))
        if total >= 3:
            print(f"    bookings={n:4d} (template seen in {total}) template={t!r}")
        else:
            h = hashlib.sha1(t.encode("utf-8")).hexdigest()[:8]
            print(f"    bookings={n:4d} (template seen in {total}) len={len(t)} "
                  f"ascii={t.isascii()} sha1={h} (content withheld)")

    # ---------- Guest name metadata counters ----------
    gn_all = [b for b in with_comments
              if any(GUEST_NAME_RE.match(l) for l in guest_comment_text(b).splitlines())]
    gn_bcom = [b for b in gn_all if is_bcom(b)]
    print(f"\nbookingcom_guest_name_metadata_count={len(gn_bcom)} "
          f"(all channels={len(gn_all)})")
    gn_tmpls = defaultdict(int)
    for b in gn_all:
        for l in guest_comment_text(b).splitlines():
            if GUEST_NAME_RE.match(l):
                gn_tmpls[re.sub(r":.*$", ": <redacted>", l.strip(), flags=re.S)] += 1
    print(f"  guest-name label variants: {dict(gn_tmpls)}")
    # does a guest-name line ever carry additional non-name content after it?
    print(f"  channels carrying it: "
          f"{sorted({str(b.get('refererEditable') or b.get('apiSource')) for b in gn_all})}")

    # ---------- (B) arrival window ----------
    print("\n=== (B) Booking.com arrival window in `comments` ===")
    approx_bookings = []
    approx_forms = defaultdict(int)
    for b in with_comments:
        lines = [l.strip() for l in guest_comment_text(b).splitlines() if l.strip()]
        hits = [l for l in lines if APPROX_ARRIVAL_RE.match(l)]
        if hits:
            approx_bookings.append(b)
            for l in hits:
                approx_forms[l] += 1
    print(f"bookingcom_arrival_window_count="
          f"{len([b for b in approx_bookings if is_bcom(b)])} "
          f"(all channels={len(approx_bookings)})")
    print("  exact real forms (system label + clock times only):")
    for form, n in sorted(approx_forms.items(), key=lambda kv: -kv[1]):
        print(f"    n={n:3d} {form!r}")

    # other arrival-ish lines that are NOT the approximate form
    print("\n  other arrival-ish lines (label-redacted / withheld):")
    other = defaultdict(int)
    for b in with_comments:
        for l in guest_comment_text(b).splitlines():
            s = l.strip()
            if not s or APPROX_ARRIVAL_RE.match(s) or not ARRIVAL_WORD_RE.search(s):
                continue
            m = LABEL_RE.match(s)
            key = f"{m.group(1).strip().lower()}: <redacted>" if m else \
                  (tmpl(s) if len(tmpl_bookings.get(tmpl(s), ())) >= 3
                   else f"len={len(s)} sha1={hashlib.sha1(tmpl(s).encode()).hexdigest()[:8]}")
            other[key] += 1
    for k, n in sorted(other.items(), key=lambda kv: -kv[1]):
        print(f"    n={n:3d} {k}")

    # ---------- explicit arrivalTime population ----------
    print("\n=== explicit Beds24 arrivalTime population ===")
    explicit = [b for b in bookings if str(b.get("arrivalTime") or "").strip()]
    print(f"bookings with non-empty arrivalTime: {len(explicit)} / {len(bookings)}")
    both = [b for b in approx_bookings if str(b.get("arrivalTime") or "").strip()]
    print(f"arrival window in comments AND explicit arrivalTime set: {len(both)} "
          f"(explicit must win for these)")
    print(f"arrival window in comments AND explicit arrivalTime EMPTY: "
          f"{len(approx_bookings) - len(both)} (fallback population)")
    if explicit:
        print(f"  explicit arrivalTime value shapes (digits masked): "
              f"{sorted({re.sub(chr(92)+'d', '#', str(b.get('arrivalTime'))) for b in explicit})[:10]}")

    print("\n=== END DIAGNOSTIC ===")


if __name__ == "__main__":
    main()
