"""Beds24 速報売上ロジック v3（point加算・coupon直割引の明確化）。

実データ調査(Phase 0)の結論:
  - coupon は invoiceItems type=payment（決済手段）にのみ出現し、直割引扱い。売上に加算しない。
  - point も実データでは invoiceItems type=payment にのみ出現し、price に既に含まれているため
    現状は加算額0（point_already_included_in_price）。type=chargeにpoint行が現れれば自動加算。
"""
from datetime import date, timedelta

from yuge_finance.accounting import beds24_revenue_logic as brl
from yuge_finance.normalize.schema import BookingRecord

EXCLUDE = ["cancelled", "canceled", "black"]


def _next_day(d: str) -> str:
    return (date.fromisoformat(d) + timedelta(days=1)).isoformat()


def _booking(bid, checkin, checkout=None, gross=10000, status="confirmed", raw_json_path=None):
    # 既定checkoutはcheckin+1日（実データ同様、最低1泊とする）
    return BookingRecord(booking_id=bid, checkin_date=checkin,
                         checkout_date=checkout or _next_day(checkin),
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


def test_has_cancel_signal_fields():
    assert brl.has_cancel_signal_fields({"status": "confirmed"}) is True
    assert brl.has_cancel_signal_fields({}) is False


# ---------------- extract_beds24_point_revenue (加算対象) ----------------
def test_point_in_payment_line_not_counted_as_revenue():
    """実データの実態: pointはtype=paymentの決済手段。priceに既に含まれるため加算しない。"""
    raw = {"invoiceItems": [
        {"type": "charge", "description": "[ROOMNAME1]", "lineTotal": 10000},
        {"type": "payment", "description": "point", "lineTotal": -1000},
    ]}
    assert brl.extract_beds24_point_revenue(raw) == 0.0


def test_point_in_charge_line_is_counted_as_revenue():
    """将来、type=chargeにpoint起因の追加行が現れた場合は自動加算する。"""
    raw = {"invoiceItems": [
        {"type": "charge", "description": "[ROOMNAME1]", "lineTotal": 10000},
        {"type": "charge", "description": "point reward", "lineTotal": 2000},
    ]}
    assert brl.extract_beds24_point_revenue(raw) == 2000.0


def test_negative_point_charge_not_counted():
    raw = {"invoiceItems": [
        {"type": "charge", "description": "point adjustment", "lineTotal": -500},
    ]}
    assert brl.extract_beds24_point_revenue(raw) == 0.0


# ---------------- extract_beds24_coupon_discount (直割引・非加算) ----------------
def test_coupon_discount_extracted_as_positive_amount():
    """couponは直割引。金額は絶対値で保持するが、売上には加算しない。"""
    raw = {"invoiceItems": [
        {"type": "charge", "description": "[ROOMNAME1]", "lineTotal": 10000},
        {"type": "payment", "description": "coupon", "lineTotal": -3000},
    ]}
    assert brl.extract_beds24_coupon_discount(raw) == 3000.0


def test_extract_beds24_coupon_revenue_deprecated_always_zero():
    """旧関数は非推奨。常に0を返し、収入としては扱わない。"""
    raw = {"invoiceItems": [
        {"type": "charge", "description": "coupon subsidy", "lineTotal": 2000},
    ]}
    assert brl.extract_beds24_coupon_revenue(raw) == 0.0


# ---------------- compute (integration): point加算テスト ----------------
def test_point_booking_count_increments_only_for_charge_point(tmp_path):
    raw_path = tmp_path / "2026-06.json"
    raw_path.write_text(
        '[{"id": "1", "status": "confirmed", "invoiceItems": '
        '[{"type": "charge", "description": "point reward", "lineTotal": 2000}]},'
        '{"id": "2", "status": "confirmed", "invoiceItems": '
        '[{"type": "payment", "description": "point", "lineTotal": -1000}]}]',
        encoding="utf-8")
    bookings = [_booking("1", "2026-06-10", raw_json_path=str(raw_path)),
                _booking("2", "2026-06-11", raw_json_path=str(raw_path))]
    result = brl.compute("2026-06", bookings, EXCLUDE)
    assert result["beds24_point_revenue_included"] == 2000
    assert result["beds24_point_booking_count"] == 1  # booking 2のpayment型pointは加算されない
    assert result["beds24_revenue_logic_status"] == "point_added_from_invoice_items"


def test_point_already_included_in_price_status_when_only_payment_point(tmp_path):
    raw_path = tmp_path / "2026-06.json"
    raw_path.write_text(
        '[{"id": "1", "status": "confirmed", "invoiceItems": '
        '[{"type": "charge", "description": "[ROOMNAME1]", "lineTotal": 10000},'
        ' {"type": "payment", "description": "point", "lineTotal": -1000}]}]',
        encoding="utf-8")
    bookings = [_booking("1", "2026-06-10", raw_json_path=str(raw_path))]
    result = brl.compute("2026-06", bookings, EXCLUDE)
    assert result["beds24_point_revenue_included"] == 0
    assert result["beds24_revenue_logic_status"] == "point_already_included_in_price"


def test_cancelled_booking_point_not_counted(tmp_path):
    """キャンセル済み予約のpointは加算しない。"""
    raw_path = tmp_path / "2026-06.json"
    raw_path.write_text(
        '[{"id": "1", "status": "cancelled", "cancelTime": "2026-06-01T00:00:00Z", "invoiceItems": '
        '[{"type": "charge", "description": "point reward", "lineTotal": 2000}]}]',
        encoding="utf-8")
    bookings = [_booking("1", "2026-06-10", status="cancelled", raw_json_path=str(raw_path))]
    result = brl.compute("2026-06", bookings, EXCLUDE)
    assert result["beds24_point_revenue_included"] == 0
    assert result["beds24_point_booking_count"] == 0
    assert result["beds24_cancelled_booking_count"] == 1


def test_point_prorated_to_stay_month(tmp_path):
    """pointは宿泊月按分される。全4泊のうち対象月2泊なら半分。"""
    raw_path = tmp_path / "2026-06.json"
    raw_path.write_text(
        '[{"id": "1", "status": "confirmed", "invoiceItems": '
        '[{"type": "charge", "description": "point reward", "lineTotal": 10000}]}]',
        encoding="utf-8")
    # 6/29 チェックイン, 7/3 チェックアウト = 4泊。6月分は2泊(6/29,6/30)。
    bookings = [_booking("1", "2026-06-29", checkout="2026-07-03", raw_json_path=str(raw_path))]
    result = brl.compute("2026-06", bookings, EXCLUDE)
    assert result["beds24_point_revenue_included"] == 5000  # 10000 * 2/4


# ---------------- compute (integration): coupon非加算テスト ----------------
def test_coupon_does_not_affect_point_revenue(tmp_path):
    raw_path = tmp_path / "2026-06.json"
    raw_path.write_text(
        '[{"id": "1", "status": "confirmed", "invoiceItems": '
        '[{"type": "charge", "description": "[ROOMNAME1]", "lineTotal": 10000},'
        ' {"type": "payment", "description": "coupon", "lineTotal": -3000}]}]',
        encoding="utf-8")
    bookings = [_booking("1", "2026-06-10", raw_json_path=str(raw_path))]
    result = brl.compute("2026-06", bookings, EXCLUDE)
    assert result["beds24_point_revenue_included"] == 0
    assert result["beds24_coupon_discount_amount"] == 3000
    assert result["beds24_coupon_discount_detected"] is True
    assert result["beds24_coupon_discount_booking_count"] == 1
    # 旧field(deprecated)は常に0
    assert result["beds24_coupon_revenue_included"] == 0
    assert result["beds24_coupon_booking_count"] == 0


def test_raw_payload_unavailable_does_not_crash():
    bookings = [_booking("1", "2026-06-10", raw_json_path="")]
    result = brl.compute("2026-06", bookings, EXCLUDE)
    assert result["beds24_point_revenue_included"] == 0
    assert result["beds24_revenue_logic_status"] == "raw_payload_unavailable"


# ---------------- revenue_recon integration ----------------
def test_net_revenue_formula_uses_point_not_coupon():
    """net = gross_stay_revenue + point_revenue - cancelled（couponは含まない）。"""
    from yuge_finance.accounting import revenue_recon
    bookings = [_booking("1", "2026-06-10", gross=30000)]
    rec = revenue_recon.compute("2026-06", bookings, [], [], [])
    assert rec["beds24_revenue_net_for_bi"] == rec["beds24_revenue_gross_stay"] + rec["beds24_point_revenue_included"]


def test_compat_field_equals_net_for_bi():
    from yuge_finance.accounting import revenue_recon
    bookings = [_booking("1", "2026-06-10", gross=30000)]
    rec = revenue_recon.compute("2026-06", bookings, [], [], [])
    assert rec["beds24_stay_month_revenue_excluding_cancelled"] == rec["beds24_revenue_net_for_bi"]


def test_snapshot_fields_present_in_revenue_recon():
    from yuge_finance.accounting import revenue_recon
    bookings = [_booking("1", "2026-06-10", gross=30000)]
    rec = revenue_recon.compute("2026-06", bookings, [], [], [])
    for field in ["beds24_revenue_gross_stay", "beds24_point_revenue_included",
                 "beds24_point_booking_count", "beds24_coupon_discount_detected",
                 "beds24_coupon_discount_amount", "beds24_coupon_discount_booking_count",
                 "beds24_cancelled_revenue_excluded", "beds24_revenue_net_for_bi",
                 "beds24_revenue_logic_version", "beds24_revenue_logic_status",
                 "beds24_revenue_logic_note", "beds24_cancelled_booking_count"]:
        assert field in rec, f"missing field: {field}"


def test_deprecated_coupon_fields_still_present_but_zero():
    from yuge_finance.accounting import revenue_recon
    bookings = [_booking("1", "2026-06-10", gross=30000)]
    rec = revenue_recon.compute("2026-06", bookings, [], [], [])
    assert rec["beds24_coupon_revenue_included"] == 0
    assert rec["beds24_coupon_booking_count"] == 0


# ---------------- extract_beds24_onsite_payment_revenue（現地決済/現地払い） ----------------
def test_onsite_payment_method_only_not_counted_as_revenue():
    """実データの実態: 現地決済はtype=payment(lineTotal=0)の決済手段マーカー。
    room chargeは既にtype=chargeでpriceに全額計上済みのため加算しない。"""
    raw = {"price": 13000, "invoiceItems": [
        {"type": "charge", "description": "[ROOMNAME1]", "lineTotal": 13000},
        {"type": "payment", "description": "現地支払い", "lineTotal": 0},
    ]}
    result = brl.extract_beds24_onsite_payment_revenue(raw)
    assert result["added_amount"] == 0.0
    assert result["added_count"] == 0
    assert result["status"] == "payment_method_only_not_revenue"
    assert result["candidate_count"] == 1


def test_onsite_payment_as_payment_type_matching_price_not_added():
    """charge合計==priceかつonsiteがtype=paymentのみ => 加算禁止。"""
    raw = {"price": 14500, "invoiceItems": [
        {"type": "charge", "description": "[ROOMNAME1]", "lineTotal": 14500},
        {"type": "payment", "description": "pay at property", "lineTotal": 5000},
    ]}
    result = brl.extract_beds24_onsite_payment_revenue(raw)
    assert result["added_amount"] == 0.0
    assert result["status"] == "payment_method_only_not_revenue"


def test_onsite_payment_as_separate_charge_not_in_price_is_added():
    """onsite決済がtype=chargeの別行として存在し、price(charge合計超過分)に含まれない場合は加算する。"""
    raw = {"price": 10000, "invoiceItems": [
        {"type": "charge", "description": "[ROOMNAME1]", "lineTotal": 10000},
        {"type": "charge", "description": "現地決済追加室料", "lineTotal": 3000},
    ]}
    result = brl.extract_beds24_onsite_payment_revenue(raw)
    assert result["added_amount"] == 3000.0
    assert result["added_count"] == 1
    assert result["status"] == "added_from_separate_charge"


def test_onsite_payment_charge_within_price_not_double_counted():
    """onsite決済がtype=chargeでもcharge合計がprice以内なら既に反映済みとみなし加算しない。"""
    raw = {"price": 10000, "invoiceItems": [
        {"type": "charge", "description": "現地決済", "lineTotal": 10000},
    ]}
    result = brl.extract_beds24_onsite_payment_revenue(raw)
    assert result["added_amount"] == 0.0
    assert result["status"] == "already_included_in_price"


def test_onsite_payment_candidate_not_selected_when_amount_non_positive():
    raw = {"price": 10000, "invoiceItems": [
        {"type": "charge", "description": "[ROOMNAME1]", "lineTotal": 10000},
        {"type": "charge", "description": "現地決済 値引き", "lineTotal": -500},
    ]}
    result = brl.extract_beds24_onsite_payment_revenue(raw)
    assert result["added_amount"] == 0.0
    assert result["status"] == "candidate_not_selected"


def test_onsite_payment_field_missing_when_no_signal():
    raw = {"price": 10000, "invoiceItems": [
        {"type": "charge", "description": "[ROOMNAME1]", "lineTotal": 10000},
    ]}
    result = brl.extract_beds24_onsite_payment_revenue(raw)
    assert result["candidate_count"] == 0
    assert result["status"] == "field_missing"


def test_onsite_payment_empty_raw_is_field_missing():
    result = brl.extract_beds24_onsite_payment_revenue({})
    assert result["status"] == "field_missing"
    assert result["added_amount"] == 0.0


# ---------------- compute (integration): 現地決済 ----------------
def test_compute_cancelled_booking_onsite_not_counted(tmp_path):
    raw_path = tmp_path / "2026-06.json"
    raw_path.write_text(
        '[{"id": "1", "status": "cancelled", "cancelTime": "2026-06-01T00:00:00Z", "price": 10000, '
        '"invoiceItems": [{"type": "charge", "description": "[ROOMNAME1]", "lineTotal": 10000},'
        ' {"type": "charge", "description": "現地決済", "lineTotal": 3000}]}]',
        encoding="utf-8")
    bookings = [_booking("1", "2026-06-10", status="cancelled", raw_json_path=str(raw_path))]
    result = brl.compute("2026-06", bookings, EXCLUDE)
    assert result["beds24_onsite_payment_revenue_included"] == 0
    assert result["beds24_onsite_payment_booking_count"] == 0


def test_compute_onsite_payment_prorated_to_stay_month(tmp_path):
    """現地決済の別建てchargeも宿泊月按分される。全4泊のうち対象月2泊なら半分。"""
    raw_path = tmp_path / "2026-06.json"
    raw_path.write_text(
        '[{"id": "1", "status": "confirmed", "price": 10000, "invoiceItems": '
        '[{"type": "charge", "description": "[ROOMNAME1]", "lineTotal": 10000},'
        ' {"type": "charge", "description": "現地決済追加", "lineTotal": 4000}]}]',
        encoding="utf-8")
    # 6/29 チェックイン, 7/3 チェックアウト = 4泊。6月分は2泊。
    bookings = [_booking("1", "2026-06-29", checkout="2026-07-03", raw_json_path=str(raw_path))]
    result = brl.compute("2026-06", bookings, EXCLUDE)
    assert result["beds24_onsite_payment_revenue_included"] == 2000  # 4000 * 2/4


def test_compute_onsite_payment_status_and_candidate_amount(tmp_path):
    raw_path = tmp_path / "2026-06.json"
    raw_path.write_text(
        '[{"id": "1", "status": "confirmed", "price": 13000, "invoiceItems": '
        '[{"type": "charge", "description": "[ROOMNAME1]", "lineTotal": 13000},'
        ' {"type": "payment", "description": "現地支払い", "lineTotal": 0}]}]',
        encoding="utf-8")
    bookings = [_booking("1", "2026-06-10", raw_json_path=str(raw_path))]
    result = brl.compute("2026-06", bookings, EXCLUDE)
    assert result["beds24_onsite_payment_revenue_included"] == 0
    assert result["beds24_onsite_payment_booking_count"] == 0
    assert result["beds24_onsite_payment_candidate_count"] == 1
    assert result["beds24_onsite_payment_logic_status"] == "payment_method_only_not_revenue"
    assert result["beds24_onsite_payment_logic_note"]


# ---------------- revenue_recon: net = gross + point + onsite - cancelled ----------------
def test_net_revenue_includes_onsite_payment_when_added(tmp_path):
    from yuge_finance.accounting import revenue_recon
    raw_path = tmp_path / "2026-06.json"
    raw_path.write_text(
        '[{"id": "1", "status": "confirmed", "price": 10000, "invoiceItems": '
        '[{"type": "charge", "description": "[ROOMNAME1]", "lineTotal": 10000},'
        ' {"type": "charge", "description": "現地決済追加", "lineTotal": 3000}]}]',
        encoding="utf-8")
    bookings = [_booking("1", "2026-06-10", gross=10000, raw_json_path=str(raw_path))]
    rec = revenue_recon.compute("2026-06", bookings, [], [], [])
    assert rec["beds24_onsite_payment_revenue_included"] == 3000
    assert rec["beds24_revenue_net_for_bi"] == (
        rec["beds24_revenue_gross_stay"] + rec["beds24_point_revenue_included"]
        + rec["beds24_onsite_payment_revenue_included"])


def test_net_revenue_unchanged_when_onsite_is_zero():
    """onsite=0の場合、既存値(gross+point)から変わらない。"""
    from yuge_finance.accounting import revenue_recon
    bookings = [_booking("1", "2026-06-10", gross=30000)]
    rec = revenue_recon.compute("2026-06", bookings, [], [], [])
    assert rec["beds24_onsite_payment_revenue_included"] == 0
    assert rec["beds24_revenue_net_for_bi"] == rec["beds24_revenue_gross_stay"] + rec["beds24_point_revenue_included"]


def test_snapshot_fields_include_onsite_payment():
    from yuge_finance.accounting import revenue_recon
    bookings = [_booking("1", "2026-06-10", gross=30000)]
    rec = revenue_recon.compute("2026-06", bookings, [], [], [])
    for field in ["beds24_onsite_payment_revenue_included", "beds24_onsite_payment_booking_count",
                 "beds24_onsite_payment_candidate_amount", "beds24_onsite_payment_candidate_count",
                 "beds24_onsite_payment_logic_status", "beds24_onsite_payment_logic_note"]:
        assert field in rec, f"missing field: {field}"
