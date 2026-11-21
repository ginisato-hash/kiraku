"""Actions Secret YUGE_LABOR_ACTUALS_JSON → YUGE_LABOR_ACTUAL_DIR/labor_actual_YYYY-MM.json（Issue #72 L 第2段）。

Secret の形は {"YYYY-MM": mf_payroll_csv.PayrollMonth.summary(), ...}。月合計だけを受け付け、
氏名や従業員別の値を含む形（未知のキー）は拒否する。何を置いたかは月と件数だけを出力する。
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

ALLOWED = {"work_month", "pay_date", "closing_date", "period_alignment", "employee_count", "gross_pay_jpy", "employer_burden_jpy",
           "employer_burden_basis", "labor_cost_jpy", "worked_days", "worked_hours", "hours_status", "hours_unverified_columns",
           "hours_unit_basis", "contract_types", "offices", "source_sha256", "source_rows", "company", "finalized",
           "expected_employee_count", "verification_status", "verification_problems"}


def stage(raw: str, out_dir: Path) -> list[str]:
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("YUGE_LABOR_ACTUALS_JSON must be an object keyed by YYYY-MM")
    out_dir.mkdir(parents=True, exist_ok=True)
    months = []
    for month, summary in sorted(data.items()):
        if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month) or not isinstance(summary, dict):
            raise ValueError(f"bad entry {month!r}")
        unknown = set(summary) - ALLOWED
        if unknown:
            raise ValueError(f"{month}: unexpected keys {sorted(unknown)} (month totals only)")
        if summary.get("work_month") != month or not isinstance(summary.get("labor_cost_jpy"), int):
            raise ValueError(f"{month}: work_month/labor_cost_jpy do not match")
        # Only a verified month becomes an actual; anything else stays on the assumption (never staged as 0).
        if summary.get("verification_status") != "verified":
            raise ValueError(f"{month}: not verified ({summary.get('verification_problems')})")
        (out_dir / f"labor_actual_{month}.json").write_text(json.dumps(summary, ensure_ascii=False), encoding="utf-8")
        months.append(month)
    return months


if __name__ == "__main__":
    months = stage(os.environ.get("YUGE_LABOR_ACTUALS_JSON", ""), Path(sys.argv[1]))
    print(f"staged labour actuals for {len(months)} month(s): {', '.join(months)}")
