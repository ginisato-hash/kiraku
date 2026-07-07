from yuge_finance import config, db
from yuge_finance.ingest import opening_balance
from yuge_finance.normalize.schema import OpeningBalance


def test_load_and_validate(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "IMPORTS_DIR", tmp_path)
    d = tmp_path / "opening_balance"
    d.mkdir()
    (d / "opening_balance_2026-05-31.csv").write_text(
        "as_of_date,account,subaccount,debit,credit\n"
        "2026-05-31,現預金,,3000000,\n"
        "2026-05-31,借入金,長期,,3000000\n", encoding="utf-8")
    recs = opening_balance.load_opening()
    assert len(recs) == 2
    chk = opening_balance.validate(recs)
    assert chk["balanced"]
    assert chk["debit"] == chk["credit"] == 3000000


def test_unbalanced_detected():
    recs = [
        OpeningBalance(as_of_date="2026-05-31", account="現預金", debit_total=100).finalize(),
        OpeningBalance(as_of_date="2026-05-31", account="借入金", credit_total=90).finalize(),
    ]
    assert opening_balance.validate(recs)["balanced"] is False


def test_opening_dict_and_idempotent(tmp_path):
    conn = db.connect(tmp_path / "t.sqlite")
    recs = [
        OpeningBalance(as_of_date="2026-05-31", account="現預金", debit_total=3000000).finalize(),
        OpeningBalance(as_of_date="2026-05-31", account="借入金", credit_total=3000000).finalize(),
    ]
    db.upsert(conn, "opening_balances", recs)
    db.upsert(conn, "opening_balances", recs)            # 再投入
    assert len(db.fetch(conn, "opening_balances")) == 2  # 重複しない
    od = opening_balance.opening_dict(conn)
    assert od["現預金"]["debit"] == 3000000
    assert od["借入金"]["credit"] == 3000000
    conn.close()
