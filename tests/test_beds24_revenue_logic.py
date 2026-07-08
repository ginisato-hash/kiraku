"""Beds24 速報売上ロジック v2（クーポン加算・キャンセル除外）。

実データ調査(Phase 0)の結論: couponはinvoiceItems type=paymentにのみ出現し、
type=chargeには出現しないため、既定ではbeds24_coupon_revenue_included=0。
type=chargeにcoupon等の説明を持つ行があれば自動的に加算される設計を検証する。
"""
from yuge_finance.accounting import beds24_revenue_logic as brl
from yuge_finance.normalize.schema import BookingRecord

EXCLUDE = ["cancelled", "canceled", "black"]


def _booking(bid, checkin, gross=10000, status="confirmed", raw_json_path=None):
    return BookingRecord(booking_id=bid, checkin_date=checkin, checkout_date=checkin,
                         gross_revenue=gross, status=status,
                         raw_json_path=raw_json_path or "").finalize()


# ---------------- is_beds24_cancelled_booking ----------------
def test_is_cancelled_by_status_field():
    assert brl.is_beds24_cancelled_booking({"status": "cancelled"}) is True
    assert brl.is_beds24_cancelled_booking({"status": "confirmed"}) is False


def test_is_cancelled_by_cancel_time_fallback():
    assert brl.is_beds24_cancelled_booking({"status": "unknown", "cancelTime": "2026-06-01T00:00:00Z"}) is True


def test_is_cancelled_false_when_no_signal():
    assert brl.is_beds24_cancelled_booking({}) is False
    assert brl.is_beds24_cancelled_booking({"price": 1000}) is False


def test_has_cancel_signal_fields():
    assert brl.has_cancel_signal_fields({"status": "confirmed"}) is True
    assert brl.has_cancel_signal_fields({"cancelTime": None}) is True
    assert brl.has_cancel_signal_fields({}) is False


# ---------------- extract_beds24_coupon_revenue ----------------
def test_coupon_in_payment_line_not_counted_as_revenue():
    """実データの実態: couponはtype=paymentの決済手段。加算しない。"""
    raw = {"invoiceItems": [
        {"type": "charge", "description": "[ROOMNAME1]", "lineTotal": 10000},
        {"type": "payment", "description": "coupon", "lineTotal": -3000},
    ]}
    assert brl.extract_beds24_coupon_revenue(raw) == 0.0


def test_coupon_in_charge_line_is_counted_as_revenue():
    """将来、type=chargeにcoupon説明の行が現れた場合は自動加算する。"""
    raw = {"invoiceItems": [
        {"type": "charge", "description": "[ROOMNAME1]", "lineTotal": 10000},
        {"type": "charge", "description": "coupon subsidy", "lineTotal": 2000},
    ]}
    assert brl.extract_beds24_coupon_revenue(raw) == 2000.0


def test_guest_discount_not_counted():
    """収入扱いと確認できないdiscountは加算しない。"""
    raw = {"invoiceItems": [
        {"type": "charge", "description": "guest discount", "lineTotal": 500},
    ]}
    assert brl.extract_beds24_coupon_revenue(raw) == 0.0


def test_negative_charge_amount_not_counted():
    raw = {"invoiceItems": [
        {"type": "charge", "description": "coupon adjustment", "lineTotal": -500},
    ]}
    assert brl.extract_beds24_coupon_revenue(raw) == 0.0


# ---------------- compute (integration) ----------------
def test_cancelled_booking_excluded_and_counted(tmp_path):
    raw_path = tmp_path / "2026-06.json"
    raw_path.write_text('[{"id": "1", "status": "cancelled", "cancelTime": "2026-06-01T00:00:00Z", "invoiceItems": []}]',
                        encoding="utf-8")
    bookings = [_booking("1", "2026-06-10", status="cancelled", raw_json_path=str(raw_path))]
    result = brl.compute("2026-06", bookings, EXCLUDE)
    assert result["beds24_cancelled_booking_count"] == 1
    assert result["beds24_coupon_revenue_included"] == 0


def test_coupon_booking_count_increments_only_for_charge_coupon(tmp_path):
    raw_path = tmp_path / "2026-06.json"
    raw_path.write_text(
        '[{"id": "1", "status": "confirmed", "invoiceItems": '
        '[{"type": "charge", "description": "coupon subsidy", "lineTotal": 2000}]},'
        '{"id": "2", "status": "confirmed", "invoiceItems": '
        '[{"type": "payment", "description": "coupon", "lineTotal": -1000}]}]',
        encoding="utf-8")
    bookings = [_booking("1", "2026-06-10", raw_json_path=str(raw_path)),
                _booking("2", "2026-06-11", raw_json_path=str(raw_path))]
    result = brl.compute("2026-06", bookings, EXCLUDE)
    assert result["beds24_coupon_revenue_included"] == 2000
    assert result["beds24_coupon_booking_count"] == 1  # booking 2のpayment型couponは加算されない
    assert result["beds24_revenue_logic_status"] == "coupon_included"


def test_coupon_field_missing_status_when_no_charge_coupon(tmp_path):
    raw_path = tmp_path / "2026-06.json"
    raw_path.write_text(
        '[{"id": "1", "status": "confirmed", "invoiceItems": '
        '[{"type": "charge", "description": "[ROOMNAME1]", "lineTotal": 10000},'
        ' {"type": "payment", "description": "coupon", "lineTotal": -3000}]}]',
        encoding="utf-8")
    bookings = [_booking("1", "2026-06-10", raw_json_path=str(raw_path))]
    result = brl.compute("2026-06", bookings, EXCLUDE)
    assert result["beds24_coupon_revenue_included"] == 0
    assert "coupon_field_missing" in result["beds24_revenue_logic_status"]


def test_raw_payload_unavailable_does_not_crash():
    bookings = [_booking("1", "2026-06-10", raw_json_path="")]
    result = brl.compute("2026-06", bookings, EXCLUDE)
    assert result["beds24_coupon_revenue_included"] == 0
    assert result["beds24_revenue_logic_status"] == "raw_payload_unavailable"


# ---------------- revenue_recon integration ----------------
def test_compat_field_equals_net_for_bi():
    from yuge_finance.accounting import revenue_recon
    bookings = [_booking("1", "2026-06-10", gross=30000)]
    rec = revenue_recon.compute("2026-06", bookings, [], [], [])
    assert rec["beds24_stay_month_revenue_excluding_cancelled"] == rec["beds24_revenue_net_for_bi"]


def test_snapshot_fields_present_in_revenue_recon():
    from yuge_finance.accounting import revenue_recon
    bookings = [_booking("1", "2026-06-10", gross=30000)]
    rec = revenue_recon.compute("2026-06", bookings, [], [], [])
    for field in ["beds24_revenue_gross_stay", "beds24_coupon_revenue_included",
                 "beds24_cancelled_revenue_excluded", "beds24_revenue_net_for_bi",
                 "beds24_revenue_logic_version", "beds24_revenue_logic_status",
                 "beds24_revenue_logic_note", "beds24_cancelled_booking_count",
                 "beds24_coupon_booking_count"]:
        assert field in rec, f"missing field: {field}"
