"""ops.extract の清掃DTO専用追加項目テスト
(extract_guest_notice/extract_children_age_7plus/extract_onsite_payment/extract_cleaning_extra)。

2026-09実データ調査(property 330695、9か月657予約)の結論をそのまま固定する:
  - 子供の年齢データはBeds24に一切存在しない(常にNone/False)。
  - 現地決済の未収残高 = charge合計 - (onsite marker以外のpayment合計の絶対値)。
これらはユーザー要件により明示的に許可されたCleaning DTO専用の財務例外
(onsite_payment_required/onsite_payment_amount)であり、StaffBookingRecord
(Daily Ops/宿泊者名簿)には一切追加しない。
"""
from yuge_finance.ops.extract import (
    extract_cleaning_extra, extract_children_age_7plus,
    extract_guest_notice, extract_onsite_payment,
)


def _raw_with_invoice(*, price=0, invoice_items=None, **overrides):
    base = {"id": "9001", "price": price, "invoiceItems": invoice_items or []}
    base.update(overrides)
    return base


# ---------------- guest_notice ----------------

def test_guest_notice_reads_from_guestcomments_only():
    raw = {"guestComments": "静かな部屋を希望します"}
    assert extract_guest_notice(raw) == "静かな部屋を希望します"


def test_guest_notice_never_falls_back_to_internal_notes_fields():
    """notes/comments/groupNote/messageはstaff運用メモ用途であり、guest_noticeには
    絶対に流用しない(混在防止が本要件の核心)。"""
    raw = {"notes": "内部メモ", "comments": "内部コメント", "groupNote": "団体メモ",
           "message": "システムメッセージ"}
    assert extract_guest_notice(raw) is None


def test_guest_notice_sanitizes_null_like_and_blank_values():
    for literal in (None, "", "   ", "None", "null"):
        assert extract_guest_notice({"guestComments": literal}) is None


# ---------------- children_age_7plus (常に未実装/未取得) ----------------

def test_children_age_7plus_always_unavailable_per_2026_09_investigation():
    """実データにage fieldが一切存在しないため、常に(None, False)。推測実装しない。"""
    assert extract_children_age_7plus({}) == (None, False)
    assert extract_children_age_7plus({"numChild": 2, "infoItems": [{"code": "BOOKINGCOMFLAG"}]}) == (None, False)


# ---------------- onsite payment ----------------

def test_onsite_payment_no_marker_returns_not_required():
    raw = _raw_with_invoice(price=13000, invoice_items=[
        {"type": "charge", "description": "[ROOMNAME1]", "lineTotal": 13000},
    ])
    assert extract_onsite_payment(raw) == (False, None)


def test_onsite_payment_real_case_A_full_balance_owed():
    """2026-09実データ(booking_id=89547127匿名化): charge=13000, payment(現地支払いmarker)
    lineTotal=0のみ -> 未収残高13000。"""
    raw = _raw_with_invoice(price=13000, invoice_items=[
        {"type": "charge", "description": "[ROOMNAME1] [FIRSTNIGHT] - [LEAVINGDAY]", "lineTotal": 13000},
        {"type": "payment", "description": "現地支払い", "lineTotal": 0},
    ])
    assert extract_onsite_payment(raw) == (True, 13000)


def test_onsite_payment_real_case_B_full_balance_owed():
    """2026-09実データ(booking_id=89537029匿名化): charge=14500, payment marker lineTotal=0
    -> 未収残高14500。"""
    raw = _raw_with_invoice(price=14500, invoice_items=[
        {"type": "charge", "description": "[ROOMNAME1] [FIRSTNIGHT] - [LEAVINGDAY]", "lineTotal": 14500},
        {"type": "payment", "description": "現地支払い", "lineTotal": 0},
    ])
    assert extract_onsite_payment(raw) == (True, 14500)


def test_onsite_payment_signal_present_but_balance_zero_not_shown():
    """spec 21: onsite signalはあるがbalance=0の場合は表示しない(現地決済扱いにしない)。"""
    raw = _raw_with_invoice(price=18000, invoice_items=[
        {"type": "charge", "description": "[ROOMNAME1]", "lineTotal": 18000},
        {"type": "payment", "description": "現地支払い", "lineTotal": 0},
        {"type": "payment", "description": "クレジットカード事前決済", "lineTotal": 18000},
    ])
    assert extract_onsite_payment(raw) == (False, None)


def test_onsite_payment_partial_prior_payment_reduces_balance():
    """spec test D(符号は既存コードベースの確立済み規約=絶対値を踏襲。実データにこの
    パターンの実例は無かったため、既存extract_beds24_coupon_discount等と同じ規約を
    そのまま適用した合成ケース)。charge=20000, 既決済(絶対値)5000 -> 残高15000。"""
    raw = _raw_with_invoice(price=20000, invoice_items=[
        {"type": "charge", "description": "[ROOMNAME1]", "lineTotal": 20000},
        {"type": "payment", "description": "現地支払い", "lineTotal": 0},
        {"type": "payment", "description": "事前入金", "lineTotal": 5000},
    ])
    assert extract_onsite_payment(raw) == (True, 15000)


def test_onsite_payment_no_raw_returns_not_required():
    assert extract_onsite_payment({}) == (False, None)
    assert extract_onsite_payment(None) == (False, None)


# ---------------- extract_cleaning_extra (bundling) ----------------

def test_extract_cleaning_extra_bundles_all_five_fields():
    raw = {
        "guestComments": "到着が遅くなります",
        "price": 18000,
        "invoiceItems": [
            {"type": "charge", "description": "[ROOMNAME1]", "lineTotal": 18000},
            {"type": "payment", "description": "現地支払い", "lineTotal": 0},
        ],
    }
    extra = extract_cleaning_extra(raw)
    assert extra == {
        "guest_notice": "到着が遅くなります",
        "children_age_7plus_count": None,
        "children_age_data_available": False,
        "onsite_payment_required": True,
        "onsite_payment_amount": 18000,
    }
