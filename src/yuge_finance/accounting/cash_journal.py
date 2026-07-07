"""現金取引 → 仕訳候補（喜らく単体）。

cash_rules.yml の transaction_type テンプレートで借方/貸方を決める。
review_status==approved のみ自動確定(confidence維持)。
needs_review / reviewed は confidence=low に落として例外レポートへ。
"""
from __future__ import annotations

from typing import List

from .. import config
from ..normalize.schema import CashTransaction, JournalEntry
from . import tax_rules


def _resolve_leg(spec: str, tx_category: str, default_category: str):
    """leg指定('category' or 具体カテゴリ) を (科目, 補助) に解決。"""
    if spec == "category":
        cat = tx_category or default_category
    else:
        cat = spec
    return tax_rules.resolve(cat)


def build(transactions: List[CashTransaction], month: str) -> List[JournalEntry]:
    rules = config.load_yaml("cash_rules.yml")
    types = rules.get("types", {})
    prop = config.property_name()
    entries: List[JournalEntry] = []

    for tx in transactions:
        if tx.transaction_date[:7] != month:
            continue
        if tx.amount == 0:
            continue
        rule = types.get(tx.transaction_type)
        if not rule:
            # 不明なtransaction_type → 仮勘定/現金、低信頼
            d_acc, d_sub = tax_rules.resolve("__suspense__")
            c_acc, c_sub = tax_rules.resolve("現金")
            conf, rid = "low", "cash_unknown_type"
        else:
            d_acc, d_sub = _resolve_leg(rule["debit"], tx.category, rule.get("default_category", ""))
            c_acc, c_sub = _resolve_leg(rule["credit"], tx.category, rule.get("default_category", ""))
            conf = rule.get("confidence", "medium")
            rid = f"cash_{tx.transaction_type}"

        # approved 以外は自動確定させない
        if tx.review_status != "approved":
            conf = "low"
            rid = rid + "_unapproved"

        entries.append(JournalEntry(
            journal_date=tx.transaction_date, property=prop,
            description=tx.description or tx.transaction_type,
            debit_account=d_acc, debit_subaccount=d_sub, debit_amount=tx.amount,
            credit_account=c_acc, credit_subaccount=c_sub, credit_amount=tx.amount,
            tax_category=tax_rules.tax_category_label() if tx.tax_amount else "",
            counterparty=tx.counterparty or tx.vendor,
            source="cash", source_id=tx.cash_transaction_id,
            confidence=conf, rule_id=rid,
            memo=f"{tx.vendor} {tx.memo} review={tx.review_status}".strip(),
        ).finalize())
    return entries
