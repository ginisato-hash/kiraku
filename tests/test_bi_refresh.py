import json
import os
import time

import pytest

from yuge_finance import bi_refresh, config, db, locks, publish
from yuge_finance.locks import FileLock, LockError
from yuge_finance.normalize.schema import BookingRecord


# ---------------- fixtures ----------------
def _fake_fetch(month, conn):
    recs = [BookingRecord(booking_id=f"{month}-A", channel="じゃらんnet",
                          checkin_date=f"{month}-10", checkout_date=f"{month}-11",
                          gross_revenue=30000, status="confirmed").finalize(),
            BookingRecord(booking_id=f"{month}-C", channel="楽天トラベル",
                          checkin_date=f"{month}-12", checkout_date=f"{month}-13",
                          gross_revenue=5000, status="cancelled").finalize()]
    db.upsert(conn, "beds24_bookings", recs)
    return len(recs)


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(config, "ROOT", tmp_path)
    monkeypatch.setattr(bi_refresh, "_fetch_beds24", _fake_fetch)
    conn = db.connect(tmp_path / "ledger.sqlite")
    return tmp_path, conn


# ---------------- refresh ----------------
def test_refresh_only_updates_bi_not_journal_or_excel(isolated):
    tmp, conn = isolated
    bi_refresh.refresh(["2026-06", "2026-07"], conn=conn)
    # BIファイルが出来ている
    snap = tmp / "data" / "output" / "latest" / "bi" / "bi_snapshot.json"
    assert snap.exists()
    # 仕訳帳DBは空（refreshは仕訳を永続化しない）
    assert db.fetch(conn, "journal_entries") == []
    # Excelは生成しない
    assert list((tmp / "data" / "output").rglob("*.xlsx")) == []


def test_refresh_writes_expected_bi_files(isolated):
    tmp, conn = isolated
    bi_refresh.refresh(["2026-06", "2026-07"], conn=conn)
    bi = tmp / "data" / "output" / "latest" / "bi"
    for fn in ["bi_snapshot.json", "bi_daily_timeseries.csv", "bi_monthly_kpi.csv",
               "bi_validation_status.json", "bi_exception_summary.json",
               "bi_refresh_status.json"]:
        assert (bi / fn).exists(), fn
    snap = json.loads((bi / "bi_snapshot.json").read_text(encoding="utf-8"))
    assert snap["beds24_stay_month_revenue_excluding_cancelled"] == 30000
    assert snap["beds24_stay_month_cancelled_revenue"] == 5000
    assert snap["same_month_revenue_comparison_applicable"] is False


def test_refresh_dry_run_writes_nothing(isolated):
    tmp, conn = isolated
    bi_refresh.refresh(["2026-06"], dry_run=True, conn=conn)
    assert not (tmp / "data" / "output" / "latest").exists()


def test_api_failure_does_not_break_existing_bi(isolated, monkeypatch):
    tmp, conn = isolated
    bi_refresh.refresh(["2026-06"], conn=conn)        # 成功して生成
    snap = tmp / "data" / "output" / "latest" / "bi" / "bi_snapshot.json"
    before = snap.read_bytes()

    def boom(month, conn):
        raise RuntimeError("Beds24 API down")
    monkeypatch.setattr(bi_refresh, "_fetch_beds24", boom)
    status = bi_refresh.refresh(["2026-06"], conn=conn)
    assert status["ok"] is False and status["errors"]
    assert snap.read_bytes() == before                # 既存BIは壊れていない


# ---------------- lock ----------------
def test_lock_blocks_second_acquire(tmp_path):
    p = tmp_path / "x.lock"
    l1 = FileLock(p).acquire()
    with pytest.raises(LockError):
        FileLock(p).acquire()
    l1.release()
    FileLock(p).acquire().release()   # 解放後は取れる


def test_stale_lock_is_cleared(tmp_path):
    p = tmp_path / "x.lock"
    p.write_text("999 0\n")
    old = time.time() - 7200
    os.utime(p, (old, old))           # 2時間前
    l = FileLock(p, stale_seconds=3600).acquire()   # staleなので取得できる
    assert p.exists()
    l.release()


# ---------------- publish ----------------
def _seed_latest(latest):
    latest.mkdir(parents=True, exist_ok=True)
    (latest / "bi_snapshot.json").write_text(json.dumps(
        {"month": "2026-06", "revenue_data_status": "速報",
         "same_month_revenue_comparison_applicable": False}), encoding="utf-8")
    (latest / "bi_daily_timeseries.csv").write_text("date\n2026-06-01\n", encoding="utf-8")
    (latest / "bi_monthly_kpi.csv").write_text("month\n2026-06\n", encoding="utf-8")
    (latest / "bi_validation_status.json").write_text('{"all_ok": true}', encoding="utf-8")
    (latest / "bi_exception_summary.json").write_text('{"total": 0}', encoding="utf-8")
    (latest / "bi_refresh_status.json").write_text(
        '{"source_months": ["2026-06","2026-07"], "beds24_last_fetch_at_jst": "x"}',
        encoding="utf-8")


def test_publish_copies_and_makes_manifest(tmp_path):
    latest = tmp_path / "latest"; dst = tmp_path / "web"
    _seed_latest(latest)
    res = publish.publish(latest_dir=latest, dst=dst)
    assert (dst / "bi_snapshot.json").exists()
    assert (dst / "manifest.json").exists()
    man = json.loads((dst / "manifest.json").read_text(encoding="utf-8"))
    assert man["revenue_data_status"] == "速報"
    assert man["same_month_revenue_comparison_applicable"] is False
    assert man["source_months"] == ["2026-06", "2026-07"]
    assert len(man["files"]) == 5 and man["checksum"]


# ---------------- 銀行口座実績レイヤー（BI専用。15分速報更新では取込を行わない）----------------
def test_bi_refresh_source_does_not_ingest_bank_csv():
    """15分速報更新(bi_refresh.py)は銀行CSV取込(ingest-bank-csv/bank_actuals.run)を呼ばない。"""
    src = (config.ROOT / "src" / "yuge_finance" / "bi_refresh.py").read_text(encoding="utf-8")
    assert "bank_actuals" not in src
    assert "ingest-bank-csv" not in src


def test_refresh_reflects_already_ingested_bank_actuals_without_touching_journal(isolated):
    from yuge_finance.normalize.schema import BankActualTransaction
    tmp, conn = isolated
    txn = BankActualTransaction(
        bank_account_key="X", transaction_date="2026-06-01", row_number=1,
        counterparty_raw="ZHｾﾞｲﾘｼﾎｳｼﾕｳ", memo_raw="ZHｾﾞｲﾘｼﾎｳｼﾕｳ",
        withdrawal_amount=39600, balance_after=1000000,
    ).finalize()
    db.upsert(conn, "bank_actual_transactions", [txn])

    bi_refresh.refresh(["2026-06"], conn=conn)

    snap = json.loads(
        (tmp / "data" / "output" / "latest" / "bi" / "bi_snapshot.json").read_text(encoding="utf-8"))
    assert snap["bank_actual_latest_balance"] == 1000000.0
    assert snap["bank_csv_import_status"] == "imported"
    # 15分更新は仕訳・銀行実績データ両方とも書き換えない（読み取り専用）
    assert db.fetch(conn, "journal_entries") == []
    assert len(db.fetch(conn, "bank_actual_transactions")) == 1

    bank_bi = tmp / "data" / "output" / "latest" / "bi"
    assert (bank_bi / "bank_cashflow_summary.json").exists()
    assert (bank_bi / "fixed_variable_model_update_candidates.json").exists()


def test_publish_aborts_on_broken_json(tmp_path):
    latest = tmp_path / "latest"; dst = tmp_path / "web"
    _seed_latest(latest)
    publish.publish(latest_dir=latest, dst=dst)        # 正常公開
    good = (dst / "bi_snapshot.json").read_bytes()
    (latest / "bi_snapshot.json").write_text("{ broken json", encoding="utf-8")
    with pytest.raises(publish.PublishError):
        publish.publish(latest_dir=latest, dst=dst)
    assert (dst / "bi_snapshot.json").read_bytes() == good  # 既存公開を壊さない
