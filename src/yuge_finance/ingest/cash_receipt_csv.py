"""現金レシートCSV取込 → CashTransaction 正規化（喜らく単体）。

imports/cash_receipts/csv/ と reviewed/ のCSVを読む。
imports/cash_receipts/images/ の原本ファイルと receipt_file を照合。
review_status が approved 以外は自動確定仕訳にしない（下流で制御）。
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import List, Optional

from .. import config, csvio, db
from ..normalize.dedupe import by_import_hash
from ..normalize.schema import CashTransaction

ALLOWED_TYPES = {"現金支払", "現金入金", "現金移動", "立替精算"}
ALLOWED_STATUS = {"needs_review", "reviewed", "approved"}


def _from_row(row: dict, source_file: str) -> CashTransaction:
    tx = CashTransaction(
        source_file=source_file,
        transaction_date=_normdate(row.get("transaction_date", "")),
        transaction_type=(row.get("transaction_type") or "").strip(),
        amount=row.get("amount", 0),
        tax_amount=row.get("tax_amount", 0),
        tax_rate=(row.get("tax_rate") or "").strip(),
        category=(row.get("category") or "").strip(),
        vendor=(row.get("vendor") or "").strip(),
        description=(row.get("description") or "").strip(),
        payment_method=(row.get("payment_method") or "現金").strip(),
        receipt_file=(row.get("receipt_file") or "").strip(),
        counterparty=(row.get("counterparty") or "").strip(),
        review_status=(row.get("review_status") or "needs_review").strip(),
        memo=(row.get("memo") or "").strip(),
    )
    return tx.finalize()


def _normdate(s: str) -> str:
    if not s:
        return ""
    s = str(s).strip().replace("/", "-").replace(".", "-")
    parts = s.split("-")
    if len(parts) == 3:
        y, m, d = parts
        return f"{int(y):04d}-{int(m):02d}-{int(d):02d}"
    return s


def load(month: Optional[str] = None) -> List[CashTransaction]:
    base = config.IMPORTS_DIR / "cash_receipts"
    out: List[CashTransaction] = []
    for sub in ("reviewed", "csv"):  # reviewed優先（同一hashは先勝ち）
        for csv_path in sorted((base / sub).glob("*.csv")):
            for row in csvio.read_dicts(csv_path):
                if not any(row.values()):
                    continue
                tx = _from_row(row, csv_path.name)
                if not tx.transaction_date:
                    continue
                out.append(tx)
    out = by_import_hash(out)
    if month:
        out = [t for t in out if t.transaction_date[:7] == month]
    return out


def reconcile_images(records: List[CashTransaction]) -> List[dict]:
    """receipt_file と images/ 原本の存在を照合し、不一致を返す。"""
    images_dir = config.IMPORTS_DIR / "cash_receipts" / "images"
    existing = {p.name for p in images_dir.glob("*") if p.is_file()}
    issues = []
    for r in records:
        if r.receipt_file and r.receipt_file not in existing:
            issues.append({
                "cash_transaction_id": r.cash_transaction_id,
                "receipt_file": r.receipt_file,
                "issue": "原本画像が見つかりません",
            })
    return issues


def run(month: str, conn=None) -> dict:
    own = conn is None
    conn = conn or db.connect()
    raw_dir = config.DATA_DIR / "raw" / "cash_receipts" / month
    raw_dir.mkdir(parents=True, exist_ok=True)
    base = config.IMPORTS_DIR / "cash_receipts"
    for sub in ("csv", "reviewed"):
        for csv_path in sorted((base / sub).glob("*.csv")):
            shutil.copy2(csv_path, raw_dir / f"{sub}__{csv_path.name}")
    records = load(month)
    staging = config.DATA_DIR / "staging" / "cash_transactions" / f"{month}.csv"
    csvio.write_dataclasses(staging, records, CashTransaction)
    stats = db.upsert(conn, "cash_transactions", records)
    image_issues = reconcile_images(records)
    not_approved = [r for r in records if r.review_status != "approved"]
    if own:
        conn.close()
    return {
        "count": len(records),
        "staging": str(staging),
        "image_issues": len(image_issues),
        "not_approved": len(not_approved),
        **stats,
    }
