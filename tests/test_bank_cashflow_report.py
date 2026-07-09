"""銀行口座実績 月次CF集計・固定費/変動費更新候補のテスト。

config/fixed_variable_model.yml は候補生成では絶対に書き換えない。
"""
import json

from yuge_finance import config, db
from yuge_finance.ingest import bank_actuals
from yuge_finance.normalize.schema import BankActualTransaction
from yuge_finance.reports import bank_cashflow_report


def _seed(conn):
    txns = [
        BankActualTransaction(bank_account_key="X", transaction_date="2026-06-01",
                              counterparty_raw="ZHｾﾞｲﾘｼﾎｳｼﾕｳ", memo_raw="ZHｾﾞｲﾘｼﾎｳｼﾕｳ",
                              withdrawal_amount=39600, balance_after=100000, row_number=1).finalize(),
        BankActualTransaction(bank_account_key="X", transaction_date="2026-06-02",
                              counterparty_raw="ﾗｸﾃﾝｸﾞﾙ-ﾌﾟ(ｶ", memo_raw="ﾗｸﾃﾝｸﾞﾙ-ﾌﾟ(ｶ",
                              deposit_amount=248452, balance_after=348452, row_number=2).finalize(),
        BankActualTransaction(bank_account_key="X", transaction_date="2026-06-03",
                              counterparty_raw="謎の相手", memo_raw="謎の相手",
                              withdrawal_amount=1000, balance_after=347452, row_number=3).finalize(),
    ]
    db.upsert(conn, "bank_actual_transactions", txns)


def test_monthly_summary_structure_and_totals(tmp_path):
    conn = db.connect(tmp_path / "t.sqlite")
    _seed(conn)
    txns = bank_actuals.load_all(conn)
    summary = bank_cashflow_report.build_cashflow_summary(txns, "2026-06")
    assert summary["month"] == "2026-06"
    assert summary["total_deposits"] == 248452
    assert summary["total_withdrawals"] == 40600
    assert summary["net_cashflow"] == 248452 - 40600
    assert any(c["cost_model_category"] == "ota_receivable_collection"
              for c in summary["revenue_collection_candidates"])
    assert any(c["requires_review"] for c in summary["unknown_or_review_required"])
    conn.close()


def test_write_all_never_touches_fixed_variable_model_yml(tmp_path):
    conn = db.connect(tmp_path / "t.sqlite")
    _seed(conn)
    yml_path = config.CONFIG_DIR / "fixed_variable_model.yml"
    before = yml_path.read_bytes()
    bi_dir = tmp_path / "bi"
    bank_cashflow_report.write_all(conn, bi_dir, month="2026-06")
    after = yml_path.read_bytes()
    assert before == after, "候補生成でconfig/fixed_variable_model.ymlを書き換えてはいけない"

    assert (bi_dir / "bank_cashflow_summary.json").exists()
    assert (bi_dir / "bank_cost_model_candidates.json").exists()
    candidates = json.loads(
        (bi_dir / "fixed_variable_model_update_candidates.json").read_text(encoding="utf-8"))
    assert "generated_at_jst" in candidates
    assert any(c["cost_model_category"] == "tax_accountant_fee"
              for c in candidates["fixed_cost_candidates"])
    conn.close()


def test_ota_revenue_candidates_not_merged_with_beds24_fields(tmp_path):
    conn = db.connect(tmp_path / "t.sqlite")
    _seed(conn)
    txns = bank_actuals.load_all(conn)
    summary = bank_cashflow_report.build_cashflow_summary(txns, "2026-06")
    assert "beds24_revenue_net_for_bi" not in summary
    assert "beds24_stay_month_revenue_excluding_cancelled" not in summary


def test_compute_bi_fields_empty_db_reports_not_imported(tmp_path):
    conn = db.connect(tmp_path / "t.sqlite")
    fields = bank_cashflow_report.compute_bi_fields(conn)
    assert fields["bank_csv_import_status"] == "未取込"
    assert fields["bank_csv_imported_rows"] == 0
    conn.close()


def test_compute_bi_fields_always_includes_bank_fields_source_even_without_publish_flag(tmp_path):
    """bank_fields_sourceはpublish-bi-r2の引き継ぎoption有無に関わらず常にsnapshotへ出る。"""
    conn = db.connect(tmp_path / "t.sqlite")
    fields = bank_cashflow_report.compute_bi_fields(conn)
    assert fields["bank_fields_source"] == "not_available"
    conn.close()


def test_compute_bi_fields_after_import_reports_latest_balance(tmp_path):
    conn = db.connect(tmp_path / "t.sqlite")
    _seed(conn)
    fields = bank_cashflow_report.compute_bi_fields(conn)
    assert fields["bank_csv_import_status"] == "imported"
    assert fields["bank_csv_imported_rows"] == 3
    assert fields["bank_actual_latest_balance"] == 347452.0
    assert fields["bank_actual_latest_balance_date"] == "2026-06-03"
    assert fields["bank_fields_source"] == "current_import"
    conn.close()
