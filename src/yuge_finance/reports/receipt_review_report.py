"""現金レシート確認レポート（review_status・原本照合）。"""
from __future__ import annotations

from pathlib import Path
from typing import List

from .. import csvio
from ..normalize.schema import CashTransaction

COLS = ["cash_transaction_id", "transaction_date", "transaction_type", "amount",
        "category", "vendor", "description", "receipt_file", "review_status",
        "image_found", "action"]


def write(cash_txns: List[CashTransaction], image_issue_files: set, path: Path) -> Path:
    rows = []
    for t in cash_txns:
        img_found = "" if not t.receipt_file else (
            "なし" if t.receipt_file in image_issue_files else "あり")
        if t.review_status != "approved":
            action = "要確認→承認(approved)に更新"
        elif t.receipt_file and t.receipt_file in image_issue_files:
            action = "原本画像を確認"
        else:
            action = "OK"
        rows.append({
            "cash_transaction_id": t.cash_transaction_id,
            "transaction_date": t.transaction_date,
            "transaction_type": t.transaction_type,
            "amount": t.amount, "category": t.category, "vendor": t.vendor,
            "description": t.description, "receipt_file": t.receipt_file,
            "review_status": t.review_status, "image_found": img_found,
            "action": action,
        })
    csvio.write_rows(path, rows, COLS)
    return path
