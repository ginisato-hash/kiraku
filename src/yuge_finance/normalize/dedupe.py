"""重複排除ユーティリティ（import_hash / 自然キーベース）。"""
from __future__ import annotations

from typing import Callable, List


def dedupe(records: List, keyfn: Callable[[object], str]) -> List:
    """同一キーの先勝ちで重複を除去する。"""
    seen = set()
    out = []
    for r in records:
        k = keyfn(r)
        if k in seen:
            continue
        seen.add(k)
        out.append(r)
    return out


def by_import_hash(records: List) -> List:
    return dedupe(records, lambda r: getattr(r, "import_hash", id(r)))


def by_booking_id(records: List) -> List:
    return dedupe(records, lambda r: getattr(r, "booking_id", id(r)))
