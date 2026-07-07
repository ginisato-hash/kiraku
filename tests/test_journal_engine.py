from yuge_finance.accounting import bank_journal, journal_engine
from yuge_finance.normalize import validators
from yuge_finance.normalize.schema import (BankTransaction, BookingRecord,
                                           CashTransaction)


def _booking():
    return BookingRecord(booking_id="B1", channel="Booking.com",
                         checkin_date="2026-07-10", checkout_date="2026-07-12",
                         gross_revenue=30000, ota_commission=4500,
                         status="confirmed").finalize()


def _bank_known():
    return BankTransaction(account_name="本店", transaction_date="2026-07-10",
                           description="東北電力 電気料金", withdrawal_amount=32000,
                           balance=100000).finalize()


def test_bank_rule_applied():
    tx = _bank_known()
    cfg = __import__("yuge_finance.config", fromlist=["x"]).load_yaml("journal_rules.yml")
    cls = bank_journal.classify(tx, cfg)
    assert cls["rule_id"] == "bank_utilities"
    assert cls["confidence"] == "high"


def test_unapproved_cash_not_confirmed():
    cash = [
        CashTransaction(transaction_date="2026-07-03", transaction_type="現金支払",
                        amount=2480, category="消耗品費", review_status="approved").finalize(),
        CashTransaction(transaction_date="2026-07-06", transaction_type="現金支払",
                        amount=5000, category="消耗品費", review_status="needs_review").finalize(),
    ]
    out = journal_engine.build("2026-07", [], [], cash, [])
    confirmed_sources = {e.source_id for e in out["confirmed"] if e.source == "cash"}
    exception_sources = {e.source_id for e in out["exceptions"] if e.source == "cash"}
    assert cash[0].cash_transaction_id in confirmed_sources
    assert cash[1].cash_transaction_id in exception_sources
    assert cash[1].cash_transaction_id not in confirmed_sources


def test_journal_balanced():
    out = journal_engine.build("2026-07", [_booking()], [_bank_known()], [], [])
    ok, d, c = validators.journal_balanced(out["confirmed"])
    assert ok
    assert d == c


def test_beds24_does_not_create_pl_journal():
    # 新方針: Beds24は速報。確定PL仕訳(宿泊売上)を作らない。
    out = journal_engine.build("2026-07", [_booking()], [], [], [])
    rev = [e for e in out["confirmed"] if e.credit_account == "宿泊売上"]
    assert rev == []
    assert out["by_source"]["beds24"] == 0


def test_bank_ota_deposit_is_recognized_revenue():
    # 入金ベース確定: OTA入金 → 宿泊売上(貸方)
    dep = BankTransaction(account_name="本店", transaction_date="2026-07-05",
                          description="楽天トラベル入金", deposit_amount=250000,
                          balance=250000).finalize()
    out = journal_engine.build("2026-07", [], [dep], [], [])
    rev = [e for e in out["confirmed"]
           if e.credit_account == "宿泊売上" and e.source == "bank"]
    assert sum(e.credit_amount for e in rev) == 250000
