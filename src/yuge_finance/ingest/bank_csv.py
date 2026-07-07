"""銀行CSV取込 → BankTransaction 正規化（喜らく単体）。

imports/bank/ 配下のCSVを読み、標準スキーマへ正規化する。
日本の銀行CSVの代表的な列名エイリアスに対応。
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import List, Optional

from .. import config, csvio, db
from ..normalize.dedupe import by_import_hash
from ..normalize.schema import BankTransaction

# 標準列名 -> 取りうる別名
ALIASES = {
    "transaction_date": ["transaction_date", "取引日", "日付", "お取引日", "年月日", "勘定日"],
    "posted_date": ["posted_date", "起算日", "計上日", "（起算日）"],
    "bank_name": ["bank_name", "銀行名", "金融機関", "金融機関名"],
    "account_name": ["account_name", "口座", "口座名", "口座名義", "科目", "照会口座"],
    "description": ["description", "摘要", "お取引内容", "内容", "取引内容", "備考摘要"],
    "deposit_amount": ["deposit_amount", "入金", "お預入れ金額", "預入金額", "入金額",
                       "お預り金額", "入金金額（円）", "入金金額"],
    "withdrawal_amount": ["withdrawal_amount", "出金", "お引出し金額", "引出金額", "出金額",
                          "お支払金額", "出金金額（円）", "出金金額"],
    "balance": ["balance", "残高", "差引残高", "残高（円）"],
    "counterparty": ["counterparty", "取引先", "相手先", "振込依頼人"],
    "raw_memo": ["raw_memo", "メモ", "備考"],
}


def _pick(row: dict, std: str) -> str:
    for alias in ALIASES.get(std, [std]):
        if alias in row and row[alias] not in (None, ""):
            return row[alias]
    return ""


def _from_row(row: dict, source_file: str, default_bank: str) -> BankTransaction:
    tx = BankTransaction(
        source_file=source_file,
        bank_name=_pick(row, "bank_name") or default_bank,
        account_name=_pick(row, "account_name") or default_bank,
        transaction_date=_normdate(_pick(row, "transaction_date")),
        posted_date=_normdate(_pick(row, "posted_date")),
        description=_pick(row, "description"),
        counterparty=_pick(row, "counterparty"),
        deposit_amount=_pick(row, "deposit_amount"),
        withdrawal_amount=_pick(row, "withdrawal_amount"),
        balance=_pick(row, "balance"),
        raw_memo=_pick(row, "raw_memo"),
    )
    return tx.finalize()


def _normdate(s: str) -> str:
    """YYYY/MM/DD・YYYY.MM.DD・YYYY年MM月DD日 を YYYY-MM-DD へ。"""
    if not s:
        return ""
    s = (str(s).strip()
         .replace("年", "-").replace("月", "-").replace("日", "")
         .replace("/", "-").replace(".", "-"))
    parts = [p for p in s.split("-") if p]
    if len(parts) == 3:
        y, m, d = parts
        return f"{int(y):04d}-{int(m):02d}-{int(d):02d}"
    return s


def load(month: Optional[str] = None) -> List[BankTransaction]:
    """imports/bank/ 配下を読み込み正規化。month指定時はその月に絞る。"""
    src_dir = config.IMPORTS_DIR / "bank"
    out: List[BankTransaction] = []
    for csv_path in sorted(src_dir.glob("*.csv")):
        default_bank = csv_path.stem
        for row in csvio.read_dicts(csv_path):
            if not any(row.values()):
                continue
            tx = _from_row(row, csv_path.name, default_bank)
            if not tx.transaction_date:
                continue
            out.append(tx)
    out = by_import_hash(out)
    if month:
        out = [t for t in out if t.transaction_date[:7] == month]
    return out


def run(month: str, conn=None) -> dict:
    """CLI: 原本コピー → 正規化 → staging出力 → DB保存。"""
    own = conn is None
    conn = conn or db.connect()
    raw_dir = config.DATA_DIR / "raw" / "bank" / month
    raw_dir.mkdir(parents=True, exist_ok=True)
    src_dir = config.IMPORTS_DIR / "bank"
    for csv_path in sorted(src_dir.glob("*.csv")):
        shutil.copy2(csv_path, raw_dir / csv_path.name)
    records = load(month)
    staging = config.DATA_DIR / "staging" / "bank_transactions" / f"{month}.csv"
    csvio.write_dataclasses(staging, records, BankTransaction)
    stats = db.upsert(conn, "bank_transactions", records)
    if own:
        conn.close()
    return {"count": len(records), "staging": str(staging), **stats}
