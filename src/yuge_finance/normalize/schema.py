"""標準スキーマ定義（喜らく単体）。

すべての取り込みデータはここで定義する dataclass に正規化される。
import_hash により再投入時の重複登録を防ぐ。
"""
from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field, fields
from typing import List, Optional


def make_hash(*parts: object) -> str:
    """与えられたキー要素から決定的な import_hash を生成する。"""
    joined = "|".join("" if p is None else str(p) for p in parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:32]


def _num(v: object) -> float:
    if v is None or v == "":
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).replace(",", "").replace("¥", "").replace("円", "").strip()
    if s in ("", "-"):
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


@dataclass
class BankTransaction:
    transaction_id: str = ""
    source_file: str = ""
    bank_name: str = ""
    account_name: str = ""
    transaction_date: str = ""
    posted_date: str = ""
    description: str = ""
    counterparty: str = ""
    deposit_amount: float = 0.0
    withdrawal_amount: float = 0.0
    amount_signed: float = 0.0
    balance: float = 0.0
    raw_memo: str = ""
    import_hash: str = ""

    def finalize(self) -> "BankTransaction":
        self.deposit_amount = _num(self.deposit_amount)
        self.withdrawal_amount = _num(self.withdrawal_amount)
        self.balance = _num(self.balance)
        self.amount_signed = self.deposit_amount - self.withdrawal_amount
        # 重複キー: transaction_date + description + amount_signed + balance + account_name
        self.import_hash = make_hash(
            self.transaction_date, self.description, self.amount_signed,
            self.balance, self.account_name,
        )
        if not self.transaction_id:
            self.transaction_id = "BANK-" + self.import_hash[:12]
        return self


@dataclass
class BankActualTransaction:
    """銀行口座実績レイヤー（会計確定パイプラインとは独立。BI/分析専用）。

    会計上の仕訳生成には使わない。口座残高の実績再現・費目候補分類・
    固定費/変動費更新候補のためのデータソース。
    """
    bank_account_key: str = ""
    source_file_name: str = ""
    source_file_hash: str = ""
    row_number: int = 0
    bank_branch: str = ""
    account_type: str = ""
    account_number_masked: str = ""
    transaction_date: str = ""
    value_date: str = ""
    withdrawal_amount: float = 0.0
    deposit_amount: float = 0.0
    signed_amount: float = 0.0
    balance_after: float = 0.0
    transaction_type: str = ""
    detail_type: str = ""
    counterparty_raw: str = ""
    counterparty_normalized: str = ""
    memo_raw: str = ""
    dedupe_key: str = ""
    created_at_jst: str = ""

    def finalize(self) -> "BankActualTransaction":
        self.withdrawal_amount = _num(self.withdrawal_amount)
        self.deposit_amount = _num(self.deposit_amount)
        self.balance_after = _num(self.balance_after)
        self.signed_amount = self.deposit_amount - self.withdrawal_amount
        self.dedupe_key = make_hash(
            self.bank_account_key, self.transaction_date, self.row_number,
            self.signed_amount, self.balance_after, self.counterparty_raw,
        )
        return self


@dataclass
class CashTransaction:
    cash_transaction_id: str = ""
    source_file: str = ""
    transaction_date: str = ""
    transaction_type: str = ""   # 現金支払/現金入金/現金移動/立替精算
    amount: float = 0.0
    tax_amount: float = 0.0
    tax_rate: str = ""
    category: str = ""
    vendor: str = ""
    description: str = ""
    payment_method: str = "現金"
    receipt_file: str = ""
    counterparty: str = ""
    review_status: str = "needs_review"  # needs_review/reviewed/approved
    memo: str = ""
    import_hash: str = ""

    def finalize(self) -> "CashTransaction":
        self.amount = _num(self.amount)
        self.tax_amount = _num(self.tax_amount)
        if not self.payment_method:
            self.payment_method = "現金"
        if not self.review_status:
            self.review_status = "needs_review"
        self.import_hash = make_hash(
            self.transaction_date, self.transaction_type, self.amount,
            self.vendor, self.description, self.receipt_file,
        )
        if not self.cash_transaction_id:
            self.cash_transaction_id = "CASH-" + self.import_hash[:12]
        return self


@dataclass
class BookingRecord:
    """Beds24予約レコード（売上の唯一の正規ソース）。"""
    booking_id: str = ""
    property_id: str = ""
    property_name: str = ""
    room_id: str = ""
    room_name: str = ""
    channel: str = ""
    guest_name: str = ""
    booking_date: str = ""
    checkin_date: str = ""
    checkout_date: str = ""
    stay_nights: int = 0
    rooms: int = 0
    guests: int = 0
    gross_revenue: float = 0.0
    ota_commission: float = 0.0
    net_revenue: float = 0.0
    tax_amount: float = 0.0
    status: str = ""
    payment_status: str = ""
    invoice_status: str = ""
    raw_json_path: str = ""
    import_hash: str = ""

    def finalize(self) -> "BookingRecord":
        self.gross_revenue = _num(self.gross_revenue)
        self.ota_commission = _num(self.ota_commission)
        self.net_revenue = _num(self.net_revenue)
        self.tax_amount = _num(self.tax_amount)
        self.stay_nights = int(_num(self.stay_nights))
        self.rooms = int(_num(self.rooms)) or 1
        self.guests = int(_num(self.guests))
        if not self.net_revenue:
            self.net_revenue = self.gross_revenue - self.ota_commission
        # booking_id が自然キー。状態変化も検知できるようhashに含める。
        self.import_hash = make_hash(
            self.booking_id, self.checkin_date, self.gross_revenue, self.status,
        )
        return self

    @property
    def stay_month(self) -> str:
        return self.checkin_date[:7] if self.checkin_date else ""

    def is_cancelled(self, exclude_statuses: List[str]) -> bool:
        return (self.status or "").lower() in [s.lower() for s in exclude_statuses]


@dataclass
class ManualAdjustment:
    adjustment_id: str = ""
    source_file: str = ""
    journal_date: str = ""
    description: str = ""
    debit_account: str = ""
    debit_subaccount: str = ""
    debit_amount: float = 0.0
    credit_account: str = ""
    credit_subaccount: str = ""
    credit_amount: float = 0.0
    tax_category: str = ""
    counterparty: str = ""
    memo: str = ""
    import_hash: str = ""

    def finalize(self) -> "ManualAdjustment":
        self.debit_amount = _num(self.debit_amount)
        self.credit_amount = _num(self.credit_amount)
        self.import_hash = make_hash(
            self.journal_date, self.description, self.debit_account,
            self.debit_amount, self.credit_account, self.credit_amount,
        )
        if not self.adjustment_id:
            self.adjustment_id = "ADJ-" + self.import_hash[:12]
        return self


@dataclass
class OpeningBalance:
    """会計士確定の開始残高（科目別 試算表スナップショット）。"""
    as_of_date: str = ""
    account: str = ""
    subaccount: str = ""
    debit_total: float = 0.0
    credit_total: float = 0.0
    source_file: str = ""
    memo: str = ""
    import_hash: str = ""

    def finalize(self) -> "OpeningBalance":
        self.debit_total = _num(self.debit_total)
        self.credit_total = _num(self.credit_total)
        self.import_hash = make_hash(
            self.as_of_date, self.account, self.subaccount,
            self.debit_total, self.credit_total,
        )
        return self


@dataclass
class LoanScheduleEntry:
    """月次債務返済予定表（Phase B）。返済予定表と銀行明細が一致した場合のみ仕訳化する。"""
    loan_id: str = ""
    lender: str = ""
    liability_account: str = ""   # 短期借入金/長期借入金/関係会社借入金/役員借入金/長期未払金 のみ許可
    payment_date: str = ""
    total_payment: float = 0.0
    principal_payment: float = 0.0
    interest_payment: float = 0.0
    ending_balance: float = 0.0
    bank_description_match: str = ""
    memo: str = ""
    source_file: str = ""
    import_hash: str = ""

    def finalize(self) -> "LoanScheduleEntry":
        self.total_payment = _num(self.total_payment)
        self.principal_payment = _num(self.principal_payment)
        self.interest_payment = _num(self.interest_payment)
        self.ending_balance = _num(self.ending_balance)
        self.payment_date = str(self.payment_date)[:10]
        if not self.loan_id:
            self.loan_id = "LOAN-" + make_hash(
                self.lender, self.liability_account, self.payment_date,
                self.total_payment)[:12]
        self.import_hash = make_hash(
            self.loan_id, self.payment_date, self.total_payment,
            self.principal_payment, self.interest_payment,
        )
        return self


@dataclass
class JournalEntry:
    journal_id: str = ""
    journal_date: str = ""
    month: str = ""
    property: str = "喜らく"
    description: str = ""
    debit_account: str = ""
    debit_subaccount: str = ""
    debit_amount: float = 0.0
    credit_account: str = ""
    credit_subaccount: str = ""
    credit_amount: float = 0.0
    tax_category: str = ""
    counterparty: str = ""
    source: str = ""          # beds24 / bank / cash / manual
    source_id: str = ""
    confidence: str = "high"  # high/medium/low
    rule_id: str = ""
    memo: str = ""

    def finalize(self) -> "JournalEntry":
        self.debit_amount = _num(self.debit_amount)
        self.credit_amount = _num(self.credit_amount)
        if not self.month and self.journal_date:
            self.month = self.journal_date[:7]
        if not self.property:
            self.property = "喜らく"
        if not self.journal_id:
            self.journal_id = "JNL-" + make_hash(
                self.source, self.source_id, self.debit_account,
                self.credit_account, self.debit_amount, self.rule_id,
            )[:14]
        return self


def to_dict(obj) -> dict:
    return asdict(obj)


def field_names(cls) -> List[str]:
    return [f.name for f in fields(cls)]
