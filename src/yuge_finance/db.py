"""ledger.sqlite ストア（喜らく単体）。

import_hash の UNIQUE 制約で再投入時の二重計上を防ぐ。
Beds24 予約は booking_id を主キーにし、状態変化は上書き更新する。
"""
from __future__ import annotations

import sqlite3
from dataclasses import asdict, fields
from pathlib import Path
from typing import Iterable, List, Type

from . import config
from .normalize import schema

# テーブル名 -> (dataclass, ユニークキー列, 上書き更新するか)
TABLES = {
    "bank_transactions": (schema.BankTransaction, "import_hash", False),
    "bank_actual_transactions": (schema.BankActualTransaction, "dedupe_key", False),
    "cash_transactions": (schema.CashTransaction, "import_hash", False),
    "beds24_bookings": (schema.BookingRecord, "booking_id", True),
    "manual_adjustments": (schema.ManualAdjustment, "import_hash", False),
    "opening_balances": (schema.OpeningBalance, "import_hash", False),
    "loan_schedule": (schema.LoanScheduleEntry, "import_hash", False),
    "journal_entries": (schema.JournalEntry, "journal_id", True),
}


def _coltype(py_default) -> str:
    return "REAL" if isinstance(py_default, float) else (
        "INTEGER" if isinstance(py_default, int) else "TEXT")


def connect(db_path: Path = None) -> sqlite3.Connection:
    db_path = db_path or config.DB_PATH
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    _ensure_schema(conn)
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    for table, (cls, key, _) in TABLES.items():
        cols = []
        for f in fields(cls):
            ctype = _coltype(f.default if f.default is not None else "")
            constraint = " UNIQUE" if f.name == key else ""
            cols.append(f'"{f.name}" {ctype}{constraint}')
        conn.execute(f'CREATE TABLE IF NOT EXISTS "{table}" ({", ".join(cols)})')
    conn.commit()


def upsert(conn: sqlite3.Connection, table: str, records: Iterable) -> dict:
    """レコード群を投入。新規挿入数と重複スキップ数を返す。"""
    cls, key, replace = TABLES[table]
    colnames = [f.name for f in fields(cls)]
    placeholders = ", ".join("?" for _ in colnames)
    verb = "INSERT OR REPLACE" if replace else "INSERT OR IGNORE"
    sql = f'{verb} INTO "{table}" ({", ".join(colnames)}) VALUES ({placeholders})'
    inserted = 0
    skipped = 0
    for rec in records:
        d = asdict(rec)
        row = [d.get(c) for c in colnames]
        before = conn.total_changes
        conn.execute(sql, row)
        if conn.total_changes > before:
            inserted += 1
        else:
            skipped += 1
    conn.commit()
    return {"inserted": inserted, "skipped": skipped}


def fetch(conn: sqlite3.Connection, table: str, where: str = "", params: tuple = ()) -> List[dict]:
    sql = f'SELECT * FROM "{table}"'
    if where:
        sql += f" WHERE {where}"
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def fetch_month(conn: sqlite3.Connection, table: str, date_col: str, month: str) -> List[dict]:
    """date_col の先頭7文字(YYYY-MM)が month に一致する行を返す。"""
    return fetch(conn, table, f'substr("{date_col}",1,7)=?', (month,))


def load_objects(conn: sqlite3.Connection, table: str, month: str = None,
                 date_col: str = None) -> list:
    """DB行を dataclass インスタンスに復元して返す。"""
    cls = TABLES[table][0]
    names = {f.name for f in fields(cls)}
    if month and date_col:
        rows = fetch_month(conn, table, date_col, month)
    else:
        rows = fetch(conn, table)
    out = []
    for r in rows:
        out.append(cls(**{k: v for k, v in r.items() if k in names}))
    return out


def replace_journal_for_month(conn: sqlite3.Connection, month: str, entries: Iterable) -> dict:
    """対象月の journal を作り直す（再実行の冪等性確保）。"""
    conn.execute('DELETE FROM journal_entries WHERE month=?', (month,))
    conn.commit()
    return upsert(conn, "journal_entries", entries)
