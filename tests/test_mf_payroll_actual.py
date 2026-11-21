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


def row(no, name, contract, days, hours, gross, employer):
    r = dict.fromkeys(HEADER, "")
    r.update({"従業員番号": no, "従業員": name, "所属事業所": "TEST 有限会社", "契約種別": contract, "出勤日数（平日）": str(days),
              "所定時間（平日）": str(hours), "支給合計": f"{gross:,}"})
    for c, v in zip(mp.EMPLOYER_COLUMNS, employer):
        r[c] = str(v)
    return r


def csv_bytes(rows, total=True):
    import csv, io
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=HEADER)
    w.writeheader()
    for r in rows:
        w.writerow(r)
    if total:
        t = dict.fromkeys(HEADER, "")
        t["従業員番号"] = "合計"
        t["支給合計"] = str(sum(int(r["支給合計"].replace(",", "")) for r in rows))
        for c in mp.EMPLOYER_COLUMNS:
            t[c] = str(sum(int(r[c] or 0) for r in rows))
        w.writerow(t)
    return ("﻿" + buf.getvalue()).encode("utf-8")


ROWS = [row("3", "テスト 太郎", "正社員", 20, "160.0", 300000, [15000, 2500, 300, 27000, 0, 0, 1800, 900, 10]),
        row("05", "サンプル 花子", "パート", 12, "48:30", 60000, [0, 0, 0, 0, 0, 0, 330, 180, 2]),
        row("8", "ダミー 次郎", "アルバイト", 0, "", 0, [0] * 9)]


def test_month_totals_only_no_names_and_hhmm_hours():
    m = mp.read_payment_deductions(csv_bytes(ROWS), "2026-11-13", "2026-10-31")
    s = m.summary()
    assert (s["work_month"], s["period_alignment"], s["employee_count"]) == ("2026-10", "calendar_month", 3)
    assert (s["gross_pay_jpy"], s["employer_burden_jpy"], s["labor_cost_jpy"]) == (360000, 48022, 408022)
    assert s["worked_hours"] == 208.5 and s["worked_days"] == 32
    assert s["contract_types"] == {"アルバイト": 1, "パート": 1, "正社員": 1}
    blob = json.dumps(s, ensure_ascii=False)
    assert not any(n in blob for n in ("テスト", "太郎", "サンプル", "花子", "ダミー")), "names never leave the reader"
    assert "請求確定額ではない" in s["employer_burden_basis"]


def test_total_row_must_agree_and_shapes_are_refused():
    rows = csv_bytes(ROWS).decode("utf-8-sig").splitlines()
    rows[-1] = rows[-1].replace(",360000,", ",360001,")
    with pytest.raises(mp.PayrollCsvError, match="合計 row disagrees"):
        mp.read_payment_deductions(("﻿" + "\n".join(rows) + "\n").encode(), "2026-11-13", "2026-10-31")
    with pytest.raises(mp.PayrollCsvError, match="missing"):
        mp.read_payment_deductions("﻿従業員番号,支給合計\n1,100\n".encode(), "2026-11-13", "2026-10-31")
    dup = [ROWS[0], ROWS[0]]
    with pytest.raises(mp.PayrollCsvError, match="duplicate"):
        mp.read_payment_deductions(csv_bytes(dup), "2026-11-13", "2026-10-31")
    assert mp.read_payment_deductions(csv_bytes(ROWS, total=False), "2026-11-13", "2026-10-31").labor_cost_jpy == 408022


def test_closing_not_month_end_is_not_spread_into_a_calendar_month():
    m = mp.read_payment_deductions(csv_bytes(ROWS), "2026-11-25", "2026-11-20")
    la = v2.labor_actual_from_payroll(m.summary())
    assert (m.work_month, la["cost_status"], la["cost"]) == (None, "period_not_aligned", None)


def test_bep_takes_the_payroll_cost_with_its_basis():
    la = v2.labor_actual_from_payroll(mp.read_payment_deductions(csv_bytes(ROWS), "2026-11-13", "2026-10-31").summary())
    assert (la["labor_source"], la["cost_status"], la["cost"], la["gross_pay"], la["employer_burden"]) == (v2.LABOR_SOURCE_MF_ACTUAL, "computed", 408022.0, 360000, 48022)


@pytest.mark.skipif(not os.environ.get("MF_PAYROLL_GOLDEN_CSV"), reason="real export is private; set MF_PAYROLL_GOLDEN_CSV to check it")
def test_real_export_shape():
    p = Path(os.environ["MF_PAYROLL_GOLDEN_CSV"])
    m = mp.read_payment_deductions(p.read_bytes(), os.environ.get("MF_PAYROLL_PAY_DATE", "2026-11-13"), os.environ.get("MF_PAYROLL_CLOSING_DATE", "2026-10-31"))
    assert m.employee_count >= 1 and m.labor_cost_jpy >= m.gross_pay_jpy


def test_monthly_reads_the_month_summary_only_when_present(tmp_path, monkeypatch):
    from yuge_finance import monthly
    m = mp.read_payment_deductions(csv_bytes(ROWS), "2026-11-13", "2026-10-31")
    (tmp_path / "labor_actual_2026-10.json").write_text(json.dumps(m.summary(), ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("YUGE_LABOR_ACTUAL_DIR", str(tmp_path))
    assert monthly._payroll_labor_actual("2026-10")["cost"] == 408022.0
    assert monthly._payroll_labor_actual("2026-11") is None, "a month without payroll stays on the assumption, never 0"
    monkeypatch.delenv("YUGE_LABOR_ACTUAL_DIR")
    assert monthly._payroll_labor_actual("2026-10") is None


def test_stage_from_secret_accepts_month_totals_only(tmp_path):
    from yuge_finance import labor_actuals_stage as st
    s = mp.read_payment_deductions(csv_bytes(ROWS), "2026-11-13", "2026-10-31").summary()
    assert st.stage(json.dumps({"2026-10": s}), tmp_path) == ["2026-10"]
    assert json.loads((tmp_path / "labor_actual_2026-10.json").read_text(encoding="utf-8"))["labor_cost_jpy"] == 408022
    with pytest.raises(ValueError, match="unexpected keys"):
        st.stage(json.dumps({"2026-10": {**s, "employees": [{"name": "テスト 太郎"}]}}), tmp_path)
    with pytest.raises(ValueError, match="do not match"):
        st.stage(json.dumps({"2026-11": s}), tmp_path)
