import json

from yuge_finance import db, monthly
from yuge_finance.normalize.schema import BankTransaction, CashTransaction
from yuge_finance.reports import bi_export


def _seed(conn):
    db.upsert(conn, "bank_transactions", [
        BankTransaction(account_name="本店", transaction_date="2026-07-05",
                        description="東北電力 電気料金", withdrawal_amount=32000,
                        balance=100000).finalize()])
    db.upsert(conn, "cash_transactions", [
        CashTransaction(transaction_date="2026-07-04", transaction_type="現金入金",
                        amount=500000, category="宿泊売上",
                        review_status="approved").finalize()])


def test_bi_outputs_written(tmp_path):
    conn = db.connect(tmp_path / "t.sqlite")
    _seed(conn)
    ctx = monthly.assemble("2026-07", conn)
    sev = {"all_ok": True, "critical": [], "warnings": []}
    out = bi_export.write_all("2026-07", ctx, checks=[], wb_checks=[],
                              severity=sev, out_dir=tmp_path)
    bi = tmp_path / "bi"
    for fn in ["bi_snapshot.json", "bi_daily_timeseries.csv",
               "bi_validation_status.json", "bi_exception_summary.json"]:
        assert (bi / fn).exists(), fn

    snap = json.loads((bi / "bi_snapshot.json").read_text(encoding="utf-8"))
    assert snap["month"] == "2026-07"
    assert "breakeven" in snap
    assert snap["bs_balanced"] is True          # 複式が一致すればBSは均衡
    assert "rollforward" in snap

    exc = json.loads((bi / "bi_exception_summary.json").read_text(encoding="utf-8"))
    assert "total" in exc and "by_rule" in exc
    conn.close()
