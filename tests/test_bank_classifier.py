"""銀行口座実績 費目候補分類のテスト。会計仕訳(journal_rules.yml)とは独立。"""
from yuge_finance.accounting import bank_classifier
from yuge_finance.normalize.schema import BankActualTransaction


def _tx(counterparty, amount, direction="withdrawal"):
    dep = amount if direction == "deposit" else 0
    wd = amount if direction == "withdrawal" else 0
    return BankActualTransaction(
        bank_account_key="X", transaction_date="2026-06-01", row_number=1,
        counterparty_raw=counterparty, memo_raw=counterparty,
        deposit_amount=dep, withdrawal_amount=wd, balance_after=1000,
    ).finalize()


def test_normalize_counterparty_half_width_kana_to_readable():
    normalized = bank_classifier.normalize_counterparty("ｽｽﾞｷｱﾌﾞﾗﾃﾝ")
    assert normalized == "スズキアブラテン"


def test_high_confidence_known_vendor_tax_accountant_is_auto_reflectable_candidate():
    tx = _tx("ZHｾﾞｲﾘｼﾎｳｼﾕｳ", 39600)
    cls = bank_classifier.classify_bank_transaction(tx)
    assert cls["cost_model_category"] == "tax_accountant_fee"
    assert cls["fixed_or_variable"] == "fixed"
    assert cls["confidence"] == "high"
    assert cls["requires_review"] is False
    assert cls["auto_reflectable"] is True


def test_unknown_counterparty_requires_review():
    tx = _tx("ﾅﾆｶﾜｶﾗﾅｲﾌﾘｺﾐｻｷ", 5000)
    cls = bank_classifier.classify_bank_transaction(tx)
    assert cls["cost_model_category"] == "unknown"
    assert cls["requires_review"] is True
    assert cls["confidence"] == "low"


def test_blank_memo_requires_review():
    tx = _tx("", 5000)
    cls = bank_classifier.classify_bank_transaction(tx)
    assert cls["requires_review"] is True


def test_personal_name_not_assumed_payroll_or_contractor():
    tx = _tx("ｵｶｻﾞｷ ｼﾕｳｺ", 135000)
    cls = bank_classifier.classify_bank_transaction(tx)
    assert cls["cost_model_category"] not in ("payroll_or_staff_payment", "外注費")
    assert cls["requires_review"] is True


def test_takamiya_deposit_is_not_revenue():
    tx = _tx("ｶ.ﾀｶﾐﾔﾘﾖｶﾝ", 34000000, direction="deposit")
    cls = bank_classifier.classify_bank_transaction(tx)
    assert cls["cost_model_category"] not in (
        "revenue_cash_in", "ota_receivable_collection", "coupon_point_receivable_collection")
    assert cls["cost_model_category"] == "owner_related_cash_in"
    assert cls["requires_review"] is True


def test_ota_deposit_flagged_variable_revenue_linked():
    tx = _tx("ﾗｸﾃﾝｸﾞﾙ-ﾌﾟ(ｶ", 248452, direction="deposit")
    cls = bank_classifier.classify_bank_transaction(tx)
    assert cls["cost_model_category"] == "ota_receivable_collection"
    assert cls["fixed_or_variable"] == "variable_revenue_linked"


def test_jtb_coupon_settlement_flagged_as_coupon_point_collection():
    tx = _tx("JTBｸ-ﾎﾟﾝｾｲｻﾝｾﾝﾀ-", 322050, direction="deposit")
    cls = bank_classifier.classify_bank_transaction(tx)
    assert cls["cost_model_category"] == "coupon_point_receivable_collection"


def test_evidence_source_present_for_web_researched_vendor():
    tx = _tx("SMBC(ﾆﾂﾎﾟﾝｵ-ﾁｽ", 50600)
    cls = bank_classifier.classify_bank_transaction(tx)
    assert cls["cost_model_category"] == "elevator_maintenance"
    assert cls["evidence_source"] is not None
    assert cls["evidence_source"]["evidence_source_url"]
    assert cls["evidence_source"]["researched_at_jst"]


def test_vehicle_loan_trio_flagged_as_debt_service_requires_review():
    for name in ("ｼﾞﾔﾂｸｽ", "JC VWﾌｱｲﾅﾝｽ", "ｵﾘｺ.ｼ-ﾅﾂﾂ"):
        cls = bank_classifier.classify_bank_transaction(_tx(name, 20000))
        assert cls["cost_model_category"] == "vehicle"
        assert cls["fixed_or_variable"] == "debt_service"
        assert cls["requires_review"] is True
