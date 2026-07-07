from yuge_finance.accounting import journal_engine, revenue_recon
from yuge_finance.normalize.schema import BankTransaction, BookingRecord


def _booking(bid, gross, status="confirmed", checkin="2026-06-10"):
    return BookingRecord(booking_id=bid, channel="じゃらんnet", checkin_date=checkin,
                         checkout_date="2026-06-11", gross_revenue=gross,
                         status=status).finalize()


def test_stay_month_sokuho_when_no_bank():
    # 銀行未投入 → 速報。Beds24宿泊月速報のみ。
    bookings = [_booking("B1", 30000), _booking("B2", 20000),
                _booking("B3", 5000, status="cancelled")]
    out = journal_engine.build("2026-06", bookings, [], [], [])
    rr = revenue_recon.compute("2026-06", bookings, out["confirmed"], [], [])
    assert rr["beds24_stay_month_gross_revenue"] == 55000
    assert rr["beds24_stay_month_revenue_excluding_cancelled"] == 50000
    assert rr["beds24_stay_month_cancelled_revenue"] == 5000
    assert rr["bank_deposit_month_ota_revenue"] == 0
    assert rr["revenue_data_status"] == "速報"
    # 同月比較は適用外
    assert rr["same_month_revenue_comparison_applicable"] is False
    assert rr["revenue_comparison_status"] == "同月比較対象外"


def test_deposit_month_pending_not_compared():
    # OTA入金あり → 精算明細待ち。宿泊月売上と同月差分で判定しない。
    bookings = [_booking("B1", 30000), _booking("B2", 20000)]   # 宿泊月50,000
    dep = BankTransaction(account_name="本店", transaction_date="2026-06-25",
                          description="ﾗｸﾃﾝｸﾞﾙ-ﾌﾟ(ｶ", deposit_amount=462589,
                          balance=462589).finalize()
    out = journal_engine.build("2026-06", bookings, [dep], [], [])
    rr = revenue_recon.compute("2026-06", bookings, out["confirmed"], [dep], [])
    # B: 入金月実績
    assert rr["bank_deposit_month_ota_revenue"] == 462589
    assert rr["bank_deposit_month_total_inflow"] == 462589
    # A: 宿泊月速報（入金とは別コホート）
    assert rr["beds24_stay_month_revenue_excluding_cancelled"] == 50000
    # 入金が宿泊月速報を上回っても「会計確定」にしない（精算明細未照合）
    assert rr["revenue_data_status"] == "精算明細待ち"
    assert rr["same_month_revenue_comparison_applicable"] is False


def test_kpi_fields_present():
    bookings = [_booking("B1", 30000)]
    out = journal_engine.build("2026-06", bookings, [], [], [])
    rr = revenue_recon.compute("2026-06", bookings, out["confirmed"], [], [])
    for k in ["adr", "revpar", "occupancy", "available_room_nights",
              "ota_settlement_lag_note", "settlement_reconciliation_status"]:
        assert k in rr
    assert rr["available_room_nights"] == 19 * 30   # 19室 × 6月30日
