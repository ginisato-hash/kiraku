"""手動補正CSV取込 → ManualAdjustment 正規化（喜らく単体）。

imports/manual_adjustments/ のCSVを読み、借方=貸方を検証する。
借入・固定資産・未払・前払・会計士調整 等の手動仕訳に使う。
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import List, Optional, Tuple

from .. import config, csvio, db
from ..normalize.dedupe import by_import_hash
from ..normalize.schema import ManualAdjustment

TOLERANCE = 0.5


def _from_row(row: dict, source_file: str) -> ManualAdjustment:
    adj = ManualAdjustment(
        source_file=source_file,
        journal_date=_normdate(row.get("journal_date", "")),
        description=(row.get("description") or "").strip(),
        debit_account=(row.get("debit_account") or "").strip(),
        debit_subaccount=(row.get("debit_subaccount") or "").strip(),
        debit_amount=row.get("debit_amount", 0),
        credit_account=(row.get("credit_account") or "").strip(),
        credit_subaccount=(row.get("credit_subaccount") or "").strip(),
        credit_amount=row.get("credit_amount", 0),
        tax_category=(row.get("tax_category") or "").strip(),
        counterparty=(row.get("counterparty") or "").strip(),
        memo=(row.get("memo") or "").strip(),
    )
    return adj.finalize()


def _normdate(s: str) -> str:
    if not s:
        return ""
    s = str(s).strip().replace("/", "-").replace(".", "-")
    parts = s.split("-")
    if len(parts) == 3:
        y, m, d = parts
        return f"{int(y):04d}-{int(m):02d}-{int(d):02d}"
    return s


def load(month: Optional[str] = None) -> List[ManualAdjustment]:
    src_dir = config.IMPORTS_DIR / "manual_adjustments"
    out: List[ManualAdjustment] = []
    for csv_path in sorted(src_dir.glob("*.csv")):
        for row in csvio.read_dicts(csv_path):
            if not any(row.values()):
                continue
            adj = _from_row(row, csv_path.name)
            if not adj.journal_date:
                continue
            out.append(adj)
    out = by_import_hash(out)
    if month:
        out = [a for a in out if a.journal_date[:7] == month]
    return out


def validate(records: List[ManualAdjustment]) -> List[dict]:
    """各行の借方金額=貸方金額を検証し、不一致を返す。"""
    issues = []
    for a in records:
        if abs(a.debit_amount - a.credit_amount) > TOLERANCE:
            issues.append({
                "adjustment_id": a.adjustment_id,
                "description": a.description,
                "debit_amount": a.debit_amount,
                "credit_amount": a.credit_amount,
                "issue": "借方≠貸方",
            })
    return issues


def run(month: str, conn=None) -> dict:
    own = conn is None
    conn = conn or db.connect()
    raw_dir = config.DATA_DIR / "raw" / "manual_adjustments" / month
    raw_dir.mkdir(parents=True, exist_ok=True)
    src_dir = config.IMPORTS_DIR / "manual_adjustments"
    for csv_path in sorted(src_dir.glob("*.csv")):
        shutil.copy2(csv_path, raw_dir / csv_path.name)
    records = load(month)
    issues = validate(records)
    staging = config.DATA_DIR / "staging" / "manual_adjustments" / f"{month}.csv"
    csvio.write_dataclasses(staging, records, ManualAdjustment)
    stats = db.upsert(conn, "manual_adjustments", records)
    if own:
        conn.close()
    return {"count": len(records), "staging": str(staging),
            "balance_issues": len(issues), **stats}
