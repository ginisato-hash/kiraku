"""月次債務返済予定表 取込（喜らく単体・Phase B）。

imports/loan_repayment_schedule/*.csv を読み、liability_account の許可値を検証する。
返済予定表が存在しない債務者（政策金融公庫等）は、この取込では何も生成しない
＝「予定表未投入」として accounting.debt_journal 側でexception扱いになる。
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Dict, List

from .. import config, csvio, db
from ..normalize.dedupe import by_import_hash
from ..normalize.schema import LoanScheduleEntry

SRC_DIR_NAME = "loan_repayment_schedule"
ALLOWED_LIABILITY_ACCOUNTS = {
    "短期借入金", "長期借入金", "関係会社借入金", "役員借入金", "長期未払金",
}
TOLERANCE = 1


def _is_comment_row(row: dict) -> bool:
    first = next((v for v in row.values() if v), "")
    return str(first).strip().startswith("#")


def load(month: str = None) -> List[LoanScheduleEntry]:
    src = config.IMPORTS_DIR / SRC_DIR_NAME
    out: List[LoanScheduleEntry] = []
    for csv_path in sorted(src.glob("*.csv")):
        for row in csvio.read_dicts(csv_path):
            if not any(row.values()) or _is_comment_row(row):
                continue
            liab = (row.get("liability_account") or "").strip()
            if not liab:
                continue
            e = LoanScheduleEntry(
                loan_id=(row.get("loan_id") or "").strip(),
                lender=(row.get("lender") or "").strip(),
                liability_account=liab,
                payment_date=(row.get("payment_date") or "").strip(),
                total_payment=row.get("total_payment") or 0,
                principal_payment=row.get("principal_payment") or 0,
                interest_payment=row.get("interest_payment") or 0,
                ending_balance=row.get("ending_balance") or 0,
                bank_description_match=(row.get("bank_description_match") or "").strip(),
                memo=(row.get("memo") or "").strip(),
                source_file=csv_path.name,
            ).finalize()
            out.append(e)
    out = by_import_hash(out)
    if month:
        out = [e for e in out if e.payment_date[:7] == month]
    return out


def validate(records: List[LoanScheduleEntry]) -> List[Dict]:
    """liability_account不正値、principal+interest!=totalをcritical検出する。"""
    issues: List[Dict] = []
    for e in records:
        if e.liability_account not in ALLOWED_LIABILITY_ACCOUNTS:
            issues.append({"loan_id": e.loan_id, "issue": "liability_account不正値",
                           "value": e.liability_account, "severity": "critical"})
        if abs((e.principal_payment + e.interest_payment) - e.total_payment) > TOLERANCE:
            issues.append({"loan_id": e.loan_id,
                           "issue": "principal_payment+interest_payment != total_payment",
                           "value": (e.principal_payment, e.interest_payment, e.total_payment),
                           "severity": "critical"})
    return issues


def run(month: str, conn=None) -> Dict:
    own = conn is None
    conn = conn or db.connect()
    raw_dir = config.DATA_DIR / "raw" / "loan_repayment_schedule"
    raw_dir.mkdir(parents=True, exist_ok=True)
    src = config.IMPORTS_DIR / SRC_DIR_NAME
    for csv_path in sorted(src.glob("*.csv")):
        shutil.copy2(csv_path, raw_dir / csv_path.name)

    records = load(month)
    issues = validate(records)
    critical = [i for i in issues if i["severity"] == "critical"]

    staging = config.DATA_DIR / "staging" / "loan_schedule" / f"{month}.csv"
    csvio.write_dataclasses(staging, records, LoanScheduleEntry)
    stats = db.upsert(conn, "loan_schedule", records) if records else {"inserted": 0, "skipped": 0}

    if own:
        conn.close()
    status = "予定表投入済" if records else "予定表未投入"
    if critical:
        status = "要確認"
    return {"count": len(records), "staging": str(staging), "status": status,
            "critical_issues": len(critical), "issues": issues, **stats}
