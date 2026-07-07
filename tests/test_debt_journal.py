"""Phase H-2: 月次債務返済（返済予定表 x 銀行明細マッチング）。"""
from yuge_finance.accounting import debt_journal
from yuge_finance.normalize.schema import BankTransaction, LoanScheduleEntry


def _bank(desc, amt, date="2026-06-25"):
    return BankTransaction(account_name="本店", transaction_date=date,
                           description=desc, withdrawal_amount=amt, balance=0).finalize()


def _schedule(**kw):
    base = dict(lender="きらやか銀行", liability_account="長期借入金",
               payment_date="2026-06-25", total_payment=650000,
               principal_payment=600000, interest_payment=50000,
               ending_balance=41320000, bank_description_match="長期借入金返済")
    base.update(kw)
    return LoanScheduleEntry(**base).finalize()


def test_matched_schedule_creates_confirmed_journal_principal_not_pl():
    sched = [_schedule()]
    bank = [_bank("長期借入金返済", 650000)]
    out = debt_journal.build("2026-06", sched, bank)
    assert out["exceptions"] == []
    assert len(out["confirmed"]) == 2  # 元本 + 利息
    principal_entry = next(e for e in out["confirmed"] if e.rule_id == "debt_repayment_matched")
    assert principal_entry.debit_account == "借入金"       # BS科目。PL費用ではない
    assert principal_entry.debit_amount == 600000
    interest_entry = next(e for e in out["confirmed"] if e.rule_id == "debt_interest_matched")
    assert interest_entry.debit_account == "支払利息"       # PL計上対象は利息のみ
    assert interest_entry.debit_amount == 50000
    assert out["monthly_debt_principal_payment"] == 600000
    assert out["monthly_debt_interest_payment"] == 50000
    assert out["debt_service_status"] == "予定表投入済"


def test_no_schedule_is_not_critical_pending_status():
    """政策金融公庫など返済予定表が無い場合は『予定表未投入』（criticalではない）。"""
    out = debt_journal.build("2026-06", [], [_bank("ｾｲｻｸｺｳｺ(ｺｸﾐﾝ", 35243)])
    assert out["confirmed"] == []
    assert out["exceptions"] == []
    assert out["debt_service_status"] == "予定表未投入"


def test_schedule_without_bank_match_is_exception_not_confirmed():
    sched = [_schedule(bank_description_match="存在しない摘要")]
    out = debt_journal.build("2026-06", sched, [_bank("何か別の取引", 1000)])
    assert out["confirmed"] == []
    assert len(out["exceptions"]) == 1
    assert out["exceptions"][0].rule_id == "debt_no_bank_match"
    assert out["debt_service_status"] == "要確認"


def test_principal_plus_interest_mismatch_is_flagged_internal_mismatch():
    sched = [_schedule(principal_payment=600000, interest_payment=40000, total_payment=650000)]
    out = debt_journal.build("2026-06", sched, [_bank("長期借入金返済", 650000)])
    assert out["confirmed"] == []
    assert len(out["exceptions"]) == 1
    assert out["exceptions"][0].rule_id == "debt_internal_mismatch"


def test_invalid_liability_account_is_flagged():
    sched = [_schedule(liability_account="仕入代金")]   # 許可値外
    out = debt_journal.build("2026-06", sched, [_bank("長期借入金返済", 650000)])
    assert out["confirmed"] == []
    assert any(e.rule_id == "debt_invalid_liability_account" for e in out["exceptions"])


def test_debt_balance_from_records_excludes_long_term_payable_by_default():
    from yuge_finance.normalize.schema import OpeningBalance
    records = [
        OpeningBalance(account="借入金", credit_total=118128637).finalize(),
        OpeningBalance(account="その他負債", subaccount="長期未払金", credit_total=4651180).finalize(),
    ]
    total = debt_journal.debt_balance_from_records(records, include_long_term_payable=False)
    assert total == 118128637
    total_with = debt_journal.debt_balance_from_records(records, include_long_term_payable=True)
    assert total_with == 118128637 + 4651180
