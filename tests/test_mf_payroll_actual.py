"""MF給与 支給控除一覧表CSV → 月の人件費実績。Issue #72 L 第2段。

フィクスチャは合成（列見出しは正式出力と同じ75列、氏名・金額は架空）。実データは
リポジトリへ置かない — MF_PAYROLL_GOLDEN_CSV が指す時だけ形と検算を確かめる。
"""
import json
import os
from pathlib import Path

import pytest

from yuge_finance.accounting import breakeven_v2 as v2
from yuge_finance.ingest import mf_payroll_csv as mp

HEADER = ("従業員番号,従業員,所属事業所,部門,職種,契約種別,出勤日数（平日）,出勤日数（所定休日）,出勤日数（法定休日）,欠勤日数（平日）,遅刻時間（平日）,早退時間（平日）,"
          "所定時間（平日）,所定時間（所定休日）,所定時間（法定休日）,所定外時間（平日）,法定外時間（平日）,深夜法定外時間（平日）,役員報酬(支給),基本給(支給),役職手当(支給),"
          "家族手当(支給),住宅手当(支給),営業手当(支給),残業手当(支給),深夜残業手当(支給),法定休日手当(支給),所定休日手当(支給),通勤手当/課税(支給),通勤手当/非課(支給),"
          "課税支給合計,非課税支給合計,課税現物支給合計,非課税現物支給合計,支給合計,労保対象合計,社保対象合計(金銭),社保対象合計(現物),社保対象通勤手当(金銭),"
          "社保対象通勤手当(現物),固定賃金合計,役員報酬合計,割増基礎合計,控除基礎合計,健康保険料(控除),介護保険料(控除),子ども・子育て支援金(控除),厚生年金保険料(控除),"
          "雇用保険料(控除),所得税(控除),住民税(控除),寮費(控除),社会保険料合計,控除合計,社保控除後合計,差引支給合計,現物支給額,振込支給１,振込支給２,振込支給残額,"
          "振込支給額合計,現金支給額,扶養人数,税額表,健保標準報酬,厚年標準報酬,健康保険料(会社),介護保険料(会社),子ども・子育て支援金(会社),厚生年金保険料(会社),"
          "子ども・子育て拠出金(会社),厚生年金基金掛金(会社),雇用保険料(会社),労災保険料(会社),一般拠出金(会社)").split(",")
assert len(HEADER) == 75


def row(no, name, contract, days, hours, gross, employer, extra=None, header=HEADER):
    r = dict.fromkeys(header, "")
    r.update({"従業員番号": no, "従業員": name, "所属事業所": "有限会社蔵王喜らく", "契約種別": contract, "出勤日数（平日）": str(days),
              "所定時間（平日）": str(hours), "支給合計": f"{gross:,}"})
    for c, v in zip(mp.EMPLOYER_COLUMNS, employer):
        r[c] = "" if v is None else str(v)
    r.update(extra or {})
    return r


def csv_bytes(rows, total=True, header=HEADER):
    import csv, io
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=header)
    w.writeheader()
    for r in rows:
        w.writerow(r)
    if total:
        t = dict.fromkeys(header, "")
        t["従業員番号"] = "合計"
        t["支給合計"] = str(sum(int(r["支給合計"].replace(",", "")) for r in rows))
        for c in mp.EMPLOYER_COLUMNS:
            t[c] = str(sum(int(r[c] or 0) for r in rows))
        w.writerow(t)
    return ("\ufeff" + buf.getvalue()).encode("utf-8")


ROWS = [row("3", "テスト 太郎", "正社員", 20, "160.0", 300000, [15000, 2500, 300, 27000, 0, 0, 1800, 900, 10]),
        row("05", "サンプル 花子", "パート", 12, "48.5", 60000, [0, 0, 0, 0, 0, 0, 330, 180, 2]),
        row("8", "ダミー 次郎", "アルバイト", 0, "", 0, [0] * 9)]
OK = dict(finalized=True, expected_employees=3)


def read(data, pay="2026-11-13", closing="2026-10-31", **kw):
    return mp.read_payment_deductions(data, pay, closing, **{**OK, **kw})


def test_verified_month_totals_only_no_names():
    s = read(csv_bytes(ROWS)).summary()
    assert (s["work_month"], s["verification_status"], s["verification_problems"], s["employee_count"]) == ("2026-10", "verified", [], 3)
    assert (s["gross_pay_jpy"], s["employer_burden_jpy"], s["labor_cost_jpy"]) == (360000, 48022, 408022)
    assert (s["worked_hours"], s["hours_status"], s["worked_days"]) == (208.5, "complete", 32)
    assert "0.00時間" in s["hours_unit_basis"]
    blob = json.dumps(s, ensure_ascii=False)
    assert not any(n in blob for n in ("テスト", "太郎", "サンプル", "花子", "ダミー")), "names never leave the reader"
    assert "請求確定額ではない" in s["employer_burden_basis"]


def test_l1_night_and_holiday_hours_are_counted_once():
    """Review 7056823712 L-1: 8h scheduled + 2h night overtime + 3h statutory-holiday overtime is 13h, not 8h."""
    header = HEADER + ["法定外時間（法定休日）"]
    r = row("3", "テスト 太郎", "正社員", 1, "8.00", 10000, [0, 0, 0, 0, 0, 0, 60, 30, 1],
            extra={"深夜法定外時間（平日）": "2.00", "法定外時間（法定休日）": "3.00"}, header=header)
    s = read(csv_bytes([r], header=header), expected_employees=1).summary()
    assert (s["worked_hours"], s["hours_status"]) == (13.0, "complete")


def test_l1_a_time_column_of_unconfirmed_meaning_withholds_the_total():
    header = HEADER + ["深夜所定時間（平日）", "総労働時間（平日）"]
    r = row("3", "テスト 太郎", "正社員", 1, "8.00", 10000, [0, 0, 0, 0, 0, 0, 60, 30, 1], extra={"深夜所定時間（平日）": "1.00"}, header=header)
    s = read(csv_bytes([r], header=header), expected_employees=1).summary()
    assert (s["worked_hours"], s["hours_status"], s["hours_unverified_columns"]) == (None, "unverified_columns", ["深夜所定時間（平日）"])
    # A zero there is no ambiguity.
    r2 = row("3", "テスト 太郎", "正社員", 1, "8.00", 10000, [0, 0, 0, 0, 0, 0, 60, 30, 1], extra={"深夜所定時間（平日）": "0.00"}, header=header)
    assert read(csv_bytes([r2], header=header), expected_employees=1).worked_hours == 8.0
    with pytest.raises(mp.PayrollCsvError, match="no confirmed basis"):
        read(csv_bytes(ROWS), hours_unit="sexagesimal")


def test_coverage_gate_7056719794():
    assert read(csv_bytes(ROWS, total=False)).verification_problems == ["no_total_row"]
    assert read(csv_bytes(ROWS[:2]), expected_employees=3).verification_problems == ["employee_count_mismatch"]
    blank = [ROWS[0], row("05", "サンプル 花子", "パート", 12, "48.5", 60000, [None] * 9), ROWS[2]]
    assert "employer_burden_blank" in read(csv_bytes(blank)).verification_problems
    other = [row("3", "テスト 太郎", "正社員", 1, "8", 100, [0] * 9, extra={"所属事業所": "TEST 別会社"})]
    assert "company_mismatch" in read(csv_bytes(other), expected_employees=1).verification_problems
    m = mp.read_payment_deductions(csv_bytes(ROWS), "2026-11-13", "2026-10-31")
    assert set(m.verification_problems) == {"not_confirmed_finalized", "employee_count_unchecked"}
    for bad in (csv_bytes(ROWS, total=False), csv_bytes(ROWS[:2])):
        la = v2.labor_actual_from_payroll(read(bad).summary())
        assert (la["cost_status"], la["cost"]) == ("unverified", None), "an unverified month never reaches the BEP as an actual"


def test_total_row_must_agree_and_shapes_are_refused():
    rows = csv_bytes(ROWS).decode("utf-8-sig").splitlines()
    rows[-1] = rows[-1].replace(",360000,", ",360001,")
    with pytest.raises(mp.PayrollCsvError, match="合計 row disagrees"):
        read(("\ufeff" + "\n".join(rows) + "\n").encode())
    with pytest.raises(mp.PayrollCsvError, match="missing"):
        read("\ufeff従業員番号,支給合計\n1,100\n".encode())
    with pytest.raises(mp.PayrollCsvError, match="duplicate"):
        read(csv_bytes([ROWS[0], ROWS[0]]))
    with pytest.raises(mp.PayrollCsvError, match="not a number"):
        read(csv_bytes([row("3", "x", "パート", 1, "inf", 100, [0] * 9)]), expected_employees=1)


def test_closing_not_month_end_is_not_spread_into_a_calendar_month():
    m = read(csv_bytes(ROWS), "2026-11-25", "2026-11-20")
    la = v2.labor_actual_from_payroll(m.summary())
    assert (m.work_month, "closing_not_month_end" in m.verification_problems, la["cost"]) == (None, True, None)


def test_l2_payroll_and_attendance_sources_stay_apart():
    la = v2.labor_actual_from_payroll(read(csv_bytes(ROWS)).summary(), attendance={"complete": True, "worked_hours": 213.0, "worked_days": 32, "exceptions": []})
    assert (la["labor_source"], la["cost_status"], la["cost"]) == (v2.LABOR_SOURCE_MF_PAYROLL, "computed", 408022.0)
    assert la["payroll_hours"]["worked_hours"] == 208.5 and la["attendance"]["worked_hours"] == 213.0 and la["hours_difference"] == -4.5
    assert "attendance_complete" not in la, "attendance completeness does not vouch for the payroll hours"
    assert v2.labor_actual_from_payroll(read(csv_bytes(ROWS)).summary())["attendance"] == {"status": "not_provided"}


@pytest.mark.skipif(not os.environ.get("MF_PAYROLL_GOLDEN_CSV"), reason="real export is private; set MF_PAYROLL_GOLDEN_CSV to check it")
def test_real_export_shape():
    p = Path(os.environ["MF_PAYROLL_GOLDEN_CSV"])
    m = mp.read_payment_deductions(p.read_bytes(), os.environ.get("MF_PAYROLL_PAY_DATE", "2026-11-13"), os.environ.get("MF_PAYROLL_CLOSING_DATE", "2026-10-31"),
                                   finalized=True, expected_employees=int(os.environ.get("MF_PAYROLL_EXPECTED_EMPLOYEES", "0")) or None)
    assert m.employee_count >= 1 and m.labor_cost_jpy >= m.gross_pay_jpy
    assert m.hours_status == "complete", m.hours_unverified_columns


def test_monthly_reads_the_month_summary_only_when_present(tmp_path, monkeypatch):
    from yuge_finance import monthly
    m = read(csv_bytes(ROWS))
    (tmp_path / "labor_actual_2026-10.json").write_text(json.dumps(m.summary(), ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("YUGE_LABOR_ACTUAL_DIR", str(tmp_path))
    assert monthly._payroll_labor_actual("2026-10")["cost"] == 408022.0
    assert monthly._payroll_labor_actual("2026-11") is None, "a month without payroll stays on the assumption, never 0"
    monkeypatch.delenv("YUGE_LABOR_ACTUAL_DIR")
    assert monthly._payroll_labor_actual("2026-10") is None


def test_stage_from_secret_accepts_month_totals_only(tmp_path):
    from yuge_finance import labor_actuals_stage as st
    s = read(csv_bytes(ROWS)).summary()
    assert st.stage(json.dumps({"2026-10": s}), tmp_path) == ["2026-10"]
    assert json.loads((tmp_path / "labor_actual_2026-10.json").read_text(encoding="utf-8"))["labor_cost_jpy"] == 408022
    with pytest.raises(ValueError, match="unexpected keys"):
        st.stage(json.dumps({"2026-10": {**s, "employees": [{"name": "テスト 太郎"}]}}), tmp_path)
    with pytest.raises(ValueError, match="do not match"):
        st.stage(json.dumps({"2026-11": s}), tmp_path)
    with pytest.raises(ValueError, match="not verified"):
        st.stage(json.dumps({"2026-10": read(csv_bytes(ROWS, total=False)).summary()}), tmp_path)
