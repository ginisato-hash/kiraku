"""ONE-TIME diagnostic round 2 (temporary; removed after the investigation).

Round 1 proved: Beds24 v2 booking field `comments` (individual booking record,
NO extra include parameter) carries the Booking.com child-age metadata
("1 child aged 10") for anchor booking 91673623, and the normal 15-min
fetch_raw() path already retrieves it. `guestComments` does not exist in the
payload at all.

Round 2 answers the two remaining implementation questions with real data:
  (A) which real child-age text shapes exist (so parser tests use ACTUAL
      patterns, not invented ones), and
  (B) which `comments` lines are OTA/system-generated boilerplate that must
      never be shown as a guest notice.

Privacy design for (B): guest-authored free text is never printed. Each line is
reduced to a template (every digit run -> '#') and counted by DISTINCT booking.
Only templates appearing in 2+ different bookings are printed — machine-
generated boilerplate by definition; human-typed text is not byte-identical
across separate bookings. Singleton templates are reported as length + hash
prefix only. The token is never printed; identity fields are never read.
"""
from __future__ import annotations

import hashlib
import re
from collections import defaultdict

from yuge_finance.api.beds24_client import Beds24Client

ANCHOR = "91673623"
MONTHS = ["2026-%02d" % m for m in range(1, 13)]

CHILD_RE = re.compile(r"aged?\b|child(?:ren)?\b", re.IGNORECASE)
WINDOW = 45


def main():
    client = Beds24Client()

    bookings = []
    for month in MONTHS:
        bookings.extend(client.fetch_raw(month))
    print(f"total bookings fetched ({MONTHS[0]}..{MONTHS[-1]}): {len(bookings)}")

    with_comments = [b for b in bookings if str(b.get("comments") or "").strip()]
    print(f"bookings with non-empty `comments`: {len(with_comments)}")

    # ---- (A) real child-age shapes ----
    print("\n=== (A) child-age metadata occurrences in `comments` ===")
    shapes = defaultdict(int)
    per_ota = defaultdict(lambda: [0, 0])  # ota -> [with children, with child-age text]
    for b in bookings:
        ota = str(b.get("refererEditable") or b.get("apiSource") or "?")
        nchild = int(float(b.get("numChild") or 0))
        comments = str(b.get("comments") or "")
        has_age = bool(re.search(r"child(?:ren)?\s+aged", comments, re.IGNORECASE))
        if nchild > 0:
            per_ota[ota][0] += 1
            if has_age:
                per_ota[ota][1] += 1
        if not has_age:
            continue
        for m in re.finditer(r"\d+\s+child(?:ren)?\s+aged[^\n]*", comments, re.IGNORECASE):
            text = m.group(0).strip()
            shapes[re.sub(r"\d+", "#", text)] += 1
            print(f"  booking_id={b.get('id')} ota={ota} numAdult={b.get('numAdult')} "
                  f"numChild={nchild} child_match={text!r}")
    print(f"\n  distinct child-age line templates (digits masked): {dict(shapes)}")
    print("  per-OTA [bookings with numChild>0, of which carrying child-age text]:")
    for ota, (a, c) in sorted(per_ota.items()):
        print(f"    {ota}: {a}, {c}")

    # ---- (B) line composition of `comments` ----
    print("\n=== (B) `comments` line templates (only 2+ distinct bookings printed) ===")
    tmpl_bookings = defaultdict(set)
    for b in with_comments:
        for line in str(b.get("comments")).splitlines():
            line = line.strip()
            if not line:
                continue
            tmpl_bookings[re.sub(r"\d+", "#", line)].add(str(b.get("id")))
    repeated = {t: len(ids) for t, ids in tmpl_bookings.items() if len(ids) >= 2}
    singles = {t: len(ids) for t, ids in tmpl_bookings.items() if len(ids) < 2}
    print(f"  distinct line templates: {len(tmpl_bookings)} "
          f"(repeated={len(repeated)}, singleton={len(singles)})")
    for t, n in sorted(repeated.items(), key=lambda kv: -kv[1]):
        print(f"  bookings={n:4d} template={t!r}")
    print(f"\n  singleton lines (content withheld — length + hash prefix only): {len(singles)}")
    for t in sorted(singles):
        h = hashlib.sha1(t.encode("utf-8")).hexdigest()[:8]
        print(f"    len={len(t)} ascii={t.isascii()} sha1={h}")

    # ---- anchor booking line-by-line ----
    print(f"\n=== anchor booking {ANCHOR}: line classification ===")
    for b in bookings:
        if str(b.get("id")) != ANCHOR:
            continue
        comments = str(b.get("comments") or "")
        print(f"  comments length={len(comments)} lines={len(comments.splitlines())}")
        for i, line in enumerate(comments.splitlines()):
            s = line.strip()
            if not s:
                print(f"    line{i}: (blank)")
                continue
            t = re.sub(r"\d+", "#", s)
            n = len(tmpl_bookings.get(t, ()))
            child = bool(CHILD_RE.search(s))
            shown = repr(s) if (n >= 2 or child) else f"(withheld, len={len(s)})"
            print(f"    line{i}: bookings_with_same_template={n} child_pattern={child} {shown}")

    print("\n=== END DIAGNOSTIC ===")


if __name__ == "__main__":
    main()
