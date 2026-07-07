"""試算表（喜らく単体）。確定仕訳から科目別 借方/貸方/純額 を集計。"""
from __future__ import annotations

from typing import Dict, List

from .. import config
from ..normalize.schema import JournalEntry

TOLERANCE = 0.5


def build(entries: List[JournalEntry], opening: Dict = None) -> List[Dict]:
    """accounts.yml の科目順に 借方合計/貸方合計/純額 を返す。

    opening: {科目: {'debit':x,'credit':y}} を渡すと開始残高を加算（ロールフォワード）。
    """
    accs = config.accounts_cfg()["accounts"]
    debit = {a["name"]: 0.0 for a in accs}
    credit = {a["name"]: 0.0 for a in accs}
    if opening:
        for acc, agg in opening.items():
            if acc in debit:
                debit[acc] += agg.get("debit", 0.0)
                credit[acc] += agg.get("credit", 0.0)
    for e in entries:
        if e.debit_account in debit:
            debit[e.debit_account] += e.debit_amount
        if e.credit_account in credit:
            credit[e.credit_account] += e.credit_amount
    rows = []
    for a in accs:
        d = round(debit[a["name"]], 2)
        c = round(credit[a["name"]], 2)
        rows.append({
            "code": a["code"], "type": a["type"], "account": a["name"],
            "debit_total": d, "credit_total": c, "net": round(d - c, 2),
            "statement": a["statement"],
        })
    return rows


def totals(rows: List[Dict]) -> Dict:
    d = round(sum(r["debit_total"] for r in rows), 2)
    c = round(sum(r["credit_total"] for r in rows), 2)
    return {"debit": d, "credit": c, "balanced": abs(d - c) <= TOLERANCE}
