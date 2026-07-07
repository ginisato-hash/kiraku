"""月次債務返済 仕訳生成・BI集計（喜らく単体・Phase B）。

返済予定表(LoanScheduleEntry)と銀行明細(BankTransaction)が一致した場合のみ、
以下の高信頼仕訳を生成する：
  借方 liability_account(principal_payment) / 借方 支払利息(interest_payment)
  貸方 現預金(total_payment)
元本返済はPL費用にしない（liability_account はBS科目のみ）。
予定表が無い明細（例: 政策金融公庫）は勝手に元本/利息を推定せず、確定仕訳を作らない。
"""
from __future__ import annotations

import datetime as _dt
from typing import Dict, List

from .. import config
from ..normalize.schema import BankTransaction, JournalEntry, LoanScheduleEntry
from . import tax_rules

TOLERANCE = 1
ALLOWED_LIABILITY_ACCOUNTS = {
    "短期借入金", "長期借入金", "関係会社借入金", "役員借入金", "長期未払金",
}


def _debt_cfg() -> Dict:
    return config.kiraku().get("debt_management", {})


def _parse_date(s: str):
    try:
        return _dt.date.fromisoformat(s[:10])
    except (ValueError, TypeError):
        return None


def _find_bank_match(entry: LoanScheduleEntry, bank_txns: List[BankTransaction],
                     amount_tol: float, date_tol_days: int):
    """摘要部分一致 + 金額一致 + 日付近接 の銀行明細を返す（無ければNone, warning情報）。"""
    edate = _parse_date(entry.payment_date)
    best = None
    best_days = None
    for t in bank_txns:
        if not entry.bank_description_match:
            continue
        if entry.bank_description_match not in (t.description or ""):
            continue
        if abs(t.withdrawal_amount - entry.total_payment) > amount_tol:
            continue
        tdate = _parse_date(t.transaction_date)
        days = abs((tdate - edate).days) if (tdate and edate) else 9999
        if best is None or days < best_days:
            best, best_days = t, days
    if best is None:
        return None, None
    warn = best_days is not None and best_days > date_tol_days
    return best, (best_days if warn else None)


def build(month: str, schedule: List[LoanScheduleEntry],
          bank_txns: List[BankTransaction]) -> Dict:
    cfg = _debt_cfg()
    amount_tol = float(cfg.get("amount_tolerance", 1))
    date_tol = int(cfg.get("date_tolerance_days", 3))
    prop = config.property_name()

    confirmed: List[JournalEntry] = []
    exceptions: List[JournalEntry] = []
    date_warnings: List[Dict] = []
    unmatched_amount = 0.0

    for e in schedule:
        if e.liability_account not in ALLOWED_LIABILITY_ACCOUNTS:
            exceptions.append(JournalEntry(
                journal_date=e.payment_date, property=prop,
                description=f"{e.lender} 返済予定表 liability_account不正値",
                debit_account=tax_rules.resolve(e.liability_account)[0],
                debit_amount=e.principal_payment,
                credit_account="現預金", credit_amount=e.total_payment,
                source="loan_schedule", source_id=e.loan_id,
                confidence="low", rule_id="debt_invalid_liability_account",
                memo=f"liability_account={e.liability_account!r} は許可値外",
            ).finalize())
            unmatched_amount += e.total_payment
            continue

        internal_ok = abs((e.principal_payment + e.interest_payment) - e.total_payment) <= TOLERANCE
        liab_acc, liab_sub = tax_rules.resolve(e.liability_account)
        match, late_days = _find_bank_match(e, bank_txns, amount_tol, date_tol)

        if not internal_ok:
            exceptions.append(JournalEntry(
                journal_date=e.payment_date, property=prop,
                description=f"{e.lender} 返済予定表 内訳不一致",
                debit_account=liab_acc, debit_subaccount=liab_sub,
                debit_amount=e.principal_payment,
                credit_account="現預金", credit_subaccount="普通預金",
                credit_amount=e.total_payment,
                source="loan_schedule", source_id=e.loan_id,
                confidence="low", rule_id="debt_internal_mismatch",
                memo=f"principal+interest({e.principal_payment + e.interest_payment})"
                     f"!=total({e.total_payment})",
            ).finalize())
            unmatched_amount += e.total_payment
            continue

        if match is None:
            exceptions.append(JournalEntry(
                journal_date=e.payment_date, property=prop,
                description=f"{e.lender} 返済予定表 銀行明細未一致",
                debit_account=liab_acc, debit_subaccount=liab_sub,
                debit_amount=e.principal_payment,
                credit_account="現預金", credit_subaccount="普通預金",
                credit_amount=e.total_payment,
                source="loan_schedule", source_id=e.loan_id,
                confidence="low", rule_id="debt_no_bank_match",
                memo="銀行明細と一致しないため未確定",
            ).finalize())
            unmatched_amount += e.total_payment
            continue

        if late_days is not None:
            date_warnings.append({"loan_id": e.loan_id, "late_days": late_days})

        # 元本返済（BS: liability_account / 現預金）。元本はPL費用にしない。
        confirmed.append(JournalEntry(
            journal_date=match.transaction_date, property=prop,
            description=f"{e.lender} 返済（元本）",
            debit_account=liab_acc, debit_subaccount=liab_sub,
            debit_amount=e.principal_payment,
            credit_account="現預金", credit_subaccount=match.account_name or "普通預金",
            credit_amount=e.principal_payment,
            source="loan_schedule", source_id=e.loan_id,
            confidence="high", rule_id="debt_repayment_matched",
            memo=f"支払日ずれ{late_days}日" if late_days else "",
        ).finalize())
        # 支払利息（PL計上対象）。
        if e.interest_payment > 0:
            confirmed.append(JournalEntry(
                journal_date=match.transaction_date, property=prop,
                description=f"{e.lender} 返済（支払利息）",
                debit_account="支払利息", debit_subaccount=e.lender,
                debit_amount=e.interest_payment,
                credit_account="現預金", credit_subaccount=match.account_name or "普通預金",
                credit_amount=e.interest_payment,
                source="loan_schedule", source_id=e.loan_id + "-int",
                confidence="high", rule_id="debt_interest_matched",
                memo="",
            ).finalize())

    missing_types = [acc for acc in sorted(
        {"短期借入金", "長期借入金", "関係会社借入金", "役員借入金", "長期未払金"})
        if acc not in {e.liability_account for e in schedule}]

    if not schedule:
        debt_service_status = "予定表未投入"
    elif exceptions:
        debt_service_status = "要確認"
    else:
        debt_service_status = "予定表投入済"

    return {
        "confirmed": confirmed, "exceptions": exceptions,
        "date_warnings": date_warnings,
        "debt_schedule_missing_count": len(missing_types),
        "debt_schedule_missing_types": missing_types,
        "debt_schedule_exception_amount": round(unmatched_amount, 2),
        "debt_service_status": debt_service_status,
        "monthly_debt_principal_payment": round(sum(e.principal_payment for e in schedule
                                                     if abs((e.principal_payment + e.interest_payment) - e.total_payment) <= TOLERANCE), 2),
        "monthly_debt_interest_payment": round(sum(e.interest_payment for e in schedule
                                                    if abs((e.principal_payment + e.interest_payment) - e.total_payment) <= TOLERANCE), 2),
        "monthly_debt_total_payment": round(sum(e.total_payment for e in schedule
                                                 if abs((e.principal_payment + e.interest_payment) - e.total_payment) <= TOLERANCE), 2),
    }


def debt_balance_from_records(records, include_long_term_payable: bool = None) -> float:
    """OpeningBalance生レコードから有利子負債合計を返す（借入金TB全額 + 任意で長期未払金）。"""
    if include_long_term_payable is None:
        include_long_term_payable = bool(
            _debt_cfg().get("include_long_term_payable_in_debt_total", False))
    total = sum(r.credit_total - r.debit_total for r in records if r.account == "借入金")
    if include_long_term_payable:
        total += sum(r.credit_total - r.debit_total for r in records
                     if r.account == "その他負債" and r.subaccount == "長期未払金")
    return round(total, 2)
