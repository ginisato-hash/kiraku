"""共通バリデーション（仕訳の貸借一致・対象月フィルタ等）。"""
from __future__ import annotations

from typing import List, Tuple

TOLERANCE = 0.5  # 円。丸め誤差吸収。


def journal_balanced(entries: List) -> Tuple[bool, float, float]:
    """仕訳全体の借方合計=貸方合計を検証。(ok, 借方合計, 貸方合計)。"""
    debit = sum(e.debit_amount for e in entries)
    credit = sum(e.credit_amount for e in entries)
    return abs(debit - credit) <= TOLERANCE, debit, credit


def entry_balanced(entry) -> bool:
    return abs(entry.debit_amount - entry.credit_amount) <= TOLERANCE


def in_month(date_str: str, month: str) -> bool:
    """date_str(YYYY-MM-DD等)の年月が month(YYYY-MM)に一致するか。"""
    return bool(date_str) and date_str[:7] == month


def out_of_month_count(records, date_attr: str, month: str) -> int:
    n = 0
    for r in records:
        d = getattr(r, date_attr, "") or ""
        if d and d[:7] != month:
            n += 1
    return n
