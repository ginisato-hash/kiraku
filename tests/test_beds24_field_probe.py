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


# ---------------- 「本日の新規予約」判定用field（Phase 0） ----------------
def test_probe_finds_booking_created_at_candidate_from_real_payload():
    """推測ではなく実payloadのキー一覧からcandidateを出す。実データではbookingTimeが該当する。"""
    probe = beds24_field_probe.build_probe()
    assert "booking_created_at" in probe["candidate_fields"]
    assert "bookingTime" in probe["candidate_fields"]["booking_created_at"]
    assert probe["selected_fields"]["booking_created_at"] == "bookingTime"


def test_probe_finds_booking_modified_at_and_status_candidates():
    probe = beds24_field_probe.build_probe()
    assert "modifiedTime" in probe["candidate_fields"]["booking_modified_at"]
    assert probe["selected_fields"]["booking_modified_at"] == "modifiedTime"
    assert "status" in probe["candidate_fields"]["booking_status"]


def test_probe_returns_no_created_at_candidates_when_key_absent():
    """実payloadに候補keyが無い場合はcandidateを空リストで返し、決め打ちしない。"""
    assert beds24_field_probe._find_candidate_keys({"foo", "bar"},
                                                    beds24_field_probe.CREATED_AT_TOKENS) == []


# ---------------- 現地決済/現地払い調査 ----------------
def test_probe_has_onsite_payment_candidate_fields():
    probe = beds24_field_probe.build_probe()
    for key in ["onsite_payment_amount", "onsite_payment_invoice_items",
               "payment_method", "payment_status", "outstanding_balance", "paid_amount"]:
        assert key in probe["candidate_fields"], f"missing candidate_fields key: {key}"


def test_probe_has_onsite_payment_selected_fields():
    probe = beds24_field_probe.build_probe()
    for key in ["onsite_payment_amount", "payment_method", "payment_status", "outstanding_balance"]:
        assert key in probe["selected_fields"], f"missing selected_fields key: {key}"


def test_probe_classification_includes_onsite_payment():
    probe = beds24_field_probe.build_probe()
    assert "onsite_payment" in probe["classification"]
    assert probe["classification"]["onsite_payment"] in (
        "already_included_in_price", "separate_revenue_addition",
        "payment_method_only_not_revenue", "candidate_not_selected", "field_missing")


def test_probe_onsite_payment_token_hits_reported():
    probe = beds24_field_probe.build_probe()
    assert "onsite_payment_candidate_token_hits_in_descriptions" in probe
