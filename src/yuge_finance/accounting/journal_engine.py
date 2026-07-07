"""仕訳生成オーケストレータ（喜らく単体）。

売上(Beds24) + 銀行 + 現金 + 手動補正 + MC + 人件費 を統合し、
high confidence のみ自動確定、medium/low は例外として分離する。
"""
from __future__ import annotations

from typing import Dict, List

from .. import config
from ..normalize.schema import (BankTransaction, BookingRecord, CashTransaction,
                                JournalEntry, ManualAdjustment)
from . import (bank_journal, cash_journal, mc_cost, payroll_cost,
               revenue_journal, tax_rules)


def _manual_to_entries(adjustments: List[ManualAdjustment], month: str) -> List[JournalEntry]:
    prop = config.property_name()
    out = []
    for a in adjustments:
        if a.journal_date[:7] != month:
            continue
        d_acc, d_sub = tax_rules.resolve(a.debit_account)
        c_acc, c_sub = tax_rules.resolve(a.credit_account)
        out.append(JournalEntry(
            journal_date=a.journal_date, property=prop, description=a.description,
            debit_account=d_acc, debit_subaccount=a.debit_subaccount or d_sub,
            debit_amount=a.debit_amount,
            credit_account=c_acc, credit_subaccount=a.credit_subaccount or c_sub,
            credit_amount=a.credit_amount,
            tax_category=a.tax_category, counterparty=a.counterparty,
            source="manual", source_id=a.adjustment_id,
            confidence="high", rule_id="manual",
            memo=a.memo,
        ).finalize())
    return out


def build(month: str,
          bookings: List[BookingRecord],
          bank_txns: List[BankTransaction],
          cash_txns: List[CashTransaction],
          adjustments: List[ManualAdjustment]) -> Dict:
    # Beds24は速報・管理会計用。既定では確定PL仕訳を作らない（revenue.beds24_creates_journal）。
    rev_cfg = config.kiraku().get("revenue", {})
    revenue = (revenue_journal.build(bookings, month)
               if rev_cfg.get("beds24_creates_journal", False) else [])
    bank = bank_journal.build(bank_txns, month)
    cash = cash_journal.build(cash_txns, month)
    manual = _manual_to_entries(adjustments, month)

    base = revenue + bank + cash + manual
    high_base = [e for e in base if e.confidence == "high"]

    mc = mc_cost.build(month, high_base)
    payroll = payroll_cost.build(month)

    all_entries = base + mc + payroll
    confirmed = [e for e in all_entries if e.confidence == "high"]
    exceptions = [e for e in all_entries if e.confidence != "high"]

    return {
        "all": all_entries,
        "confirmed": confirmed,
        "exceptions": exceptions,
        "by_source": {
            "beds24": len(revenue), "bank": len(bank), "cash": len(cash),
            "manual": len(manual), "mc": len(mc), "payroll": len(payroll),
        },
    }
