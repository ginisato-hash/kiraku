"""例外レポート（medium/low confidence・未分類・未承認）。"""
from __future__ import annotations

from pathlib import Path
from typing import List

from .. import csvio
from ..normalize.schema import JournalEntry

COLS = ["journal_id", "journal_date", "source", "rule_id", "confidence",
        "description", "debit_account", "debit_subaccount", "debit_amount",
        "credit_account", "credit_subaccount", "credit_amount",
        "counterparty", "memo"]


def write(exceptions: List[JournalEntry], path: Path) -> Path:
    rows = []
    for e in exceptions:
        rows.append({c: getattr(e, c, "") for c in COLS})
    csvio.write_rows(path, rows, COLS)
    return path
