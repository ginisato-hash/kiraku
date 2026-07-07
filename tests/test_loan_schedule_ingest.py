"""Phase H-2: 返済予定表CSV取込のバリデーション。"""
from yuge_finance.ingest import loan_schedule
from yuge_finance.normalize.schema import LoanScheduleEntry


def test_comment_rows_are_skipped(tmp_path, monkeypatch):
    from yuge_finance import config
    monkeypatch.setattr(config, "IMPORTS_DIR", tmp_path)
    d = tmp_path / "loan_repayment_schedule"
    d.mkdir()
    (d / "s.csv").write_text(
        "loan_id,lender,liability_account,payment_date,total_payment,principal_payment,"
        "interest_payment,ending_balance,bank_description_match,memo\n"
        "# 記入例:コメント行\n"
        "L1,きらやか銀行,長期借入金,2026-06-25,650000,600000,50000,41320000,長期借入金返済,\n",
        encoding="utf-8")
    records = loan_schedule.load("2026-06")
    assert len(records) == 1
    assert records[0].lender == "きらやか銀行"


def test_validate_flags_invalid_liability_account_and_mismatch():
    good = LoanScheduleEntry(loan_id="L1", lender="A", liability_account="長期借入金",
                             payment_date="2026-06-25", total_payment=100,
                             principal_payment=90, interest_payment=10).finalize()
    bad_account = LoanScheduleEntry(loan_id="L2", lender="B", liability_account="仕入代金",
                                    payment_date="2026-06-25", total_payment=100,
                                    principal_payment=90, interest_payment=10).finalize()
    bad_sum = LoanScheduleEntry(loan_id="L3", lender="C", liability_account="長期借入金",
                                payment_date="2026-06-25", total_payment=100,
                                principal_payment=80, interest_payment=10).finalize()
    issues = loan_schedule.validate([good, bad_account, bad_sum])
    assert all(i["severity"] == "critical" for i in issues)
    kinds = {i["issue"] for i in issues}
    assert "liability_account不正値" in kinds
    assert "principal_payment+interest_payment != total_payment" in kinds
    assert len(issues) == 2  # goodは問題なし
