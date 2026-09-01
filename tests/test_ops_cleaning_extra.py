"""ops.extract の清掃DTO専用追加項目テスト
(extract_guest_notice/extract_children_age_7plus/parse_booking_com_child_ages/
compute_bedding_guest_count/extract_invoice_balance/extract_amount_due_at_property/
extract_cleaning_extra)。

2026-09実データ調査(property 330695、5か月分)の結論をそのまま固定する:
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
  - 【重要・実データ未検証】Booking.comの子供年齢("1 child aged 10"等)について、
    3回の独立した実データ調査(9か月・664予約・Booking.com+子供ありの実予約10件
    全件)を行ったが、guestCommentsは全件空文字列(長さ0)であり、この
    patternはおろか"aged"という単語自体がguestComments/infoItems含む全field
    のどこにも一件も出現しなかった。ユーザーからは「実確認済み」との説明を
    受けたが、本リポジトリがアクセスできる実Beds24データではこの調査結果どおり
    一切確認できていない。以下のparser関連テストはユーザー提示の仕様どおりに
    構築した合成データでのみ検証しており、実データによる検証はできていない
    (extract.pyのparse_booking_com_child_ages docstring参照)。

これらはユーザー要件により明示的に許可されたCleaning DTO専用の財務例外
(payment_due_at_property/amount_due_at_property)であり、StaffBookingRecord
(Daily Ops/宿泊者名簿)には一切追加しない。
"""
from yuge_finance.ops.extract import (
    CHANNEL_COLLECT_INFO_CODES, compute_bedding_guest_count, extract_amount_due_at_property,
    extract_cleaning_extra, extract_children_age_7plus, extract_guest_notice,
    extract_invoice_balance, parse_booking_com_child_ages,
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

def test_children_age_7plus_unavailable_when_no_guestcomments_or_no_pattern_match():
    """現状の全実予約(guestComments空)を含む、patternが一切見つからない通常ケース。"""
    assert extract_children_age_7plus({}) == (None, False)
    assert extract_children_age_7plus({"numChild": 2, "infoItems": [{"code": "BOOKINGCOMFLAG"}]}) == (None, False)
    assert extract_children_age_7plus({"guestComments": ""}) == (None, False)
    assert extract_children_age_7plus({"guestComments": "We will arrive late tonight"}) == (None, False)


# ---------------- parse_booking_com_child_ages (合成データのみ・実データ未検証) ----------------
# 実Beds24データにこのpatternの出現例が一件も無いため(module docstring参照)、
# 以下は全てユーザー提示の仕様どおりに構築した合成テストである。

def test_parse_single_child_age_pattern():
    assert parse_booking_com_child_ages("1 child aged 10") == [10]


def test_parse_single_child_age_pattern_with_surrounding_text():
    """guestCommentsに他の文章が同居していても、pattern自体は正しく抽出できる。"""
    assert parse_booking_com_child_ages("1 child aged 6\nWe will arrive around 18:00") == [6]


def test_parse_multiple_children_age_pattern_comma_separated():
    assert parse_booking_com_child_ages("2 children aged 5, 10") == [5, 10]


def test_parse_no_match_returns_empty_list():
    assert parse_booking_com_child_ages("") == []
    assert parse_booking_com_child_ages(None) == []
    assert parse_booking_com_child_ages("We have a baby") == []


def test_extract_children_age_7plus_from_matched_pattern():
    """境界: age>=7のみ7歳以上カウントへ含める(age=7ちょうどは含む、age=6は含まない)。"""
    assert extract_children_age_7plus({"guestComments": "1 child aged 10"}) == (1, True)
    assert extract_children_age_7plus({"guestComments": "1 child aged 7"}) == (1, True)
    assert extract_children_age_7plus({"guestComments": "1 child aged 6"}) == (0, True)
    assert extract_children_age_7plus({"guestComments": "2 children aged 5, 10"}) == (1, True)


def test_extract_guest_notice_strips_matched_child_age_metadata_leaving_the_rest():
    """要件13: child-age metadataとguest_noticeが同居する場合、metadata部分だけ除去。"""
    raw = {"guestComments": "1 child aged 10\nWe will arrive around 18:00"}
    assert extract_guest_notice(raw) == "We will arrive around 18:00"


def test_extract_guest_notice_unaffected_when_no_child_age_pattern_present():
    raw = {"guestComments": "We will arrive around 18:00"}
    assert extract_guest_notice(raw) == "We will arrive around 18:00"


# ---------------- compute_bedding_guest_count ----------------

def test_bedding_count_booking_com_age_10_included():
    """要件22例1: adults=2, children=1, age=10 -> bedding_count=3."""
    assert compute_bedding_guest_count("Booking.com", 2, 1, 1, True) == 3


def test_bedding_count_booking_com_age_6_excluded():
    """要件22例2: adults=2, children=1, age=6 -> bedding_count=2(実人数は変えない)。"""
    assert compute_bedding_guest_count("Booking.com", 2, 1, 0, True) == 2


def test_bedding_count_booking_com_age_boundary_7_included():
    assert compute_bedding_guest_count("Booking.com", 2, 1, 1, True) == 3  # age=7は含む


def test_bedding_count_booking_com_two_children_ages_5_and_10():
    """要件22例4: adults=2, children=2, ages=[5,10] -> 7歳以上1名のみ加算 -> bedding_count=3."""
    assert compute_bedding_guest_count("Booking.com", 2, 2, 1, True) == 3


def test_bedding_count_booking_com_no_age_data_falls_back_to_full_children():
    """年齢が一切確認できない場合は、推測せず子供全員を布団人数に含める安全側fallback。"""
    assert compute_bedding_guest_count("Booking.com", 2, 1, None, False) == 3


def test_bedding_count_domestic_ota_always_includes_all_children_regardless_of_age():
    """要件23: 楽天/じゃらんは年齢を一切参照しない。"""
    assert compute_bedding_guest_count("楽天トラベル", 2, 2, None, False) == 4
    assert compute_bedding_guest_count("じゃらん", 1, 2, None, False) == 3
    # 万一children_age_data_availableがTrueでも(通常あり得ないが)国内OTAは無視する。
    assert compute_bedding_guest_count("楽天トラベル", 2, 2, 0, True) == 4


def test_bedding_count_direct_uses_existing_safe_fallback_unchanged():
    """要件8: Directは年齢推測ロジックを作らない。既存の安全なfallback(adults+children)
    を壊さない — 意図せず人数が減らないことを確認する。"""
    assert compute_bedding_guest_count("Direct", 2, 1, None, False) == 3
    assert compute_bedding_guest_count("未知OTA", 3, 0, None, False) == 3


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

def test_extract_cleaning_extra_bundles_all_six_fields_with_new_names():
    raw = _raw(price=18000, invoice_items=[
        {"type": "charge", "description": "[ROOMNAME1]", "lineTotal": 18000},
    ], guestComments="到着が遅くなります", numAdult=2, numChild=0)
    extra = extract_cleaning_extra(raw)
    assert extra == {
        "guest_notice": "到着が遅くなります",
        "children_age_7plus_count": None,
        "children_age_data_available": False,
        "bedding_guest_count": 2,
        "payment_due_at_property": True,
        "amount_due_at_property": 18000,
    }


def test_extract_cleaning_extra_end_to_end_booking_com_child_age():
    """raw dict(guestComments含む) -> bedding_guest_count/guest_noticeまで一気通貫。
    OTA判定は既存normalize_booking_source()経由(refererEditable)。合成データのみ
    (実データ未検証。module docstring参照)。"""
    raw = _raw(
        price=16000,
        invoice_items=[{"type": "charge", "description": "[ROOMNAME1]", "lineTotal": 16000}],
        refererEditable="Booking.com", numAdult=2, numChild=1,
        guestComments="1 child aged 10\nWe will arrive around 18:00",
    )
    extra = extract_cleaning_extra(raw)
    assert extra["bedding_guest_count"] == 3  # adults2 + confirmed-7plus 1
    assert extra["children_age_7plus_count"] == 1
    assert extra["children_age_data_available"] is True
    assert extra["guest_notice"] == "We will arrive around 18:00"  # metadata部分は除去済み


def test_extract_cleaning_extra_domestic_ota_bedding_count_ignores_age():
    raw = _raw(refererEditable="楽天トラベル", numAdult=2, numChild=2)
    extra = extract_cleaning_extra(raw)
    assert extra["bedding_guest_count"] == 4
