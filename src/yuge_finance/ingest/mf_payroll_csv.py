"""MF給与 支給控除一覧表CSV（確定済み支給月）→ 月の人件費実績。Issue #72 L 第2段。

入力は MF給与の 帳票一覧 → 支給控除一覧表 → CSVダウンロード（UTF-8）の正式出力。
1行1従業員と、末尾の「合計」行。氏名列は読まず、出力は月の合計だけ（従業員別の金額を
持ち出さない）。合計行は使わずに従業員行から積み上げ、合計行と一致することを検算する
（一致しなければ例外にして値を出さない）。

人件費 = 支給合計 + 会社負担。会社負担は同じCSVの「…(会社)」9列で、MF給与が計算した額。
年金事務所・労働局の請求確定額とは別物なので、確度を `employer_burden_basis` に書く。
勤務月は締め日で決める。締め日が月末なら暦月と一致し按分しない。月末以外の締めは
按分を推定せず `period_alignment = "closing_not_month_end"` として月の値を出さない。
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import sys
from calendar import monthrange
from dataclasses import dataclass, asdict
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional

EMPLOYER_COLUMNS = ["健康保険料(会社)", "介護保険料(会社)", "子ども・子育て支援金(会社)", "厚生年金保険料(会社)",
                    "子ども・子育て拠出金(会社)", "厚生年金基金掛金(会社)", "雇用保険料(会社)", "労災保険料(会社)", "一般拠出金(会社)"]
REQUIRED = ["従業員番号", "所属事業所", "契約種別", "支給合計", "出勤日数（平日）", "出勤日数（所定休日）", "出勤日数（法定休日）",
            "所定時間（平日）", "所定時間（所定休日）", "所定時間（法定休日）", "所定外時間（平日）", "法定外時間（平日）", *EMPLOYER_COLUMNS]
HOUR_COLUMNS = ["所定時間（平日）", "所定時間（所定休日）", "所定時間（法定休日）", "所定外時間（平日）", "法定外時間（平日）"]
DAY_COLUMNS = ["出勤日数（平日）", "出勤日数（所定休日）", "出勤日数（法定休日）"]
EMPLOYER_BASIS = "MF給与の計算額（支給控除一覧表の会社負担列）。年金事務所・労働局の請求確定額ではない"


class PayrollCsvError(ValueError):
    pass


def _number(v: str, col: str, row: int) -> float:
    s = (v or "").strip().replace(",", "")
    if s == "":
        return 0.0
    # 時間は "184.0" や "8:30" の両方がありうる。h:mm は時間へ換算する。
    m = re.fullmatch(r"(-?\d+):([0-5]\d)", s)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
        return h + (mi / 60 if h >= 0 else -mi / 60)
    if not re.fullmatch(r"-?\d+(\.\d+)?", s):
        raise PayrollCsvError(f"row {row}: {col} is not a number")
    return float(s)


@dataclass(frozen=True)
class PayrollMonth:
    work_month: Optional[str]          # YYYY-MM（締め日が月末のときだけ）
    pay_date: str
    closing_date: str
    period_alignment: str              # "calendar_month" | "closing_not_month_end"
    employee_count: int
    gross_pay_jpy: int
    employer_burden_jpy: int
    employer_burden_basis: str
    labor_cost_jpy: int
    worked_days: float
    worked_hours: float
    contract_types: Dict[str, int]
    offices: List[str]
    source_sha256: str
    source_rows: int

    def summary(self) -> Dict:
        return asdict(self)


def read_payment_deductions(data: bytes, pay_date: str, closing_date: str) -> PayrollMonth:
    for d in (pay_date, closing_date):
        try:
            date.fromisoformat(d)
        except ValueError as e:
            raise PayrollCsvError(f"date {d!r} is not YYYY-MM-DD") from e
    text = data.decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(text)))
    if len(rows) < 2:
        raise PayrollCsvError("no rows")
    header = rows[0]
    missing = [c for c in REQUIRED if c not in header]
    if missing:
        raise PayrollCsvError(f"not the 支給控除一覧表 export: missing {missing}")
    if len(set(header)) != len(header):
        raise PayrollCsvError("duplicate column names")
    ix = {c: header.index(c) for c in header}
    employees, total = [], None
    for n, r in enumerate(rows[1:], start=2):
        if len(r) != len(header):
            raise PayrollCsvError(f"row {n}: {len(r)} columns, header has {len(header)}")
        key = r[ix["従業員番号"]].strip()
        if key == "合計":
            if total is not None:
                raise PayrollCsvError("two 合計 rows")
            total = (n, r)
        elif re.fullmatch(r"\d{1,10}", key):
            if total is not None:
                raise PayrollCsvError(f"row {n}: employee row after 合計")
            employees.append((n, r))
        else:
            raise PayrollCsvError(f"row {n}: 従業員番号 is neither a number nor 合計")
    if not employees:
        raise PayrollCsvError("no employee rows")
    if len({r[ix['従業員番号']] for _, r in employees}) != len(employees):
        raise PayrollCsvError("duplicate 従業員番号")

    def col_sum(c: str) -> float:
        return sum(_number(r[ix[c]], c, n) for n, r in employees)

    gross = col_sum("支給合計")
    burden = sum(col_sum(c) for c in EMPLOYER_COLUMNS)
    if total is not None:
        n, r = total
        for c in ["支給合計", *EMPLOYER_COLUMNS]:
            if round(_number(r[ix[c]], c, n)) != round(col_sum(c)):
                raise PayrollCsvError(f"合計 row disagrees with the employee rows on {c}")
    for v, c in ((gross, "支給合計"), (burden, "会社負担")):
        if v != round(v):
            raise PayrollCsvError(f"{c} is not whole yen")
    closing = date.fromisoformat(closing_date)
    month_end = closing.day == monthrange(closing.year, closing.month)[1]
    contract: Dict[str, int] = {}
    for _, r in employees:
        k = r[ix["契約種別"]].strip() or "未設定"
        contract[k] = contract.get(k, 0) + 1
    return PayrollMonth(
        work_month=f"{closing.year:04d}-{closing.month:02d}" if month_end else None,
        pay_date=pay_date, closing_date=closing_date,
        period_alignment="calendar_month" if month_end else "closing_not_month_end",
        employee_count=len(employees), gross_pay_jpy=int(round(gross)), employer_burden_jpy=int(round(burden)),
        employer_burden_basis=EMPLOYER_BASIS, labor_cost_jpy=int(round(gross + burden)),
        worked_days=round(sum(col_sum(c) for c in DAY_COLUMNS), 2), worked_hours=round(sum(col_sum(c) for c in HOUR_COLUMNS), 2),
        contract_types=dict(sorted(contract.items())), offices=sorted({r[ix["所属事業所"]].strip() for _, r in employees}),
        source_sha256=hashlib.sha256(data).hexdigest(), source_rows=len(rows) - 1)


def main(argv: List[str]) -> int:
    """python -m yuge_finance.ingest.mf_payroll_csv CSV --pay-date YYYY-MM-DD --closing-date YYYY-MM-DD [--out JSON]

    標準出力（または --out）は月の合計だけ。氏名・従業員別の金額は出さない。
    """
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("csv"); p.add_argument("--pay-date", required=True); p.add_argument("--closing-date", required=True); p.add_argument("--out")
    a = p.parse_args(argv)
    m = read_payment_deductions(Path(a.csv).read_bytes(), a.pay_date, a.closing_date)
    out = json.dumps(m.summary(), ensure_ascii=False, indent=2)
    if a.out:
        Path(a.out).write_text(out + "\n", encoding="utf-8")
    else:
        print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
