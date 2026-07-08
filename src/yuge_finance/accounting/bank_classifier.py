"""銀行口座実績 費目候補分類（喜らく単体）。

会計仕訳(journal_rules.yml)とは独立。confidence付きの「候補」を返すのみで、
固定費・変動費モデル(config/fixed_variable_model.yml)を自動更新することはない。

分類ルールは config/bank_classification_rules.yml に定義する。
"""
from __future__ import annotations

import unicodedata
from typing import Dict, Optional

from .. import config
from ..normalize.schema import BankActualTransaction

# cost_model_category -> 既存22科目マスタでの候補表示用(account_code, account_name)。
# あくまで「候補」の参考表示であり、これ自体で仕訳を確定しない。
COST_MODEL_TO_ACCOUNT: Dict[str, tuple] = {
    "revenue_cash_in": ("4100", "その他売上"),
    "loan_or_capital_cash_in": ("2200", "借入金"),
    "owner_related_cash_in": ("2200", "借入金"),
    "ota_receivable_collection": ("1100", "売掛金"),
    "coupon_point_receivable_collection": ("1100", "売掛金"),
    "bank_fee": ("5700", "固定費"),
    "debt_service_bank": ("2200", "借入金"),
    "debt_service_related_party": ("2200", "借入金"),
    "utilities": ("5300", "水道光熱費"),
    "fuel": ("5300", "水道光熱費"),
    "gas": ("5300", "水道光熱費"),
    "water": ("5300", "水道光熱費"),
    "telephone": ("5700", "固定費"),
    "lease": ("5700", "固定費"),
    "maintenance_contract": ("5500", "修繕費"),
    "elevator_maintenance": ("5500", "修繕費"),
    "tax_accountant_fee": ("5700", "固定費"),
    "insurance": ("5700", "固定費"),
    "social_insurance": ("5700", "固定費"),
    "labor_insurance": ("5700", "固定費"),
    "payroll_or_staff_payment": ("5600", "人件費"),
    "cleaning": ("5200", "リネン費"),
    "linen": ("5200", "リネン費"),
    "supplies": ("5400", "消耗品費"),
    "vehicle": ("5700", "固定費"),
    "membership_dues": ("5700", "固定費"),
    "tourism_association_fee": ("5700", "固定費"),
    "tax_payment": ("5700", "固定費"),
    "rent_or_hot_spring_fee": ("5700", "固定費"),
    "software_system_fee": ("5700", "固定費"),
    "unknown": ("1200", "その他流動資産"),
}


def normalize_counterparty(raw: str) -> str:
    """半角カナ等をNFKCで正規化し、読みやすい表記にする。"""
    if not raw:
        return ""
    return unicodedata.normalize("NFKC", raw).strip()


def load_rules() -> dict:
    return config.load_yaml("bank_classification_rules.yml")


def _match(tx: BankActualTransaction, rule: dict) -> bool:
    m = rule.get("match", {})
    direction = m.get("direction", "any")
    is_deposit = tx.signed_amount > 0
    if direction == "deposit" and not is_deposit:
        return False
    if direction == "withdrawal" and is_deposit:
        return False
    amt = abs(tx.signed_amount)
    if "amount_min" in m and amt < m["amount_min"]:
        return False
    if "amount_max" in m and amt > m["amount_max"]:
        return False
    cc = m.get("counterparty_contains")
    if not cc:
        return False
    hay = f"{tx.counterparty_raw} {tx.memo_raw}"
    return any(s in hay for s in cc)


def classify_bank_transaction(tx: BankActualTransaction, rules_cfg: Optional[dict] = None) -> dict:
    """摘要・金額・入出金方向から費目候補を返す。自動確定はしない(すべてcandidate)。"""
    rules_cfg = rules_cfg if rules_cfg is not None else load_rules()
    action = None
    rule_id = "unmatched"
    for rule in rules_cfg.get("rules", []):
        if _match(tx, rule):
            action = rule["action"]
            rule_id = rule["id"]
            break
    if action is None:
        action = rules_cfg.get("default", {})
        rule_id = "unmatched"

    category = action.get("cost_model_category", "unknown")
    code, name = COST_MODEL_TO_ACCOUNT.get(category, COST_MODEL_TO_ACCOUNT["unknown"])

    evidence_source = None
    if action.get("evidence_source_url"):
        evidence_source = {
            "evidence_source_url": action.get("evidence_source_url"),
            "evidence_summary": action.get("evidence_summary", ""),
            "researched_at_jst": action.get("researched_at_jst", ""),
        }

    return {
        "rule_id": rule_id,
        "account_code_candidate": code,
        "account_name_candidate": name,
        "cost_model_category": category,
        "fixed_or_variable": action.get("fixed_or_variable", "unknown"),
        "cashflow_category": action.get("cashflow_category", "unknown"),
        "business_area": action.get("business_area", "unknown"),
        "confidence": action.get("confidence", "low"),
        "reason": action.get("reason", ""),
        "requires_review": bool(action.get("requires_review", True)),
        "auto_reflectable": bool(action.get("auto_reflectable", False)),
        "evidence_source": evidence_source,
    }
