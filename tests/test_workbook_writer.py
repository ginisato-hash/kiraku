import openpyxl

from yuge_finance.excel.workbook_validator import validate
from yuge_finance.excel.workbook_writer import WorkbookWriter
from yuge_finance.normalize.schema import JournalEntry
from yuge_finance.reports import receipt_review_report, validation_report


def _journal():
    return [
        JournalEntry(journal_date="2026-07-10", month="2026-07", description="売上",
                     debit_account="売掛金", debit_amount=30000,
                     credit_account="宿泊売上", credit_amount=30000,
                     source="beds24", rule_id="rev").finalize(),
    ]


def test_write_and_formula_preserved(tmp_path):
    out = tmp_path / "wb.xlsx"
    w = WorkbookWriter()
    path = w.write("2026-07", _journal(), [], [], output_path=out)
    assert path.exists()

    wb = openpyxl.load_workbook(str(path))
    # 仕訳帳にデータが入っている
    assert wb["04_仕訳帳"]["E7"].value == "売掛金"
    assert wb["04_仕訳帳"]["G7"].value == 30000
    # 試算表の数式が値で潰されていない
    d6 = wb["05_試算表"]["D6"].value
    assert isinstance(d6, str) and d6.startswith("=SUMIFS")
    # 試算表 対象月セット
    assert wb["05_試算表"]["B3"].value == "2026/07"


def test_validator_reports_ok(tmp_path):
    out = tmp_path / "wb.xlsx"
    path = WorkbookWriter().write("2026-07", _journal(), [], [], output_path=out)
    checks = validate(path)
    assert all(c["status"] == "OK" for c in checks), checks


def test_reports_written(tmp_path):
    vp = tmp_path / "validation_report.md"
    validation_report.write("2026-07", [], [], {"all_ok": True}, vp)
    assert vp.exists() and "検証レポート" in vp.read_text(encoding="utf-8")

    rr = tmp_path / "receipt_review_report.csv"
    receipt_review_report.write([], set(), rr)
    assert rr.exists()
