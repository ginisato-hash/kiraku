from yuge_finance import db
from yuge_finance.normalize.schema import BankTransaction, CashTransaction


def _bank():
    return [
        BankTransaction(account_name="本店", transaction_date="2026-07-05",
                        description="入金", deposit_amount=1000, balance=1000).finalize(),
        BankTransaction(account_name="本店", transaction_date="2026-07-06",
                        description="出金", withdrawal_amount=500, balance=500).finalize(),
    ]


def test_bank_idempotent(tmp_path):
    conn = db.connect(tmp_path / "t.sqlite")
    s1 = db.upsert(conn, "bank_transactions", _bank())
    assert s1["inserted"] == 2
    s2 = db.upsert(conn, "bank_transactions", _bank())   # 再投入
    assert s2["inserted"] == 0
    assert s2["skipped"] == 2
    assert len(db.fetch(conn, "bank_transactions")) == 2
    conn.close()


def test_cash_idempotent(tmp_path):
    conn = db.connect(tmp_path / "t.sqlite")
    cash = [CashTransaction(transaction_date="2026-07-03", transaction_type="現金支払",
                            amount=2480, vendor="コメリ", review_status="approved").finalize()]
    db.upsert(conn, "cash_transactions", cash)
    db.upsert(conn, "cash_transactions", cash)
    assert len(db.fetch(conn, "cash_transactions")) == 1
    conn.close()
