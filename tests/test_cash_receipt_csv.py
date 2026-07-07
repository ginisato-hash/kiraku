from yuge_finance import config
from yuge_finance.ingest import cash_receipt_csv


def test_from_row_defaults():
    row = {"transaction_date": "2026/07/03", "transaction_type": "現金支払",
           "amount": "2480", "category": "消耗品費", "vendor": "コメリ",
           "receipt_file": "r1.jpg"}
    tx = cash_receipt_csv._from_row(row, "c.csv")
    assert tx.transaction_date == "2026-07-03"
    assert tx.payment_method == "現金"
    assert tx.review_status == "needs_review"   # 既定はneeds_review
    assert tx.amount == 2480.0


def test_load_and_image_reconcile(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "IMPORTS_DIR", tmp_path)
    base = tmp_path / "cash_receipts"
    (base / "csv").mkdir(parents=True)
    (base / "reviewed").mkdir()
    (base / "images").mkdir()
    (base / "images" / "ok.jpg").write_text("x")
    (base / "csv" / "c.csv").write_text(
        "transaction_date,transaction_type,amount,category,vendor,receipt_file,review_status\n"
        "2026-07-03,現金支払,2480,消耗品費,コメリ,ok.jpg,approved\n"
        "2026-07-04,現金支払,5000,消耗品費,謎,missing.jpg,needs_review\n",
        encoding="utf-8")
    txns = cash_receipt_csv.load("2026-07")
    assert len(txns) == 2
    issues = cash_receipt_csv.reconcile_images(txns)
    assert len(issues) == 1
    assert issues[0]["receipt_file"] == "missing.jpg"
