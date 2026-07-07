from yuge_finance import config
from yuge_finance.ingest import bank_csv


def test_from_row_normalization():
    row = {"取引日": "2026/07/05", "摘要": "楽天トラベル入金",
           "入金": "250,000", "出金": "", "残高": "1250000", "取引先": "楽天"}
    tx = bank_csv._from_row(row, "test.csv", "テスト銀行")
    assert tx.transaction_date == "2026-07-05"
    assert tx.deposit_amount == 250000.0
    assert tx.withdrawal_amount == 0.0
    assert tx.amount_signed == 250000.0
    assert tx.balance == 1250000.0
    assert tx.import_hash


def test_withdrawal_signed_negative():
    row = {"取引日": "2026-07-10", "摘要": "電気料金", "出金": "32000", "残高": "1218000"}
    tx = bank_csv._from_row(row, "f.csv", "B")
    assert tx.amount_signed == -32000.0


def test_load_from_imports(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "IMPORTS_DIR", tmp_path)
    bank_dir = tmp_path / "bank"
    bank_dir.mkdir()
    (bank_dir / "a.csv").write_text(
        "取引日,摘要,入金,出金,残高\n2026-07-01,入金,1000,,1000\n"
        "2026-08-01,別月,500,,1500\n", encoding="utf-8")
    rows = bank_csv.load("2026-07")
    assert len(rows) == 1
    assert rows[0].transaction_date == "2026-07-01"
