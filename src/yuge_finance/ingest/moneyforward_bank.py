"""Kiraku's two MF bank feeds, isolated from journals and public BI data.

The MF API has no bank balance or bank refresh timestamp. Neither is inferred
from transaction totals. Raw revisions are retained; a missing row is flagged,
never silently deleted. A sync commits only after both accounts are complete.
"""
from __future__ import annotations

import calendar
import hashlib
import json
import os
import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote
from zoneinfo import ZoneInfo

from .. import config
from ..api.moneyforward_client import MoneyForwardClient, MoneyForwardError

ACCOUNT_LABELS = {"kirayaka": "きらやか銀行", "yamagata": "山形銀行"}
STATUSES = {"excluded", "none", "registered", "modified", "new_voucher_attached"}
DEFAULT_STATE_DIR = config.DATA_DIR / "private" / "moneyforward"


class BankSyncError(RuntimeError):
    """A deliberately non-sensitive operational error."""


def _date(value: str) -> date:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise BankSyncError("日付は YYYY-MM-DD 形式で指定してください")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise BankSyncError("有効な日付を指定してください") from None


def _id(value: Any) -> str:
    if not isinstance(value, str) or not value or len(value) > 2048:
        raise BankSyncError("MFの識別子が不正です")
    decoded = unquote(value)
    if any(ord(c) < 32 for c in decoded):
        raise BankSyncError("MFの識別子が不正です")
    return decoded


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


@dataclass(frozen=True)
class AccountBinding:
    key: str
    connected_account_id: str
    connected_sub_account_id: str | None


def load_bindings(value: str | None = None) -> list[AccountBinding]:
    """Explicit IDs only: service display names never select a bank account."""
    value = config.env("MF_BANK_ACCOUNTS_JSON") if value is None else value
    try:
        parsed = json.loads(value)
    except (ValueError, TypeError):
        raise BankSyncError("MF_BANK_ACCOUNTS_JSON に2口座のID対応を設定してください") from None
    if not isinstance(parsed, dict) or set(parsed) != set(ACCOUNT_LABELS):
        raise BankSyncError("MF_BANK_ACCOUNTS_JSON は kirayaka / yamagata の2口座に限定してください")
    bindings = []
    identities = set()
    for key in ACCOUNT_LABELS:
        item = parsed[key]
        if not isinstance(item, dict) or set(item) != {"connected_account_id", "connected_sub_account_id"}:
            raise BankSyncError("口座設定には連携サービスIDと配下口座IDを指定してください")
        parent = _id(item["connected_account_id"])
        child = None if item["connected_sub_account_id"] is None else _id(item["connected_sub_account_id"])
        identity = (parent, child)
        if identity in identities:
            raise BankSyncError("同じMF口座が二重に設定されています")
        identities.add(identity)
        bindings.append(AccountBinding(key, item["connected_account_id"], item["connected_sub_account_id"]))
    # A parent-only selection overlaps every child selection under that parent.
    for b in bindings:
        if b.connected_sub_account_id is None and sum(_id(x.connected_account_id) == _id(b.connected_account_id) for x in bindings) > 1:
            raise BankSyncError("連携サービス全体と配下口座を重複して設定できません")
    return bindings


def _validate_office(office: dict, office_code: str) -> None:
    if not isinstance(office, dict) or office.get("code") != office_code:
        raise BankSyncError("MF事業者が設定と一致しません")


def _validate_accounts(accounts: list[dict], bindings: list[AccountBinding]) -> None:
    for binding in bindings:
        parents = [a for a in accounts if _id(a.get("id")) == _id(binding.connected_account_id)]
        if len(parents) != 1 or parents[0].get("is_manual") is not False:
            raise BankSyncError("設定した銀行サービスをMFで一意に確認できません")
        children = parents[0].get("connected_sub_accounts")
        if not isinstance(children, list):
            raise BankSyncError("MF口座一覧の形式が不正です")
        if binding.connected_sub_account_id is None:
            if children:
                raise BankSyncError("銀行の配下口座IDを明示してください")
        elif sum(_id(c.get("id")) == _id(binding.connected_sub_account_id) for c in children) != 1:
            raise BankSyncError("設定した配下口座をMFで一意に確認できません")


def _connect(state_dir: Path) -> sqlite3.Connection:
    state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(state_dir, 0o700)
    path = state_dir / "ledger.sqlite"
    conn = sqlite3.connect(path, timeout=30)
    os.chmod(path, 0o600)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sync_runs (
            id TEXT PRIMARY KEY, attempted_at TEXT NOT NULL, completed_at TEXT,
            start_date TEXT NOT NULL, end_date TEXT NOT NULL,
            status TEXT NOT NULL, error_code TEXT
        );
        CREATE TABLE IF NOT EXISTS transactions (
            office_code TEXT NOT NULL, id TEXT NOT NULL, account_key TEXT NOT NULL,
            date TEXT NOT NULL, value INTEGER NOT NULL, side TEXT NOT NULL,
            journalizing_status TEXT NOT NULL, raw_payload TEXT NOT NULL,
            payload_hash TEXT NOT NULL, first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL, sync_run_id TEXT NOT NULL,
            present_in_latest_fetch INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY (office_code, id)
        );
        CREATE INDEX IF NOT EXISTS transactions_account_date ON transactions(account_key, date);
        CREATE TABLE IF NOT EXISTS transaction_versions (
            office_code TEXT NOT NULL, id TEXT NOT NULL, payload_hash TEXT NOT NULL,
            raw_payload TEXT NOT NULL, first_seen_at TEXT NOT NULL, sync_run_id TEXT NOT NULL,
            PRIMARY KEY (office_code, id, payload_hash)
        );
    """)
    return conn


def _scope(conn: sqlite3.Connection, office_code: str, bindings: list[AccountBinding]) -> dict:
    canonical = [{"key": b.key, "parent": _id(b.connected_account_id),
                  "child": _id(b.connected_sub_account_id) if b.connected_sub_account_id is not None else None}
                 for b in bindings]
    values = {"property_id": "kiraku", "schema_version": "1", "office_code": office_code,
              "bindings_sha256": hashlib.sha256(_json(canonical).encode()).hexdigest()}
    existing = dict(conn.execute("SELECT key, value FROM metadata").fetchall())
    if existing and any(existing.get(k) != v for k, v in values.items()):
        raise BankSyncError("保存済みの事業者・口座設定と一致しません。別口座の履歴を混在させず確認してください")
    if not existing and conn.execute("SELECT 1 FROM transactions LIMIT 1").fetchone():
        raise BankSyncError("保存済みMF明細の事業者情報を確認できません")
    return values


def _fiscal_start(office: dict, end: date) -> date:
    periods = office.get("accounting_periods")
    if not isinstance(periods, list):
        raise BankSyncError("MF会計期間の応答形式が不正です")
    starts = []
    for period in periods:
        if not isinstance(period, dict):
            raise BankSyncError("MF会計期間の応答形式が不正です")
        first, last = _date(period.get("start_date")), _date(period.get("end_date"))
        if first > last:
            raise BankSyncError("MF会計期間の開始日と終了日が不正です")
        if first <= end <= last:
            starts.append(first)
    if len(starts) != 1:
        raise BankSyncError("対象日のMF会計期間を確認できません。取得開始日を明示してください")
    return starts[0]


def _normalize(row: dict, binding: AccountBinding, start: date, end: date) -> dict:
    if not isinstance(row, dict):
        raise BankSyncError("MF明細の形式が不正です")
    identity = _id(row.get("id"))
    day = _date(row.get("date"))
    if not start <= day <= end:
        raise BankSyncError("取得期間外のMF明細が返されました")
    value = row.get("value")
    if type(value) is not int or not 0 <= value <= 999999999999:
        raise BankSyncError("MF明細の円金額が不正です")
    if row.get("side") not in {"INCOME", "EXPENSE"} or row.get("journalizing_status") not in STATUSES:
        raise BankSyncError("MF明細の収支・仕訳状態が不正です")
    parent = _id(row.get("connected_account_id"))
    child = row.get("connected_sub_account_id")
    child = None if child is None else _id(child)
    expected_child = None if binding.connected_sub_account_id is None else _id(binding.connected_sub_account_id)
    if parent != _id(binding.connected_account_id) or child != expected_child:
        raise BankSyncError("許可した2口座以外の明細が返されました")
    if not isinstance(row.get("voucher_file_ids"), list) or any(not isinstance(v, str) for v in row["voucher_file_ids"]):
        raise BankSyncError("MF明細の証憑ID形式が不正です")
    for key in ("content", "memo"):
        if row.get(key) is not None and not isinstance(row[key], str):
            raise BankSyncError("MF明細の摘要形式が不正です")
    raw = _json(row)
    return {"id": identity, "account_key": binding.key, "date": day.isoformat(),
            "value": value, "side": row["side"], "journalizing_status": row["journalizing_status"],
            "raw_payload": raw, "payload_hash": hashlib.sha256(raw.encode()).hexdigest()}


def _windows(start: date, end: date):
    while start <= end:
        last = min(end, start + timedelta(days=365))
        yield start, last
        start = last + timedelta(days=1)


def _write_private_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(path.name + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def discover(*, client: MoneyForwardClient | None = None, out: Path | None = None) -> dict:
    """Write sensitive account selectors locally, never into Actions stdout."""
    client = client or MoneyForwardClient()
    office = client.get_office()
    _validate_office(office, client.office_code)
    accounts = client.list_connected_accounts()
    path = out or DEFAULT_STATE_DIR / "discovery.json"
    _write_private_json(path, {"office": office, "connected_accounts": accounts})
    return {"output": str(path), "service_count": len(accounts)}


def sync(*, state_dir: Path = DEFAULT_STATE_DIR, client: MoneyForwardClient | None = None,
         bindings: list[AccountBinding] | None = None, start_date: str | None = None,
         end_date: str | None = None, full: bool = False, now: datetime | None = None) -> dict:
    client = client or MoneyForwardClient()
    bindings = bindings or load_bindings()
    if {b.key for b in bindings} != set(ACCOUNT_LABELS) or len(bindings) != 2:
        raise BankSyncError("喜らくの2口座のみを指定してください")
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise BankSyncError("同期時刻にはタイムゾーンが必要です")
    stamp = now.astimezone(timezone.utc).isoformat(timespec="seconds")
    today = now.astimezone(ZoneInfo("Asia/Tokyo")).date()
    end = _date(end_date) if end_date else today
    if end > today:
        raise BankSyncError("未来の期間を取得済みにはできません")
    start = _date(start_date) if start_date else max(date(2000, 1, 1), end - timedelta(days=89))
    conn = _connect(Path(state_dir))
    run_id = str(uuid.uuid4())
    scope_validated = False
    try:
        scope = _scope(conn, client.office_code, bindings)
        scope_validated = True
        latest = conn.execute("SELECT * FROM sync_runs WHERE status = 'success' ORDER BY completed_at DESC, rowid DESC LIMIT 1").fetchone()
        office = client.get_office()
        _validate_office(office, client.office_code)
        if not start_date and (full or latest is None):
            start = _fiscal_start(office, end)
        if start > end:
            raise BankSyncError("取得開始日は終了日以前にしてください")
        if (end - start).days > 3660:
            raise BankSyncError("取得期間は10年以内に分けてください")
        _validate_accounts(client.list_connected_accounts(), bindings)
        rows = []
        identities: set[str] = set()
        for binding in bindings:
            query = ({"connected_sub_account_id": binding.connected_sub_account_id}
                     if binding.connected_sub_account_id is not None
                     else {"connected_account_id": binding.connected_account_id})
            for first, last in _windows(start, end):
                for row in client.list_transactions(first.isoformat(), last.isoformat(), **query):
                    record = _normalize(row, binding, first, last)
                    if record["id"] in identities:
                        raise BankSyncError("取得結果に同一明細IDが重複しています。再照合が必要です")
                    identities.add(record["id"])
                    rows.append(record)
        with conn:
            # Lock first-time scope only with the first complete successful sync.
            # A typo during onboarding can be fixed without discarding any data.
            conn.executemany("INSERT OR IGNORE INTO metadata(key, value) VALUES (?, ?)", scope.items())
            conn.execute("INSERT INTO sync_runs VALUES (?, ?, ?, ?, ?, 'success', NULL)",
                         (run_id, stamp, stamp, start.isoformat(), end.isoformat()))
            conn.execute("UPDATE transactions SET present_in_latest_fetch = 0 WHERE office_code = ? AND date BETWEEN ? AND ?",
                         (client.office_code, start.isoformat(), end.isoformat()))
            for row in rows:
                old = conn.execute("SELECT account_key FROM transactions WHERE office_code = ? AND id = ?",
                                   (client.office_code, row["id"])).fetchone()
                if old and old["account_key"] != row["account_key"]:
                    raise BankSyncError("既存明細の口座が変更されています。手動照合が必要です")
                conn.execute("""INSERT OR IGNORE INTO transaction_versions VALUES (?, ?, ?, ?, ?, ?)""",
                             (client.office_code, row["id"], row["payload_hash"], row["raw_payload"], stamp, run_id))
                conn.execute("""INSERT INTO transactions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                    ON CONFLICT(office_code, id) DO UPDATE SET
                      date=excluded.date, value=excluded.value, side=excluded.side,
                      journalizing_status=excluded.journalizing_status, raw_payload=excluded.raw_payload,
                      payload_hash=excluded.payload_hash, last_seen_at=excluded.last_seen_at,
                      sync_run_id=excluded.sync_run_id, present_in_latest_fetch=1""",
                             (client.office_code, row["id"], row["account_key"], row["date"], row["value"],
                              row["side"], row["journalizing_status"], row["raw_payload"], row["payload_hash"],
                              stamp, stamp, run_id))
        summary = build_summary(conn, now=now)
        _write_private_json(Path(state_dir) / "summary.json", summary)
        return {"status": "success", "account_count": 2, "fetched_count": len(rows),
                "last_success_at": stamp, "start_date": start.isoformat(), "end_date": end.isoformat()}
    except (MoneyForwardError, BankSyncError, sqlite3.Error, OSError, ValueError, TypeError) as exc:
        # Existing data is retained, and failure never advances last_success_at.
        # A configuration/scope mismatch must not mutate the previous scope.
        if scope_validated:
            error_code = ("mf_api_error" if isinstance(exc, MoneyForwardError) else
                          "storage_error" if isinstance(exc, (sqlite3.Error, OSError)) else "validation_error")
            try:
                with conn:
                    conn.execute("""INSERT INTO sync_runs VALUES (?, ?, NULL, ?, ?, 'failed', ?)
                        ON CONFLICT(id) DO UPDATE SET status='failed', error_code=excluded.error_code""",
                                 (run_id, stamp, start.isoformat(), end.isoformat(), error_code))
                _write_private_json(Path(state_dir) / "summary.json", build_summary(conn, now=now))
            except (sqlite3.Error, OSError):
                # A damaged/unwritable disk cannot reliably publish failure state.
                # The workflow refuses to publish an unchanged summary.
                pass
        if isinstance(exc, (MoneyForwardError, BankSyncError)):
            raise
        raise BankSyncError("MF同期のローカル保存または応答検証に失敗しました") from None
    finally:
        conn.close()


def _coverage(ranges: list[tuple[date, date]], start: date, end: date) -> str:
    cursor = start
    overlaps = False
    for left, right in sorted(ranges):
        if left > end or right < start:
            continue
        overlaps = True
        if left > cursor:
            return "partial"
        cursor = max(cursor, right + timedelta(days=1))
        if cursor > end:
            return "complete"
    return "partial" if overlaps else "not_fetched"


def build_summary(conn: sqlite3.Connection, *, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(ZoneInfo("Asia/Tokyo")).date()
    runs = conn.execute("SELECT * FROM sync_runs ORDER BY attempted_at DESC, rowid DESC").fetchall()
    successful = [r for r in runs if r["status"] == "success"]
    latest = runs[0] if runs else None
    last_success = successful[0] if successful else None
    ranges = [(_date(r["start_date"]), _date(r["end_date"])) for r in successful]
    first = min((a for a, _ in ranges), default=today.replace(day=1))
    months = []
    cursor = first.replace(day=1)
    while cursor <= today:
        months.append(cursor.strftime("%Y-%m"))
        cursor = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
    accounts = []
    for key, label in ACCOUNT_LABELS.items():
        rows = conn.execute("SELECT date, value, side, present_in_latest_fetch FROM transactions WHERE account_key = ?", (key,)).fetchall()
        observed = [r for r in rows if r["present_in_latest_fetch"]]
        monthly = []
        for month in months:
            month_start = _date(month + "-01")
            month_end = min(today, month_start.replace(day=calendar.monthrange(month_start.year, month_start.month)[1]))
            selected = [r for r in observed if r["date"].startswith(month)]
            income = sum(r["value"] for r in selected if r["side"] == "INCOME")
            expense = sum(r["value"] for r in selected if r["side"] == "EXPENSE")
            monthly.append({"month": month, "income_jpy": income, "expense_jpy": expense,
                            "net_jpy": income - expense, "transaction_count": len(selected),
                            "unverified_count": sum(not r["present_in_latest_fetch"] and r["date"].startswith(month) for r in rows),
                            "coverage": _coverage(ranges, month_start, month_end)})
        accounts.append({"key": key, "label": label, "balance_jpy": None,
                         "balance_status": "not_provided_by_api",
                         "last_fetched_at": last_success["completed_at"] if last_success else None,
                         "latest_transaction_date": max((r["date"] for r in observed), default=None),
                         "transaction_count": len(observed),
                         "unverified_count": len(rows) - len(observed), "months": monthly})
    return {"schema_version": 1, "source": "moneyforward", "property_id": "kiraku",
            "generated_at": now.astimezone(timezone.utc).isoformat(timespec="seconds"),
            "period": {"start_date": first.isoformat(), "end_date": max((b for _, b in ranges), default=today).isoformat()},
            "sync": {"status": latest["status"] if latest else "not_connected",
                     "attempted_at": latest["attempted_at"] if latest else None,
                     "last_success_at": last_success["completed_at"] if last_success else None,
                     "error_code": latest["error_code"] if latest else None},
            "balance_supported": False, "bank_sync_time_supported": False, "accounts": accounts}
