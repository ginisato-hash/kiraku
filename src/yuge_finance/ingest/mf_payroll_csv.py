"""MF給与 支給控除一覧表CSV（確定済み支給月）→ 月の人件費実績。Issue #72 L 第2段。

入力は MF給与の 帳票一覧 → 支給控除一覧表 → CSVダウンロード（UTF-8）の正式出力。
1行1従業員と、末尾の「合計」行。氏名列は読まず、出力は月の合計だけ（従業員別の金額を
持ち出さない）。合計行は使わずに従業員行から積み上げ、合計行と一致することを検算する
（一致しなければ例外にして値を出さない）。

人件費 = 支給合計 + 会社負担。会社負担は同じCSVの「…(会社)」9列で、MF給与が計算した額。
年金事務所・労働局の請求確定額とは別物なので、確度を `employer_burden_basis` に書く。
勤務月は締め日で決める。締め日が月末なら暦月と一致し按分しない。月末以外の締めは
按分を推定せず `period_alignment = "closing_not_month_end"` として月の値を出さない。

時間（L-1）：MF給与の時間区分のうち、重ならない区分だけを足す。MF公式（payr12）は「法定外」を
深夜以外の残業とし、夜間分は「深夜法定外」へ分けて集計すると説明しているので、所定・所定外・
法定外・深夜法定外（平日/所定休日/法定休日）を足す。深夜所定・深夜所定外・総労働・休憩・休暇みなし
など、内訳か別枠か公式に確定できない区分に値があれば、総時間を出さない（hours_status）。
単位は呼び出し側が確認した設定（既定 "decimal" ＝勤怠項目設定の 0.00時間）。h:mm は60進として読む。

網羅性（#7056719794）：合計行・事業者・確定済み・対象人数・会社負担の記録がそろった時だけ
verification_status = "verified"。それ以外は "unverified" と理由を返し、BEP は採用しない。
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
REQUIRED = ["従業員番号", "所属事業所", "契約種別", "支給合計", "出勤日数（平日）", *EMPLOYER_COLUMNS]
DAY_TYPES = ["平日", "所定休日", "法定休日"]
# 重ならない区分（payr12: 法定外は深夜以外、深夜分は深夜法定外へ分割）。
HOUR_PARTS = [f"{k}（{d}）" for k in ("所定時間", "所定外時間", "法定外時間", "深夜法定外時間") for d in DAY_TYPES]
# 実働でないもの（不就労・残数）。合計に入れず、値があっても総時間の判定を止めない。
NOT_WORKED = re.compile(r"^(遅刻時間|早退時間|欠勤|有休|代休|1日の所定労働時間|所定労働時間)")
HOURS_UNIT_BASIS = {"decimal": "MF給与 勤怠項目設定の単位 0.00時間（10進）"}
DAY_COLUMNS = ["出勤日数（平日）", "出勤日数（所定休日）", "出勤日数（法定休日）"]
EMPLOYER_BASIS = "MF給与の計算額（支給控除一覧表の会社負担列）。年金事務所・労働局の請求確定額ではない"


class PayrollCsvError(ValueError):
    pass


def _blank(v: str) -> bool:
    return (v or "").strip() == ""


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
    return float(s)  # the pattern admits finite values only (no inf/nan)


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
    worked_hours: Optional[float]
    hours_status: str                  # "complete" | "unverified_columns"
    hours_unverified_columns: List[str]
    hours_unit_basis: str
    contract_types: Dict[str, int]
    offices: List[str]
    source_sha256: str
    source_rows: int
    company: str
    finalized: bool
    expected_employee_count: Optional[int]
    verification_status: str           # "verified" | "unverified"
    verification_problems: List[str]

    def summary(self) -> Dict:
        return asdict(self)


def read_payment_deductions(data: bytes, pay_date: str, closing_date: str, *, company: str = "有限会社蔵王喜らく",
                            finalized: bool = False, expected_employees: Optional[int] = None, hours_unit: str = "decimal") -> PayrollMonth:
    """finalized は取得元が「確定済み」の支給月だったことを呼び出し側が確認した時だけ True。"""
    if hours_unit not in HOURS_UNIT_BASIS:
        raise PayrollCsvError(f"hours unit {hours_unit!r} has no confirmed basis")
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
    problems: List[str] = []
    if total is None:
        problems.append("no_total_row")
    # An employee paid something with every 会社負担 cell blank is a missing record, not a confirmed 0.
    if any(_number(r[ix["支給合計"]], "支給合計", n) > 0 and all(_blank(r[ix[c]]) for c in EMPLOYER_COLUMNS) for n, r in employees):
        problems.append("employer_burden_blank")
    offices = sorted({r[ix["所属事業所"]].strip() for _, r in employees})
    if offices != [company]:
        problems.append("company_mismatch")
    if not finalized:
        problems.append("not_confirmed_finalized")
    if expected_employees is None:
        problems.append("employee_count_unchecked")
    elif expected_employees != len(employees):
        problems.append("employee_count_mismatch")
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
    if not month_end:
        problems.append("closing_not_month_end")
    hour_cols = [c for c in header if "時間" in c and not NOT_WORKED.match(c)]
    unverified = [c for c in hour_cols if c not in HOUR_PARTS and any(_number(r[ix[c]], c, n) != 0 for n, r in employees)]
    worked_hours = None if unverified else round(sum(col_sum(c) for c in HOUR_PARTS if c in ix), 2)
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
        worked_days=round(sum(col_sum(c) for c in DAY_COLUMNS if c in ix), 2), worked_hours=worked_hours,
        hours_status="unverified_columns" if unverified else "complete", hours_unverified_columns=unverified,
        hours_unit_basis=HOURS_UNIT_BASIS[hours_unit],
        contract_types=dict(sorted(contract.items())), offices=offices,
        source_sha256=hashlib.sha256(data).hexdigest(), source_rows=len(rows) - 1,
        company=company, finalized=finalized, expected_employee_count=expected_employees,
        verification_status="unverified" if problems else "verified", verification_problems=problems)


def main(argv: List[str]) -> int:
    """python -m yuge_finance.ingest.mf_payroll_csv CSV --pay-date YYYY-MM-DD --closing-date YYYY-MM-DD [--out JSON]

    標準出力（または --out）は月の合計だけ。氏名・従業員別の金額は出さない。
    """
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("csv"); p.add_argument("--pay-date", required=True); p.add_argument("--closing-date", required=True); p.add_argument("--out")
    p.add_argument("--finalized", action="store_true", help="the pay month was 確定済み in MF給与 when exported")
    p.add_argument("--expected-employees", type=int, help="対象者数 shown for the pay month in MF給与")
    p.add_argument("--company", default="有限会社蔵王喜らく")
    a = p.parse_args(argv)
    m = read_payment_deductions(Path(a.csv).read_bytes(), a.pay_date, a.closing_date, company=a.company, finalized=a.finalized,
                                expected_employees=a.expected_employees)
    out = json.dumps(m.summary(), ensure_ascii=False, indent=2, allow_nan=False)
    if a.out:
        Path(a.out).write_text(out + "\n", encoding="utf-8")
    else:
        print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
