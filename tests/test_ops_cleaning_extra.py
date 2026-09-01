"""ops.extract の清掃DTO専用追加項目テスト
(extract_guest_notice/extract_children_age_7plus/extract_invoice_balance/
extract_amount_due_at_property/extract_cleaning_extra)。

2026-09実データ調査(property 330695、5か月分)の結論をそのまま固定する:
  - 子供の年齢データはBeds24に一切存在しない(常にNone/False)。
  - GET /bookings/invoicesは200を返すが対象データ0件、GET /bookingsのpayloadにも
    balance相当のtop-level fieldは存在しない -> Beds24公式のbooking単位
    Invoice Balanceは invoiceItems から自前計算する。
  - 符号は「payment側のlineTotalは実額入金時は既に負数」— 11件の実予約
    (現地支払いmarker/BankTransfer/coupon+point+事前払いの組み合わせ/無支払いの
    Booking.com)で invoice_balance = charge_sum + payment_sum(符号そのまま) が
    全件一致することを確認済み。旧実装(payment側をabs())はBankTransfer等の
    支払い済み予約で残高を二重加算する実害があったため撤回した。
  - channel collect(BOOKINGCOMBANKTRANS)は実データで154件確認済み。
    BOOKINGCOMVIRTCARD/EXPEDIACOLLECT/AGODACOLLECT/VIRTUALCARDは防御的に実装
    したが実データに0件。HOTELCOLLECTはOTA collectではないため除外対象に含めない。

これらはユーザー要件により明示的に許可されたCleaning DTO専用の財務例外
(payment_due_at_property/amount_due_at_property)であり、StaffBookingRecord
(Daily Ops/宿泊者名簿)には一切追加しない。
"""
from yuge_finance.ops.extract import (
    CHANNEL_COLLECT_INFO_CODES, extract_amount_due_at_property, extract_cleaning_extra,
    extract_children_age_7plus, extract_guest_notice, extract_invoice_balance,
)


def _raw(*, price=0, invoice_items=None, info_items=None, **overrides):
    base = {"id": "9001", "price": price, "invoiceItems": invoice_items or [],
            "infoItems": info_items or []}
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


# ---------------- extract_invoice_balance (Beds24公式 Invoice Balance 相当) ----------------

def test_invoice_balance_no_payments_equals_full_charge():
    """2026-09実データ(booking_id=88328714匿名化、他multiple件): 通常のBooking.com
    予約でpayment行が一切無ければ、charge全額がそのまま未収残高。"""
    raw = _raw(price=7650, invoice_items=[
        {"type": "charge", "description": "[ROOMNAME1] [FIRSTNIGHT] - [LEAVINGDAY]", "lineTotal": 7650},
    ])
    assert extract_invoice_balance(raw) == 7650


def test_invoice_balance_onsite_marker_zero_lineTotal_leaves_full_balance():
    """2026-09実データ(booking_id=89547127匿名化): charge=13000, 「現地支払い」
    payment行のlineTotal=0 -> 残高13000。"""
    raw = _raw(price=13000, invoice_items=[
        {"type": "charge", "description": "[ROOMNAME1] [FIRSTNIGHT] - [LEAVINGDAY]", "lineTotal": 13000},
        {"type": "payment", "description": "現地支払い", "lineTotal": 0},
    ])
    assert extract_invoice_balance(raw) == 13000


def test_invoice_balance_bank_transfer_fully_paid_is_zero_not_double_counted():
    """2026-09実データ(booking_id=88817891匿名化): charge=39561, BankTransfer
    payment lineTotal=-39561(符号そのまま、実額入金時は負数) -> 残高0。
    旧実装(abs()で加算)はこれを誤って79122(2倍加算)にしていた実害のあったケース。"""
    raw = _raw(price=39561, invoice_items=[
        {"type": "charge", "description": "[ROOMNAME1] [FIRSTNIGHT] - [LEAVINGDAY]", "lineTotal": 39561},
        {"type": "payment", "description": "BankTransfer", "lineTotal": -39561},
    ])
    assert extract_invoice_balance(raw) == 0


def test_invoice_balance_multiple_payment_channels_combine_to_zero():
    """2026-09実データ(booking_id=88956281匿名化): charge=22000、
    point(-700)+coupon(-3300)+事前払い(-18000)の合計が丁度charge全額を相殺 -> 残高0。"""
    raw = _raw(price=22000, invoice_items=[
        {"type": "charge", "description": "[ROOMNAME1]", "lineTotal": 22000},
        {"type": "payment", "description": "point", "lineTotal": -700},
        {"type": "payment", "description": "coupon", "lineTotal": -3300},
        {"type": "payment", "description": "事前払い", "lineTotal": -18000},
    ])
    assert extract_invoice_balance(raw) == 0


def test_invoice_balance_partial_prior_payment_leaves_remainder():
    """spec test D 相当: charge=20000, 既決済(符号そのまま、実データ規約を適用)-5000
    -> 残高15000。"""
    raw = _raw(price=20000, invoice_items=[
        {"type": "charge", "description": "[ROOMNAME1]", "lineTotal": 20000},
        {"type": "payment", "description": "事前入金", "lineTotal": -5000},
    ])
    assert extract_invoice_balance(raw) == 15000


def test_invoice_balance_no_raw_or_no_items_is_zero():
    assert extract_invoice_balance({}) == 0
    assert extract_invoice_balance(None) == 0


# ---------------- CHANNEL_COLLECT_INFO_CODES ----------------

def test_channel_collect_codes_include_verified_and_defensive_official_codes():
    assert "BOOKINGCOMBANKTRANS" in CHANNEL_COLLECT_INFO_CODES  # 実データ154件で確認済み
    for defensive in ("BOOKINGCOMVIRTCARD", "EXPEDIACOLLECT", "AGODACOLLECT", "VIRTUALCARD"):
        assert defensive in CHANNEL_COLLECT_INFO_CODES


def test_hotelcollect_is_never_a_channel_collect_exclusion_signal():
    """要件A-4: HOTELCOLLECTはExpediaのhotel collect(施設側回収)でありOTA collectでは
    ないため、除外signalに含めてはならない。"""
    assert "HOTELCOLLECT" not in CHANNEL_COLLECT_INFO_CODES


# ---------------- extract_amount_due_at_property (最終判定) ----------------

def test_amount_due_1_invoice_balance_positive_no_channel_collect_shows_amount():
    raw = _raw(price=18000, invoice_items=[
        {"type": "charge", "description": "[ROOMNAME1]", "lineTotal": 18000},
    ], info_items=[])
    assert extract_amount_due_at_property(raw) == (True, 18000)


def test_amount_due_2_invoice_balance_zero_is_blank():
    raw = _raw(price=18000, invoice_items=[
        {"type": "charge", "description": "[ROOMNAME1]", "lineTotal": 18000},
        {"type": "payment", "description": "BankTransfer", "lineTotal": -18000},
    ])
    assert extract_amount_due_at_property(raw) == (False, None)


def test_amount_due_3_bookingcomvirtcard_excludes_even_with_positive_balance():
    raw = _raw(price=18000, invoice_items=[
        {"type": "charge", "description": "[ROOMNAME1]", "lineTotal": 18000},
    ], info_items=[{"code": "BOOKINGCOMVIRTCARD"}])
    assert extract_amount_due_at_property(raw) == (False, None)


def test_amount_due_4_bookingcombanktrans_excludes_even_with_positive_balance():
    """実データで実際に確認済みの除外パターン(現実にはbalanceが0になるケースが
    多いが、万一0でなくてもchannel collectであれば除外する防御を単独で検証)。"""
    raw = _raw(price=18000, invoice_items=[
        {"type": "charge", "description": "[ROOMNAME1]", "lineTotal": 18000},
        {"type": "payment", "description": "BankTransfer", "lineTotal": -10000},  # 部分入金想定
    ], info_items=[{"code": "BOOKINGCOMBANKTRANS"}])
    # balanceは8000 > 0だが、channel collectのため現地決済扱いにしない。
    assert extract_amount_due_at_property(raw) == (False, None)


def test_amount_due_5_hotelcollect_does_not_exclude_shows_amount():
    """要件テスト5: HOTELCOLLECTは除外signalではないため、balance>0ならそのまま表示。"""
    raw = _raw(price=7800, invoice_items=[
        {"type": "charge", "description": "[ROOMNAME1]", "lineTotal": 7800},
    ], info_items=[{"code": "HOTELCOLLECT"}])
    assert extract_amount_due_at_property(raw) == (True, 7800)


def test_amount_due_6_partial_prepayment_shows_remainder_only():
    """要件テスト6: charges=20000, payments=5000(符号そのまま-5000) ->
    Beds24 invoiceBalance相当=15000、現地 ¥15,000として表示。"""
    raw = _raw(price=20000, invoice_items=[
        {"type": "charge", "description": "[ROOMNAME1]", "lineTotal": 20000},
        {"type": "payment", "description": "事前入金", "lineTotal": -5000},
    ])
    assert extract_amount_due_at_property(raw) == (True, 15000)


def test_amount_due_negative_balance_is_blank():
    """chargeより多く支払われている(返金待ち等)場合も現地決済としては表示しない。"""
    raw = _raw(price=10000, invoice_items=[
        {"type": "charge", "description": "[ROOMNAME1]", "lineTotal": 10000},
        {"type": "payment", "description": "事前入金", "lineTotal": -15000},
    ])
    assert extract_amount_due_at_property(raw) == (False, None)


def test_amount_due_no_raw_returns_not_required():
    assert extract_amount_due_at_property({}) == (False, None)
    assert extract_amount_due_at_property(None) == (False, None)


def test_amount_due_no_longer_depends_on_onsite_payment_marker():
    """根本修正の核心: 「現地支払い」等のmarkerが一切無い実予約でも、
    invoice_balance>0かつchannel collectでなければ現地決済として表示する
    (旧実装はmarkerが無い予約を誤って除外していた — 要件A-1/A-9)。"""
    raw = _raw(price=16327, invoice_items=[
        {"type": "charge", "description": "[ROOMNAME1]", "lineTotal": 16327},
    ], info_items=[{"code": "BOOKINGCOMFLAG"}])  # markerなし、フラグのみ
    assert extract_amount_due_at_property(raw) == (True, 16327)


# ---------------- extract_cleaning_extra (bundling) ----------------

def test_extract_cleaning_extra_bundles_all_five_fields_with_new_names():
    raw = _raw(price=18000, invoice_items=[
        {"type": "charge", "description": "[ROOMNAME1]", "lineTotal": 18000},
    ], guestComments="到着が遅くなります")
    extra = extract_cleaning_extra(raw)
    assert extra == {
        "guest_notice": "到着が遅くなります",
        "children_age_7plus_count": None,
        "children_age_data_available": False,
        "payment_due_at_property": True,
        "amount_due_at_property": 18000,
    }
