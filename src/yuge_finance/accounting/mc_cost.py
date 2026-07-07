"""MCコスト 発生計上（俺の人件費）。

固定: 150,000円/月
変動: GOP before MC × 15%
固定分と変動分は補助科目で分離して保持する。
仕訳: 借方 MCコスト(補助 MC固定/MC変動) / 貸方 買掛金・未払金。
"""
from __future__ import annotations

import calendar
from typing import Dict, List

from .. import config
from ..normalize.schema import JournalEntry

# GOP before MC の計算で控除対象とする費用科目（MC・減価償却・営業外・税金は除く）
_OPEX_ACCOUNTS = {
    "OTA手数料", "リネン費", "水道光熱費", "消耗品費", "修繕費", "人件費", "固定費",
}
_REVENUE_ACCOUNTS = {"宿泊売上", "その他売上"}


def gop_before_mc(entries: List[JournalEntry]) -> float:
    """既存仕訳から GOP before MC を概算（売上 - 営業費用）。"""
    revenue = 0.0
    opex = 0.0
    for e in entries:
        # 売上は貸方計上
        if e.credit_account in _REVENUE_ACCOUNTS:
            revenue += e.credit_amount
        if e.debit_account in _REVENUE_ACCOUNTS:
            revenue -= e.debit_amount  # 取消等
        # 費用は借方計上
        if e.debit_account in _OPEX_ACCOUNTS:
            opex += e.debit_amount
        if e.credit_account in _OPEX_ACCOUNTS:
            opex -= e.credit_amount
    return revenue - opex


def build(month: str, base_entries: List[JournalEntry]) -> List[JournalEntry]:
    cfg = config.kiraku().get("mc_cost", {})
    if not cfg.get("enabled", True):
        return []
    prop = config.property_name()
    y, m = (int(x) for x in month.split("-"))
    last_day = f"{month}-{calendar.monthrange(y, m)[1]:02d}"

    fixed = float(cfg.get("fixed_monthly", 150000))
    gop = gop_before_mc(base_entries)
    variable = round(max(0.0, gop) * float(cfg.get("variable_rate", 0.15)))

    entries = []
    for amt, sub, rid in (
        (round(fixed), cfg.get("fixed_subaccount", "MC固定"), "mc_fixed"),
        (variable, cfg.get("variable_subaccount", "MC変動"), "mc_variable"),
    ):
        if amt <= 0:
            continue
        entries.append(JournalEntry(
            journal_date=last_day, property=prop,
            description=f"MCコスト {sub}",
            debit_account="MCコスト", debit_subaccount=sub, debit_amount=amt,
            credit_account="買掛金・未払金", credit_subaccount="未払MC", credit_amount=amt,
            tax_category="対象外", counterparty="MC",
            source="estimate", source_id=f"{rid}-{month}",
            confidence="high", rule_id=rid,
            memo=f"GOP before MC={round(gop)}" if rid == "mc_variable" else "固定MC",
        ).finalize())
    return entries
