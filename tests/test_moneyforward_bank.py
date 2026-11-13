"""Synthetic-only integration tests for the private two-bank read model."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

import pytest

from yuge_finance.api.moneyforward_client import MoneyForwardError
from yuge_finance.ingest.moneyforward_bank import BankSyncError, discover, load_bindings, sync

NOW = datetime(2026, 11, 13, 3, tzinfo=timezone.utc)
LATER = datetime(2026, 11, 13, 4, tzinfo=timezone.utc)
MAPPING = {
    "kirayaka": {"connected_account_id": "parent-a", "connected_sub_account_id": "child-a%2Ftest"},
    "yamagata": {"connected_account_id": "parent-b", "connected_sub_account_id": "child-b"},
}
BINDINGS = load_bindings(json.dumps(MAPPING))


def transaction(identity="txn-a", account="kirayaka", day="2026-11-01", value=1000, side="INCOME", status="none"):
    return {"id": identity, "date": day, "value": value, "side": side,
            "content": "TEST PRIVATE PAYEE", "memo": None, "journalizing_status": status,
            **MAPPING[account], "voucher_file_ids": []}


class FakeClient:
    office_code = "TEST-0001"

    def __init__(self, rows=None, fail_account=None):
        self.rows = rows if rows is not None else [transaction(), transaction("txn-b", "yamagata", value=300, side="EXPENSE")]
        self.fail_account = fail_account
        self.calls = []

    def get_office(self):
        return {"code": self.office_code, "name": "TEST PROPERTY", "accounting_periods": [
            {"start_date": "2026-06-01", "end_date": "2027-05-31"}]}

    def list_connected_accounts(self):
        return [{"id": v["connected_account_id"], "is_manual": False,
                 "connected_sub_accounts": [{"id": v["connected_sub_account_id"], "name": "TEST ACCOUNT"}]}
                for v in MAPPING.values()]

    def list_transactions(self, first, last, **query):
        self.calls.append((first, last, query))
        if query.get("connected_sub_account_id") == self.fail_account:
            raise MoneyForwardError("MF API request failed")
        return deepcopy([r for r in self.rows if r["connected_sub_account_id"] == query["connected_sub_account_id"]
                         and first <= r["date"] <= last])


def read_summary(tmp_path):
    return json.loads((tmp_path / "summary.json").read_text())


def db_rows(tmp_path, table):
    with sqlite3.connect(tmp_path / "ledger.sqlite") as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(f"SELECT * FROM {table}")]


def run(tmp_path, **kwargs):
    return sync(state_dir=tmp_path, bindings=BINDINGS, now=kwargs.pop("now", NOW), **kwargs)


def test_first_sync_fetches_fiscal_period_two_exact_accounts_and_no_financial_journals(tmp_path):
    client = FakeClient()
    result = run(tmp_path, client=client)
    assert result["fetched_count"] == 2
    assert len(client.calls) == 2
    assert all(c[0] == "2026-06-01" and c[1] == "2026-11-13" for c in client.calls)
    assert {c[2]["connected_sub_account_id"] for c in client.calls} == {v["connected_sub_account_id"] for v in MAPPING.values()}
    summary = read_summary(tmp_path)
    assert summary["sync"]["status"] == "success"
    assert summary["balance_supported"] is False
    assert summary["bank_sync_time_supported"] is False
    a, b = summary["accounts"]
    assert a["balance_jpy"] is None and b["balance_jpy"] is None
    assert a["months"][-1] == {"month": "2026-11", "income_jpy": 1000, "expense_jpy": 0,
                               "net_jpy": 1000, "transaction_count": 1, "unverified_count": 0, "coverage": "complete"}
    assert b["months"][-1]["net_jpy"] == -300
    encoded = json.dumps(summary)
    for secret in ("TEST PRIVATE PAYEE", "parent-a", "child-a", "txn-a", "TEST-0001", "raw_payload", "memo"):
        assert secret not in encoded
    with sqlite3.connect(tmp_path / "ledger.sqlite") as conn:
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert not names & {"journal_entries", "bank_transactions", "bank_actual_transactions"}
    assert (tmp_path / "ledger.sqlite").stat().st_mode & 0o777 == 0o600


def test_repeat_is_idempotent_and_changed_payload_updates_without_losing_raw_revision(tmp_path):
    run(tmp_path, client=FakeClient())
    run(tmp_path, client=FakeClient(), now=LATER)
    assert len(db_rows(tmp_path, "transactions")) == 2
    assert len(db_rows(tmp_path, "transaction_versions")) == 2
    corrected = transaction(value=1200, status="modified")
    corrected["voucher_file_ids"] = ["test-voucher"]
    run(tmp_path, client=FakeClient([corrected, transaction("txn-b", "yamagata", value=300, side="EXPENSE")]), now=LATER)
    rows = db_rows(tmp_path, "transactions")
    first = next(r for r in rows if r["id"] == "txn-a")
    assert first["value"] == 1200
    assert first["journalizing_status"] == "modified"
    assert first["first_seen_at"] == NOW.isoformat(timespec="seconds")
    assert len(db_rows(tmp_path, "transaction_versions")) == 3


def test_second_account_failure_does_not_commit_first_or_advance_success(tmp_path):
    run(tmp_path, client=FakeClient())
    before = db_rows(tmp_path, "transactions")
    client = FakeClient([transaction(value=999999)], fail_account="child-b")
    with pytest.raises(MoneyForwardError):
        run(tmp_path, client=client, now=LATER)
    assert db_rows(tmp_path, "transactions") == before
    summary = read_summary(tmp_path)
    assert summary["sync"] == {"status": "failed", "attempted_at": LATER.isoformat(timespec="seconds"),
                               "last_success_at": NOW.isoformat(timespec="seconds"), "error_code": "mf_api_error"}
    assert summary["accounts"][0]["last_fetched_at"] == NOW.isoformat(timespec="seconds")


def test_same_day_same_amount_legitimate_rows_retained_and_all_statuses_count_as_cash(tmp_path):
    rows = [transaction("t" + str(i), value=100, status=status) for i, status in enumerate(
        ["excluded", "none", "registered", "modified", "new_voucher_attached"])]
    run(tmp_path, client=FakeClient(rows))
    assert len(db_rows(tmp_path, "transactions")) == 5
    assert read_summary(tmp_path)["accounts"][0]["months"][-1]["income_jpy"] == 500


def test_missing_row_retains_audit_and_is_flagged_then_recovers(tmp_path):
    run(tmp_path, client=FakeClient())
    run(tmp_path, client=FakeClient([]), now=LATER)
    assert len(db_rows(tmp_path, "transactions")) == 2
    a = read_summary(tmp_path)["accounts"][0]
    assert a["unverified_count"] == 1
    assert a["months"][-1]["income_jpy"] == 0
    assert a["months"][-1]["unverified_count"] == 1
    run(tmp_path, client=FakeClient(), now=LATER)
    assert read_summary(tmp_path)["accounts"][0]["unverified_count"] == 0


def test_partial_month_is_not_complete_and_old_month_not_current(tmp_path):
    run(tmp_path, client=FakeClient([transaction(day="2026-10-25")]), start_date="2026-10-20", end_date="2026-10-31")
    a = read_summary(tmp_path)["accounts"][0]
    assert a["months"][-2]["coverage"] == "partial"
    assert a["months"][-1]["month"] == "2026-11"
    assert a["months"][-1]["coverage"] == "not_fetched"
    assert a["months"][-1]["income_jpy"] == 0


def test_incremental_overlaps_90_days_and_full_rechecks_fiscal_start(tmp_path):
    run(tmp_path, client=FakeClient())
    client = FakeClient()
    run(tmp_path, client=client, now=LATER)
    assert client.calls[0][0] == "2026-08-16"
    client.calls.clear()
    run(tmp_path, client=client, now=LATER, full=True)
    assert client.calls[0][0] == "2026-06-01"


def test_scope_change_fails_without_mutating_existing_state(tmp_path):
    run(tmp_path, client=FakeClient())
    summary = (tmp_path / "summary.json").read_bytes()
    runs = db_rows(tmp_path, "sync_runs")
    changed = deepcopy(MAPPING)
    changed["kirayaka"]["connected_sub_account_id"] = "other-child"
    with pytest.raises(BankSyncError, match="保存済み"):
        sync(state_dir=tmp_path, client=FakeClient(), bindings=load_bindings(json.dumps(changed)), now=LATER)
    assert db_rows(tmp_path, "sync_runs") == runs
    assert (tmp_path / "summary.json").read_bytes() == summary


def test_first_account_typo_can_be_fixed_before_first_success(tmp_path):
    wrong = deepcopy(MAPPING)
    wrong["kirayaka"]["connected_sub_account_id"] = "typo"
    with pytest.raises(BankSyncError):
        sync(state_dir=tmp_path, client=FakeClient(), bindings=load_bindings(json.dumps(wrong)), now=NOW)
    assert db_rows(tmp_path, "metadata") == []
    run(tmp_path, client=FakeClient(), now=LATER)
    assert read_summary(tmp_path)["sync"]["status"] == "success"
    assert len(db_rows(tmp_path, "transactions")) == 2


def test_database_failure_rolls_back_rows_and_marks_sync_failed(tmp_path):
    run(tmp_path, client=FakeClient())
    before = db_rows(tmp_path, "transactions")
    with sqlite3.connect(tmp_path / "ledger.sqlite") as conn:
        conn.execute("""CREATE TRIGGER reject_test_update BEFORE UPDATE ON transactions
            BEGIN SELECT RAISE(ABORT, 'synthetic storage failure'); END""")
    with pytest.raises(BankSyncError, match="ローカル保存"):
        run(tmp_path, client=FakeClient(), now=LATER)
    assert db_rows(tmp_path, "transactions") == before
    assert read_summary(tmp_path)["sync"]["status"] == "failed"
    assert read_summary(tmp_path)["sync"]["error_code"] == "storage_error"
    assert read_summary(tmp_path)["sync"]["last_success_at"] == NOW.isoformat(timespec="seconds")


def test_malformed_fiscal_period_is_safe_failure(tmp_path):
    client = FakeClient()
    client.get_office = lambda: {"code": client.office_code, "accounting_periods": [{}]}
    with pytest.raises(BankSyncError):
        run(tmp_path, client=client)
    assert read_summary(tmp_path)["sync"]["status"] == "failed"


@pytest.mark.parametrize("patch", [{"value": -1}, {"value": True}, {"value": 1.5}, {"side": "OTHER"},
                                    {"journalizing_status": "new_unknown_status"}, {"connected_account_id": "other-bank"}])
def test_invalid_or_wrong_bank_rows_abort_both_accounts(tmp_path, patch):
    row = transaction()
    row.update(patch)
    with pytest.raises(BankSyncError):
        run(tmp_path, client=FakeClient([row]))
    assert db_rows(tmp_path, "transactions") == []
    assert read_summary(tmp_path)["sync"]["status"] == "failed"


def test_discovery_writes_private_file_without_returning_sensitive_payload(tmp_path):
    output = tmp_path / "discovery.json"
    result = discover(client=FakeClient(), out=output)
    assert result == {"output": str(output), "service_count": 2}
    assert output.stat().st_mode & 0o777 == 0o600
    assert json.loads(output.read_text())["office"]["code"] == "TEST-0001"


def test_two_bank_allowlist_rejects_extra_and_duplicate(tmp_path):
    bad = deepcopy(MAPPING)
    bad["gmo"] = bad["kirayaka"]
    with pytest.raises(BankSyncError):
        load_bindings(json.dumps(bad))
    bad = deepcopy(MAPPING)
    bad["yamagata"] = bad["kirayaka"]
    with pytest.raises(BankSyncError, match="二重"):
        load_bindings(json.dumps(bad))


def test_cli_mf_commands_are_separate_from_close_and_no_credentials_on_commandline():
    from yuge_finance.cli import build_parser
    args = build_parser().parse_args(["sync-mf-banks", "--full"])
    assert args.full is True
    assert not hasattr(args, "api_key")
