"""人件費 発生計上（喜らく固有前提）。

フロント: 時給1,500 × 12h × 営業日数 × ワンオペ(1名)
清掃    : 時給1,200 × 6h × 人数(2.5) × 稼働日数
※ 実際の給与は銀行/現金から計上されるため accrual_enabled=false が既定。
   有効時は 借方 人件費(補助) / 貸方 未払金 で概算発生計上する。
"""
from __future__ import annotations

import calendar
from typing import Dict, List

from .. import config
from ..normalize.schema import JournalEntry


def days_in_month(month: str) -> int:
    y, m = (int(x) for x in month.split("-"))
    return calendar.monthrange(y, m)[1]


def compute(month: str) -> Dict[str, float]:
    p = config.kiraku().get("payroll", {})
    days = days_in_month(month)
    f = p.get("front", {})
    c = p.get("cleaning", {})
    front = f.get("hourly_wage", 1500) * f.get("hours_per_day", 12) * days * 1
    cleaning = c.get("hourly_wage", 1200) * c.get("hours_per_day", 6) \
        * c.get("headcount", 2.5) * days
    return {"front": float(front), "cleaning": float(cleaning)}


def build(month: str) -> List[JournalEntry]:
    p = config.kiraku().get("payroll", {})
    if not p.get("accrual_enabled", False):
        return []
    amounts = compute(month)
    prop = config.property_name()
    last_day = f"{month}-{days_in_month(month):02d}"
    entries = []
    for key, sub in (("front", "フロント人件費"), ("cleaning", "清掃人件費")):
        amt = round(amounts[key])
        if amt <= 0:
            continue
        entries.append(JournalEntry(
            journal_date=last_day, property=prop,
            description=f"{sub} 発生計上",
            debit_account="人件費", debit_subaccount=sub, debit_amount=amt,
            credit_account="買掛金・未払金", credit_subaccount="未払給与", credit_amount=amt,
            tax_category="対象外", counterparty="",
            source="estimate", source_id=f"payroll-{key}-{month}",
            confidence="high", rule_id="payroll_accrual",
            memo="人件費概算（config.payroll）",
        ).finalize())
    return entries
