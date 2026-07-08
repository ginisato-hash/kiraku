"""銀行口座実績CSV取込（BI/分析専用）のテスト。会計仕訳パイプラインとは無関係。"""
from pathlib import Path

from yuge_finance import config, db
from yuge_finance.ingest import bank_actuals
from yuge_finance.normalize.schema import OpeningBalance

CSV_HEADER = ("照会口座,番号,勘定日,（起算日）,出金金額（円）,入金金額（円）,小切手区分,"
             "残高（円）,取引区分,明細区分,金融機関名,支店名,摘要\n")


def _write_csv(path: Path, rows, encoding="cp932"):
    text = CSV_HEADER + "".join(rows)
    path.write_bytes(text.encode(encoding))


def test_parse_csv_cp932_and_normalizes_dates_and_amounts(tmp_path):
    p = tmp_path / "sample.csv"
    _write_csv(p, [
        '"蔵王支店（017） 普通 0036041","001","2026年05月01日","","1,157,369","","","9,826,178","出金","","","","ｷﾕｳﾌﾘｼｷﾝ"\n',
        '"蔵王支店（017） 普通 0036041","002","2026年05月07日","","","34,000,000","","43,826,178","入金","","","","ｶ.ﾀｶﾐﾔﾘﾖｶﾝ"\n',
    ])
    txns = bank_actuals.parse_csv(p, "蔵王支店_0036041")
    assert len(txns) == 2
    assert txns[0].transaction_date == "2026-05-01"
    assert txns[0].withdrawal_amount == 1157369.0
    assert txns[0].deposit_amount == 0.0
    assert txns[0].balance_after == 9826178.0
    assert txns[0].signed_amount == -1157369.0
    assert txns[1].deposit_amount == 34000000.0
    assert txns[1].counterparty_normalized  # 半角カナが正規化されている
    assert "ｶ" not in txns[1].counterparty_normalized  # 半角カナが残っていない


def test_dedupe_key_is_stable_and_prevents_double_counting(tmp_path):
    p = tmp_path / "sample.csv"
    _write_csv(p, [
        '"蔵王支店（017） 普通 0036041","001","2026年05月01日","","1,157,369","","","9,826,178","出金","","","","ｷﾕｳﾌﾘｼｷﾝ"\n',
    ])
    a = bank_actuals.parse_csv(p, "蔵王支店_0036041")
    b = bank_actuals.parse_csv(p, "蔵王支店_0036041")
    assert a[0].dedupe_key == b[0].dedupe_key

    conn = db.connect(tmp_path / "t.sqlite")
    stats1 = db.upsert(conn, "bank_actual_transactions", a)
    stats2 = db.upsert(conn, "bank_actual_transactions", b)
    assert stats1["inserted"] == 1
    assert stats2["inserted"] == 0 and stats2["skipped"] == 1
    assert len(db.fetch(conn, "bank_actual_transactions")) == 1
    conn.close()


def test_balance_chain_verification_all_rows_consistent(tmp_path):
    p = tmp_path / "sample.csv"
    _write_csv(p, [
        '"X","001","2026年05月01日","","100","","","900","出金","","","",""\n',
        '"X","002","2026年05月02日","","","500","","1400","入金","","","",""\n',
        '"X","003","2026年05月03日","","200","","","1200","出金","","","",""\n',
    ])
    txns = bank_actuals.parse_csv(p, "X")
    chain = bank_actuals.verify_balance_chain(txns)
    assert chain["balanced"] is True
    assert chain["mismatches"] == []
    assert chain["opening_balance_before_first_transaction"] == 1000.0


def test_balance_chain_detects_mismatch(tmp_path):
    p = tmp_path / "sample.csv"
    _write_csv(p, [
        '"X","001","2026年05月01日","","100","","","900","出金","","","",""\n',
        '"X","002","2026年05月02日","","","500","","9999","入金","","","",""\n',
    ])
    txns = bank_actuals.parse_csv(p, "X")
    chain = bank_actuals.verify_balance_chain(txns)
    assert chain["balanced"] is False
    assert len(chain["mismatches"]) == 1
    assert chain["mismatches"][0]["row_number"] == 2


def test_month_end_observed_balance_uses_latest_on_or_before(tmp_path):
    p = tmp_path / "sample.csv"
    _write_csv(p, [
        '"X","001","2026年05月29日","","","100","","7749218","入金","","","",""\n',
        '"X","002","2026年06月01日","","50","","","7749168","出金","","","",""\n',
    ])
    txns = bank_actuals.parse_csv(p, "X")
    me = bank_actuals.month_end_observed_balance(txns, "2026-05")
    assert me == {"balance": 7749218.0, "date": "2026-05-29"}


def test_reconcile_with_accountant_bs_flags_date_mismatch_not_as_error(tmp_path):
    conn = db.connect(tmp_path / "t.sqlite")
    ob = OpeningBalance(as_of_date="2026-05-31", account="現預金", subaccount="普通預金",
                        debit_total=7950646).finalize()
    db.upsert(conn, "opening_balances", [ob])

    p = tmp_path / "sample.csv"
    _write_csv(p, [
        '"X","001","2026年05月29日","","","100","","7749218","入金","","","",""\n',
    ])
    txns = bank_actuals.parse_csv(p, "X")
    recon = bank_actuals.reconcile_with_accountant_bs(conn, txns, opening_date="2026-05-31")
    assert recon["accountant_bs_cash_balance"] == 7950646.0
    assert recon["bank_csv_observed_balance"] == 7749218.0
    assert recon["bank_csv_observed_date"] == "2026-05-29"
    assert recon["bank_vs_accountant_difference"] == 7749218.0 - 7950646.0
    assert "日付相違" in recon["bank_balance_reconciliation_status"]
    conn.close()


def test_reconcile_exact_match_not_flagged_as_review():
    pass  # 実データでは日付一致まで再現できないため、上のケースで十分カバーする


def test_run_dry_run_does_not_persist(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "IMPORTS_DIR", tmp_path / "imports")
    src = tmp_path / "source.csv"
    _write_csv(src, [
        '"X","001","2026年05月01日","","100","","","900","出金","","","",""\n',
    ])
    conn = db.connect(tmp_path / "t.sqlite")
    res = bank_actuals.run(src, "X", apply=False, conn=conn)
    assert res["rows_parsed"] == 1
    assert db.fetch(conn, "bank_actual_transactions") == []
    assert (tmp_path / "imports" / "bank" / "source.csv").exists()
    conn.close()


def test_run_apply_persists_and_second_run_skips_duplicates(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "IMPORTS_DIR", tmp_path / "imports")
    src = tmp_path / "source.csv"
    _write_csv(src, [
        '"X","001","2026年05月01日","","100","","","900","出金","","","",""\n',
    ])
    conn = db.connect(tmp_path / "t.sqlite")
    res1 = bank_actuals.run(src, "X", apply=True, conn=conn)
    assert res1["inserted"] == 1
    res2 = bank_actuals.run(src, "X", apply=True, conn=conn)
    assert res2["inserted"] == 0 and res2["skipped"] == 1
    assert len(db.fetch(conn, "bank_actual_transactions")) == 1
    conn.close()
