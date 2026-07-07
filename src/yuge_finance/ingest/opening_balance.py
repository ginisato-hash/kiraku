"""開始残高・会計士YTD取込（喜らく単体）。

imports/opening_balance/ から以下を取り込む:
  - opening_balance_<date>.csv          : 科目別 開始残高(試算表スナップショット)
  - accountant_pl_ytd_<month>.csv       : 会計士確定 YTD PL（参照データ。二重計上しない）
  - accountant_cf_ytd_<month>.csv       : 会計士確定 YTD CF（参照データ）
  - accountant_kpi_summary_<date>.csv   : 会計士経営分析表 参照値（限界利益率・固定費等）
開始残高は借方合計=貸方合計、および config.opening_balance_lock の確定値との一致をcritical検証する。
2026-06以降のPL/BS/CFは、この開始残高からロールフォワードし、YTD参照値は二重計上しない。
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Dict, List

from .. import config, csvio, db
from ..normalize.dedupe import by_import_hash
from ..normalize.schema import OpeningBalance

TOLERANCE = 0.5
SRC_DIR_NAME = "opening_balance"
STAGING = "data/staging/opening"

_ASSET = {"現預金", "売掛金", "その他流動資産", "有形固定資産"}
_LIAB = {"買掛金・未払金", "借入金", "その他負債"}
_EQUITY = {"資本金・資本剰余金", "利益剰余金"}


def _opening_date() -> str:
    return str(config.kiraku().get("period", {}).get("opening_date", "2026-05-31"))


def _lock() -> Dict:
    return config.kiraku().get("opening_balance_lock", {})


def load_opening(as_of_date: str = None) -> List[OpeningBalance]:
    """imports/opening_balance/opening_balance_*.csv を読み込む。

    as_of_date を指定した場合、その日付のCSV(opening_balance_<date>.csv)のみを対象にする。
    """
    src = config.IMPORTS_DIR / SRC_DIR_NAME
    default_as_of = as_of_date or _opening_date()
    pattern = f"opening_balance_{as_of_date}.csv" if as_of_date else "opening_balance_*.csv"
    out: List[OpeningBalance] = []
    for csv_path in sorted(src.glob(pattern)):
        for row in csvio.read_dicts(csv_path):
            if not any(row.values()):
                continue
            acc = (row.get("account") or "").strip()
            if not acc:
                continue
            ob = OpeningBalance(
                as_of_date=(row.get("as_of_date") or default_as_of).strip(),
                account=acc,
                subaccount=(row.get("subaccount") or "").strip(),
                debit_total=row.get("debit") or row.get("debit_total") or 0,
                credit_total=row.get("credit") or row.get("credit_total") or 0,
                source_file=csv_path.name,
                memo=(row.get("memo") or "").strip(),
            ).finalize()
            out.append(ob)
    return by_import_hash(out)


def validate(records: List[OpeningBalance]) -> Dict:
    """借方合計=貸方合計（簿記としての基本整合性）。"""
    d = round(sum(r.debit_total for r in records), 2)
    c = round(sum(r.credit_total for r in records), 2)
    return {"debit": d, "credit": c, "balanced": abs(d - c) <= TOLERANCE}


def account_totals(records: List[OpeningBalance]) -> Dict[str, float]:
    """22 TB科目の資産/負債/純資産区分で集計した合計を返す。"""
    by_acc: Dict[str, List[float]] = {}
    for r in records:
        agg = by_acc.setdefault(r.account, [0.0, 0.0])
        agg[0] += r.debit_total
        agg[1] += r.credit_total
    asset = sum(d - c for a, (d, c) in by_acc.items() if a in _ASSET)
    liab = sum(c - d for a, (d, c) in by_acc.items() if a in _LIAB)
    equity = sum(c - d for a, (d, c) in by_acc.items() if a in _EQUITY)
    return {"asset_total": round(asset, 2), "liability_total": round(liab, 2),
            "equity_total": round(equity, 2)}


def critical_checks(records: List[OpeningBalance], as_of_date: str = None) -> List[Dict]:
    """会計士確定BSロック値との一致をcritical検証する。"""
    lock = _lock()
    tot = account_totals(records)
    dates = {r.as_of_date for r in records if r.as_of_date}
    actual_date = as_of_date or (sorted(dates)[0] if len(dates) == 1 else "|".join(sorted(dates)))

    def chk(name, actual, expected):
        ok = abs(actual - expected) <= TOLERANCE if isinstance(expected, (int, float)) else str(actual) == str(expected)
        return {"check": name, "value": actual, "expected": expected,
                "status": "OK" if ok else "critical"}

    checks = [
        chk("opening_balance_date", actual_date, lock.get("date")),
        chk("opening_balance_asset_total", tot["asset_total"], lock.get("asset_total")),
        chk("opening_balance_liability_total", tot["liability_total"], lock.get("liability_total")),
        chk("opening_balance_equity_total", tot["equity_total"], lock.get("equity_total")),
        chk("asset_total_eq_liability_plus_equity", round(tot["asset_total"], 2),
            round(tot["liability_total"] + tot["equity_total"], 2)),
    ]
    return checks


def opening_dict(conn) -> Dict[str, Dict[str, float]]:
    """DBの開始残高を {科目: {'debit':x,'credit':y}} で返す。"""
    rows = db.fetch(conn, "opening_balances")
    out: Dict[str, Dict[str, float]] = {}
    for r in rows:
        acc = r["account"]
        agg = out.setdefault(acc, {"debit": 0.0, "credit": 0.0})
        agg["debit"] += r["debit_total"] or 0
        agg["credit"] += r["credit_total"] or 0
    return out


def _load_item_amount_csv(prefix: str) -> Dict[str, float]:
    src = config.IMPORTS_DIR / SRC_DIR_NAME
    data: Dict[str, float] = {}
    for csv_path in sorted(src.glob(f"{prefix}_*.csv")):
        for row in csvio.read_dicts(csv_path):
            item = (row.get("item") or "").strip()
            if not item:
                continue
            raw = str(row.get("amount", "")).replace(",", "").strip()
            try:
                data[item] = float(raw)
            except ValueError:
                data[item] = raw  # 日付等の非数値項目はそのまま保持
    return data


def accountant_pl_ytd() -> Dict[str, float]:
    """会計士確定YTD PL（参照データ）。2026-06以降のPLへは二重計上しない。"""
    return _load_item_amount_csv("accountant_pl_ytd")


def accountant_cf_ytd() -> Dict[str, float]:
    return _load_item_amount_csv("accountant_cf_ytd")


def accountant_kpi_summary() -> Dict[str, float]:
    """会計士経営分析表 参照値（限界利益率・固定費合計・損益分岐点売上高等）。"""
    return _load_item_amount_csv("accountant_kpi_summary")


def run(conn=None, as_of_date: str = None) -> Dict:
    own = conn is None
    conn = conn or db.connect()
    src = config.IMPORTS_DIR / SRC_DIR_NAME
    raw_dir = config.DATA_DIR / "raw" / "opening_balance"
    raw_dir.mkdir(parents=True, exist_ok=True)
    for csv_path in sorted(src.glob("*.csv")):
        shutil.copy2(csv_path, raw_dir / csv_path.name)

    records = load_opening(as_of_date)
    check = validate(records)
    crit = critical_checks(records, as_of_date)
    crit_fail = [c for c in crit if c["status"] == "critical"]

    staging_dir = config.ROOT / STAGING
    staging_dir.mkdir(parents=True, exist_ok=True)
    csvio.write_dataclasses(staging_dir / "opening_balance.csv", records, OpeningBalance)
    stats = db.upsert(conn, "opening_balances", records)

    pl_ytd = accountant_pl_ytd()
    cf_ytd = accountant_cf_ytd()
    kpi_summary = accountant_kpi_summary()
    (staging_dir / "accountant_pl_ytd.json").write_text(
        json.dumps(pl_ytd, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    (staging_dir / "accountant_cf_ytd.json").write_text(
        json.dumps(cf_ytd, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    (staging_dir / "accountant_kpi_summary.json").write_text(
        json.dumps(kpi_summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    if own:
        conn.close()
    return {"opening_rows": len(records), "balanced": check["balanced"],
            "opening_debit": check["debit"], "opening_credit": check["credit"],
            "pl_ytd_items": len(pl_ytd), "cf_ytd_items": len(cf_ytd),
            "kpi_items": len(kpi_summary),
            "critical_checks": crit, "critical_failures": len(crit_fail),
            **stats}
