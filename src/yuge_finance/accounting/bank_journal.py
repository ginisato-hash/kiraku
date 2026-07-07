"""銀行明細 → 仕訳候補（喜らく単体）。

journal_rules.yml のルールを上から評価。
  入金(deposit)   : 借方 現預金 / 貸方 相手科目
  出金(withdrawal): 借方 相手科目 / 貸方 現預金
マッチしない明細は仮勘定 + confidence=low（例外レポート行き）。
"""
from __future__ import annotations

from typing import List, Optional

from .. import config
from ..normalize.schema import BankTransaction, JournalEntry
from . import tax_rules


def _match(tx: BankTransaction, rule: dict) -> bool:
    m = rule.get("match", {})
    direction = m.get("direction", "any")
    is_deposit = tx.amount_signed > 0
    if direction == "deposit" and not is_deposit:
        return False
    if direction == "withdrawal" and is_deposit:
        return False
    amt = abs(tx.amount_signed)
    if "amount_min" in m and amt < m["amount_min"]:
        return False
    if "amount_max" in m and amt > m["amount_max"]:
        return False
    dc = m.get("description_contains")
    if dc:
        hay = f"{tx.description} {tx.raw_memo}"
        if not any(s in hay for s in dc):
            return False
    cc = m.get("counterparty_contains")
    if cc:
        if not any(s in (tx.counterparty or "") for s in cc):
            return False
    # description_contains も counterparty_contains も無いルールはキーワード無しなので不採用
    if not dc and not cc:
        return False
    return True


def classify(tx: BankTransaction, rules_cfg: dict) -> dict:
    for rule in rules_cfg.get("rules", []):
        if _match(tx, rule):
            act = rule["action"]
            return {"category": act["category"], "confidence": act.get("confidence", "medium"),
                    "rule_id": rule["id"]}
    return {"category": None,
            "confidence": rules_cfg.get("default", {}).get("confidence", "low"),
            "rule_id": "unmatched"}


def build(transactions: List[BankTransaction], month: str) -> List[JournalEntry]:
    rules_cfg = config.load_yaml("journal_rules.yml")
    prop = config.property_name()
    entries: List[JournalEntry] = []

    for tx in transactions:
        if tx.transaction_date[:7] != month:
            continue
        amt = abs(tx.amount_signed)
        if amt == 0:
            continue
        cls = classify(tx, rules_cfg)
        if cls["category"]:
            account, sub = tax_rules.resolve(cls["category"])
        else:
            account, sub = tax_rules.resolve("__suspense__")  # 仮勘定へ
        is_deposit = tx.amount_signed > 0
        if is_deposit:
            d_acc, d_sub, c_acc, c_sub = "現預金", tx.account_name or "現預金", account, sub
        else:
            d_acc, d_sub, c_acc, c_sub = account, sub, "現預金", tx.account_name or "現預金"

        entries.append(JournalEntry(
            journal_date=tx.transaction_date, property=prop,
            description=tx.description or "銀行取引",
            debit_account=d_acc, debit_subaccount=d_sub, debit_amount=amt,
            credit_account=c_acc, credit_subaccount=c_sub, credit_amount=amt,
            tax_category="", counterparty=tx.counterparty,
            source="bank", source_id=tx.transaction_id,
            confidence=cls["confidence"], rule_id=cls["rule_id"],
            memo=tx.raw_memo,
        ).finalize())
    return entries
