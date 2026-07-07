"""Phase H-1: 開始残高（会計士確定BS 2026-05-31）ロック値の検証。"""
from yuge_finance.ingest import opening_balance


def _load_real_records():
    return opening_balance.load_opening("2026-05-31")


def test_asset_liability_equity_totals_match_lock():
    records = _load_real_records()
    tot = opening_balance.account_totals(records)
    assert tot["asset_total"] == 77335346
    assert tot["liability_total"] == 131088630
    assert tot["equity_total"] == -53753284


def test_asset_equals_liability_plus_equity():
    records = _load_real_records()
    tot = opening_balance.account_totals(records)
    assert tot["asset_total"] == round(tot["liability_total"] + tot["equity_total"], 2)


def test_critical_checks_all_ok():
    records = _load_real_records()
    checks = opening_balance.critical_checks(records, as_of_date="2026-05-31")
    assert all(c["status"] == "OK" for c in checks), checks
    names = {c["check"] for c in checks}
    assert {"opening_balance_asset_total", "opening_balance_liability_total",
            "opening_balance_equity_total", "opening_balance_date",
            "asset_total_eq_liability_plus_equity"} <= names


def test_debit_credit_balanced():
    records = _load_real_records()
    chk = opening_balance.validate(records)
    assert chk["balanced"]


def test_critical_checks_fail_on_wrong_totals():
    from yuge_finance.normalize.schema import OpeningBalance
    bad = [OpeningBalance(as_of_date="2026-05-31", account="現預金",
                          debit_total=100).finalize()]
    checks = opening_balance.critical_checks(bad, as_of_date="2026-05-31")
    fails = [c for c in checks if c["status"] == "critical"]
    assert fails  # 100 != 77,335,346 なので必ず失敗する


def test_pl_ytd_is_reference_only_not_double_counted(tmp_path, monkeypatch):
    """accountant_pl_ytd はDBの journal_entries / trial_balance には一切影響しない参照値。"""
    from yuge_finance import config, db
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    conn = db.connect(tmp_path / "t.sqlite")
    # opening_balances / journal_entries は空のまま
    pl_ytd = opening_balance.accountant_pl_ytd()
    assert isinstance(pl_ytd, dict)
    # journal_entriesテーブルに何も挿入されていないこと（PL参照値がどこにも計上されない）
    assert db.fetch(conn, "journal_entries") == []
    conn.close()
