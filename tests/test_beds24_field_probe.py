"""Beds24 raw payload field probe（point候補・coupon分類）。"""
from yuge_finance.reports import beds24_field_probe


def test_probe_candidate_fields_has_point_amount():
    probe = beds24_field_probe.build_probe()
    assert "point_amount" in probe["candidate_fields"]
    assert "point_invoice_items" in probe["candidate_fields"]
    assert "coupon_discount_amount" in probe["candidate_fields"]
    assert "coupon_invoice_items" in probe["candidate_fields"]


def test_probe_classification_matches_business_rule():
    probe = beds24_field_probe.build_probe()
    assert probe["classification"]["coupon"] == "direct_discount_not_revenue"
    assert probe["classification"]["point"] == "revenue_addition_candidate"


def test_probe_selected_fields_present():
    probe = beds24_field_probe.build_probe()
    for key in ["cancel_status", "point_amount", "point_invoice_items", "coupon_discount_amount"]:
        assert key in probe["selected_fields"]


def test_probe_does_not_leak_pii():
    probe = beds24_field_probe.build_probe()
    text = str(probe["sample_bookings_pii_redacted"])
    for pii_key in ["firstName", "lastName", "email", "phone", "address", "notes", "comments"]:
        assert f"'{pii_key}'" not in text
