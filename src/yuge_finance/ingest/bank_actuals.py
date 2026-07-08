"""銀行口座実績CSV取込 → BankActualTransaction 正規化（喜らく単体・BI/分析専用）。

既存の会計確定パイプライン(ingest.bank_csv / bank_transactions / 仕訳エンジン)とは
完全に独立。ここで作るデータは口座残高の実績再現・費目候補分類・
固定費/変動費更新候補のBI表示専用であり、仕訳を作らない。
"""
from __future__ import annotations

import csv as _csv
import hashlib
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from .. import config, db
from ..accounting import bank_classifier
from ..normalize.schema import BankActualTransaction

TOLERANCE = 0.5


def _decode(path: Path, encoding: Optional[str]) -> str:
    raw = path.read_bytes()
    if encoding and encoding != "auto":
        return raw.decode(encoding)
    for enc in ("cp932", "shift_jis", "utf-8-sig", "utf-8"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _normdate(s: str) -> str:
    if not s:
        return ""
    s = (str(s).strip().replace("年", "-").replace("月", "-").replace("日", "")
         .replace("/", "-").replace(".", "-"))
    parts = [p for p in s.split("-") if p]
    if len(parts) == 3:
        y, m, d = parts
        return f"{int(y):04d}-{int(m):02d}-{int(d):02d}"
    return s


def _num(s: str) -> float:
    if s is None or s == "":
        return 0.0
    s = str(s).replace(",", "").strip()
    try:
        return float(s)
    except ValueError:
        return 0.0


def _mask_account_number(acc_num: str) -> str:
    if not acc_num:
        return ""
    return ("*" * max(len(acc_num) - 4, 0)) + acc_num[-4:]


def parse_csv(file_path: Path, account_key: str, encoding: str = "auto") -> List[BankActualTransaction]:
    """CSV1ファイルを BankActualTransaction のリストへ正規化する（会計仕訳は作らない）。"""
    text = _decode(file_path, encoding)
    reader = _csv.DictReader(text.splitlines())
    source_hash = hashlib.sha256(file_path.read_bytes()).hexdigest()[:32]
    now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    out: List[BankActualTransaction] = []
    row_no = 0
    for row in reader:
        if not any((v or "").strip() for v in row.values() if v is not None):
            continue
        row_no += 1
        raw_account = (row.get("照会口座") or "").strip()
        parts = raw_account.split()
        branch = parts[0] if parts else ""
        acc_type = parts[1] if len(parts) >= 3 else ""
        acc_num = parts[2] if len(parts) >= 3 else ""
        memo = (row.get("摘要") or "").strip()
        tx = BankActualTransaction(
            bank_account_key=account_key or raw_account,
            source_file_name=file_path.name,
            source_file_hash=source_hash,
            row_number=row_no,
            bank_branch=branch,
            account_type=acc_type,
            account_number_masked=_mask_account_number(acc_num),
            transaction_date=_normdate(row.get("勘定日", "")),
            value_date=_normdate(row.get("（起算日）", "")),
            withdrawal_amount=_num(row.get("出金金額（円）", "")),
            deposit_amount=_num(row.get("入金金額（円）", "")),
            balance_after=_num(row.get("残高（円）", "")),
            transaction_type=(row.get("取引区分") or "").strip(),
            detail_type=(row.get("明細区分") or "").strip(),
            counterparty_raw=memo,
            counterparty_normalized=bank_classifier.normalize_counterparty(memo),
            memo_raw=memo,
            created_at_jst=now,
        ).finalize()
        out.append(tx)
    return out


def verify_balance_chain(txns: List[BankActualTransaction]) -> Dict:
    """previous_balance + deposit - withdrawal = balance_after を行順に検証する。

    先頭行は取引前残高(opening_balance_before_first_transaction)を逆算する。
    """
    ordered = sorted(txns, key=lambda t: t.row_number)
    mismatches = []
    opening_balance_before_first = None
    prev_balance = None
    for i, tx in enumerate(ordered):
        if i == 0:
            opening_balance_before_first = round(
                tx.balance_after - tx.deposit_amount + tx.withdrawal_amount, 2)
            prev_balance = tx.balance_after
            continue
        calc = round(prev_balance + tx.deposit_amount - tx.withdrawal_amount, 2)
        if abs(calc - tx.balance_after) > TOLERANCE:
            mismatches.append({
                "row_number": tx.row_number, "transaction_date": tx.transaction_date,
                "expected_balance": calc, "actual_balance": tx.balance_after,
                "difference": round(tx.balance_after - calc, 2),
            })
        prev_balance = tx.balance_after
    return {
        "opening_balance_before_first_transaction": opening_balance_before_first,
        "mismatches": mismatches,
        "balanced": len(mismatches) == 0,
        "row_count": len(ordered),
    }


def month_end_observed_balance(txns: List[BankActualTransaction], month: str) -> Optional[Dict]:
    """month(YYYY-MM)末以前で最新の観測残高。月末ちょうどの明細が無くても直近日で返す。"""
    cutoff = f"{month}-31"
    candidates = [t for t in txns if t.transaction_date and t.transaction_date <= cutoff]
    if not candidates:
        return None
    latest = max(candidates, key=lambda t: (t.transaction_date, t.row_number))
    return {"balance": latest.balance_after, "date": latest.transaction_date}


def reconcile_with_accountant_bs(conn, txns: List[BankActualTransaction],
                                 opening_date: Optional[str] = None) -> Dict:
    """会計士確定BS(現預金/普通預金)と銀行実績残高を比較する。日付のズレは自動エラーにしない。"""
    opening_date = opening_date or str(config.kiraku().get("period", {}).get("opening_date", "2026-05-31"))
    month = opening_date[:7]
    observed = month_end_observed_balance(txns, month)

    rows = db.fetch(conn, "opening_balances",
                    'account=? AND subaccount=? AND as_of_date=?',
                    ("現預金", "普通預金", opening_date))
    if rows:
        accountant_cash = round((rows[0]["debit_total"] or 0) - (rows[0]["credit_total"] or 0), 2)
    else:
        rows_all = db.fetch(conn, "opening_balances", 'account=? AND as_of_date=?',
                            ("現預金", opening_date))
        accountant_cash = (round(sum((r["debit_total"] or 0) - (r["credit_total"] or 0) for r in rows_all), 2)
                          if rows_all else None)

    if observed is None or accountant_cash is None:
        return {
            "bank_balance_reconciliation_status": "データ不足",
            "accountant_bs_cash_balance": accountant_cash,
            "bank_csv_observed_balance": observed["balance"] if observed else None,
            "bank_csv_observed_date": observed["date"] if observed else None,
            "bank_vs_accountant_difference": None,
        }

    diff = round(observed["balance"] - accountant_cash, 2)
    date_matches = observed["date"] == opening_date
    if abs(diff) <= 1.0:
        status = "一致"
    elif not date_matches:
        status = "日付相違のため要確認（自動エラーではない）"
    else:
        status = "差異あり（要確認）"
    return {
        "bank_balance_reconciliation_status": status,
        "accountant_bs_cash_balance": accountant_cash,
        "bank_csv_observed_balance": observed["balance"],
        "bank_csv_observed_date": observed["date"],
        "bank_vs_accountant_difference": diff,
    }


def run(file_path: Path, account_key: str, encoding: str = "auto",
       month: Optional[str] = None, apply: bool = False, conn=None) -> Dict:
    """CLI: 原本をimports/bank/へ保存 → 正規化 → 残高検証 → 照合 → (apply時のみ)DB保存。"""
    own = conn is None
    conn = conn or db.connect()

    dest_dir = config.IMPORTS_DIR / "bank"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / file_path.name
    if file_path.resolve() != dest_path.resolve():
        shutil.copy2(file_path, dest_path)

    txns = parse_csv(dest_path, account_key, encoding)
    chain = verify_balance_chain(txns)
    recon = reconcile_with_accountant_bs(conn, txns)

    stats = {"inserted": 0, "skipped": 0}
    if apply:
        stats = db.upsert(conn, "bank_actual_transactions", txns)

    if own:
        conn.close()

    return {
        "file": str(dest_path), "account_key": account_key, "month": month,
        "rows_parsed": len(txns), "applied": apply,
        "balance_chain": chain, "reconciliation": recon,
        **stats,
    }


def load_all(conn) -> List[BankActualTransaction]:
    return db.load_objects(conn, "bank_actual_transactions")
