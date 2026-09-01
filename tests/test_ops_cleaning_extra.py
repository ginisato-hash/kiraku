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
  - Booking.comの子供年齢は Beds24 v2 の `comments` に入る(2026-09-02確定)。
    anchor booking 91673623 を直接GETし、Beds24 UI「ゲストからのコメント」の
    "1 child aged 10" が `comments`(長さ142)に実在することを確認。include
    parameterは不要、booking group/masterでもない(masterId=None)。通常の15分
    refresh(fetch_raw)が返すrecordにも同じ`comments`が含まれる。
    前実装が読んでいた `guestComments` はpayloadに存在しないキーであり
    (実recordの全71キーに無い)、常にNoneだった。「guestCommentsは全件空」と
    いう前回の結論は存在しないキーの長さを測っていたもので、撤回済み。
  - 実在する年齢表記は "N child aged N" の1形のみ(723予約・`comments`非空354件を
    走査、digitsマスク後のtemplateは '# child aged #' の1種類)。子供2名の予約
    (92008803)は "1 child aged 6" と "1 child aged 8" の2行に分かれて出現する。
    「N children aged A, B」というカンマ区切り形式は実データに存在しないため、
    その前提で書かれていた旧テスト/旧実装(単一match)は撤回した。
  - 2026-09-03 再監査(724予約/`comments`非空355件): 当時のfilterを通り抜けていた
    label形式の行は "Guest name: <氏名>" だけ(59予約、全てBooking.com、variantは
    1形のみ)。本番帳票の備考欄に「客: Guest name: U…」と出ていた原因。あわせて
    OTA収集の "電話番号:" 行(3予約)もfield-label型metadataとして除外対象に加えた。
  - Booking.comの到着予定時間帯は "Approximate time of arrival: between HH:MM and
    HH:MM" の1形のみ(46予約、全てBooking.com、windowは14:00〜22:00の毎正時8種)。
    46予約すべてBeds24のarrivalTimeが空 = fallbackが必要な母集団、明示値との
    衝突は実データ0件。明示arrivalTimeは345/724予約で非空("##:##"/"##：##")。
  - `comments`にはOTA自動生成の定型行(PRE-PAID/BOOKING NOTE/部屋設定コード/
    booked rate/管理画面リンク等)がゲスト入力と混在する。以下のsystem行テストは
    「2予約以上にbyte一致で出現した実template」だけを根拠にしている。

これらはユーザー要件により明示的に許可されたCleaning DTO専用の財務例外
(payment_due_at_property/amount_due_at_property)であり、StaffBookingRecord
(Daily Ops/宿泊者名簿)には一切追加しない。
"""
from yuge_finance.ops.extract import (
    CHANNEL_COLLECT_INFO_CODES, compute_bedding_guest_count, extract_amount_due_at_property,
    extract_booking_comment_arrival_window, extract_child_age_info, extract_cleaning_extra,
    extract_children_age_7plus,
    extract_guest_notice, extract_invoice_balance, parse_booking_com_child_ages,
)


def _raw(*, price=0, invoice_items=None, info_items=None, **overrides):
    base = {"id": "9001", "price": price, "invoiceItems": invoice_items or [],
            "infoItems": info_items or []}
    base.update(overrides)
    return base


# ---------------- guest_notice ----------------

def test_guest_notice_reads_from_comments_field():
    """実フィールド名は`comments`(Beds24 v2)。`guestComments`は存在しないキー。"""
    assert extract_guest_notice({"comments": "静かな部屋を希望します"}) == "静かな部屋を希望します"
    assert extract_guest_notice({"guestComments": "静かな部屋を希望します"}) is None


def test_guest_notice_never_falls_back_to_internal_notes_fields():
    """notes/groupNote/messageはstaff運用メモ用途であり、guest_noticeには絶対に
    流用しない(混在防止が本要件の核心)。"""
    raw = {"notes": "内部メモ", "groupNote": "団体メモ", "message": "システムメッセージ"}
    assert extract_guest_notice(raw) is None


def test_guest_notice_sanitizes_null_like_and_blank_values():
    for literal in (None, "", "   ", "None", "null"):
        assert extract_guest_notice({"comments": literal}) is None


# ---------------- children_age_7plus (常に未実装/未取得) ----------------

def test_children_age_7plus_unavailable_when_no_comments_or_no_pattern_match():
    """`comments`が空/年齢表記が無い場合(実データでもBooking.com子供あり10件のうち
    4件は年齢表記が無い)は(None, False)で既存の安全なfallbackへ流す。"""
    assert extract_children_age_7plus({}) == (None, False)
    assert extract_children_age_7plus({"numChild": 2, "infoItems": [{"code": "BOOKINGCOMFLAG"}]}) == (None, False)
    assert extract_children_age_7plus({"comments": ""}) == (None, False)
    assert extract_children_age_7plus({"comments": "We will arrive late tonight"}) == (None, False)


# ---------------- parse_booking_com_child_ages (実データ由来のpatternのみ) ----------------
# 実データに存在する形は "N child aged N" だけ(module docstring参照)。推測patternは
# 追加しない。

def test_parse_single_child_age_pattern_real_anchor_booking():
    """実データ booking 91673623 の`comments` 1行目。"""
    assert parse_booking_com_child_ages("1 child aged 10") == [10]


def test_parse_child_age_with_surrounding_text():
    assert parse_booking_com_child_ages("1 child aged 6\nWe will arrive around 18:00") == [6]


def test_parse_multiple_children_appear_as_separate_lines_real_booking_92008803():
    """実データ booking 92008803(numChild=2)は1行1名で2行に分かれて出現する。
    旧実装は単一matchしか見ておらず2人目の年齢を取りこぼしていた。"""
    assert parse_booking_com_child_ages("1 child aged 6\n1 child aged 8") == [6, 8]


def test_parse_child_age_lines_inside_full_real_comments_block():
    comments = ("1 child aged 10\n\n** THIS RESERVATION HAS BEEN PRE-PAID **\n"
                "BOOKING NOTE : Payment charge is JPY 563.753\nNonSmoke\nNonSmoke\n"
                "Non Smoking Requested\n")
    assert parse_booking_com_child_ages(comments) == [10]


def test_parse_no_match_returns_empty_list():
    assert parse_booking_com_child_ages("") == []
    assert parse_booking_com_child_ages(None) == []
    assert parse_booking_com_child_ages("We have a baby") == []


def test_extract_children_age_7plus_from_matched_pattern():
    """境界: age>=7のみ7歳以上カウントへ含める(age=7ちょうどは含む、age=6は含まない)。"""
    assert extract_children_age_7plus({"comments": "1 child aged 10"}) == (1, True)
    assert extract_children_age_7plus({"comments": "1 child aged 7"}) == (1, True)
    assert extract_children_age_7plus({"comments": "1 child aged 6"}) == (0, True)
    assert extract_children_age_7plus({"comments": "1 child aged 6\n1 child aged 8"}) == (1, True)


def test_extract_child_age_info_reports_known_count_and_7plus():
    assert extract_child_age_info({"comments": "1 child aged 6\n1 child aged 8"}) == (2, 1)
    assert extract_child_age_info({"comments": "1 child aged 10"}) == (1, 1)
    assert extract_child_age_info({"comments": ""}) == (0, 0)


def test_extract_guest_notice_strips_matched_child_age_metadata_leaving_the_rest():
    """要件13: child-age metadataとguest_noticeが同居する場合、metadata部分だけ除去。"""
    raw = {"comments": "1 child aged 10\nWe will arrive around 18:00"}
    assert extract_guest_notice(raw) == "We will arrive around 18:00"


def test_extract_guest_notice_unaffected_when_no_child_age_pattern_present():
    raw = {"comments": "We will arrive around 18:00"}
    assert extract_guest_notice(raw) == "We will arrive around 18:00"


# ---------------- guest_notice: OTA自動生成行の分離(実データtemplateのみ) ----------------

def test_guest_notice_is_none_for_real_anchor_booking_91673623():
    """実データ booking 91673623 の`comments`全文(7行)は child-age metadata と
    Booking.com自動生成行だけで構成される -> 「客:」行は一切出さない。"""
    comments = ("1 child aged 10\n\n** THIS RESERVATION HAS BEEN PRE-PAID **\n"
                "BOOKING NOTE : Payment charge is JPY 563.753\nNonSmoke\nNonSmoke\n"
                "Non Smoking Requested\n")
    assert extract_guest_notice({"comments": comments}) is None


def test_guest_notice_drops_each_real_system_line_template():
    """実データで2予約以上にbyte一致で出現したOTA自動生成行(=機械生成の証拠)。"""
    for line in (
        "** THIS RESERVATION HAS BEEN PRE-PAID **",
        "BOOKING NOTE : Payment charge is JPY 563.753",
        "Non Smoking Requested",
        "NonSmoke",
        "LargeBed, NonSmoke",
        "NonSmoke, TwinBeds",
        "Approximate time of arrival: between 15:00 and 16:00",
        "booked rate: Non-refundable Rate (12345)",
        "booked rate: Multiple nights Discount (12345)",
        "Reservation has a cancellation grace period. Do not charge if cancelled before 2026-8-1 12:00:00",
        "BED PREFERENCE:Standard Double or Twin Room: 2 futon mats",
        "こちらは「スマート・フレックス予約」の対象予約です。",
        "アップグレード後のポリシー：チェックインの3日前までキャンセル無料",
        "詳細についてはhttps://admin.booking.com/hotel/hoteladmin/extranet_ng/manage/booking.html?hotel_id=1&res_id=2をご覧ください",
        "company: カヤバ株式会社 vat",
    ):
        assert extract_guest_notice({"comments": line}) is None, line


def test_guest_notice_keeps_guest_authored_text_that_ota_merely_relays():
    """OTA文面でもゲストの希望を伝える文はスタッフに必要なので残す。"""
    for line in (
        "This guest would like the rooms in this booking to be close together if possible.",
        "You have a booker that would like free parking. (based on availability)",
    ):
        assert extract_guest_notice({"comments": line}) == line


def test_guest_notice_keeps_text_after_additional_notes_marker():
    """実データ形: 部屋設定コード + AdditionalNotes: + ゲスト本文。"""
    raw = {"comments": "NonSmoke, QuietRoom AdditionalNotes: please do not assign me a corner room"}
    assert extract_guest_notice(raw) == "please do not assign me a corner room"


def test_guest_notice_strips_domestic_ota_room_charge_prefix_only():
    """実データ形: 楽天/じゃらんの"[室料:12000円＝12000円]"prefix。prefixだけ外し本文は残す。"""
    raw = {"comments": "[室料:12000円＝12000円]禁煙かつ静かな部屋希望します。"}
    assert extract_guest_notice(raw) == "禁煙かつ静かな部屋希望します。"
    assert extract_guest_notice({"comments": "[室料:12000円＝12000円]車"}) == "車"


# ---------------- compute_bedding_guest_count ----------------

def test_bedding_count_booking_com_age_10_included():
    """要件22例1: adults=2, children=1, age=10 -> bedding_count=3."""
    assert compute_bedding_guest_count("Booking.com", 2, 1, 1, True) == 3


def test_bedding_count_booking_com_age_6_excluded():
    """要件22例2: adults=2, children=1, age=6 -> bedding_count=2(実人数は変えない)。"""
    assert compute_bedding_guest_count("Booking.com", 2, 1, 0, True) == 2


def test_bedding_count_booking_com_age_boundary_7_included():
    assert compute_bedding_guest_count("Booking.com", 2, 1, 1, True) == 3  # age=7は含む


def test_bedding_count_booking_com_two_children_real_booking_92008803():
    """実データ booking 92008803: adults=1, children=2, ages=[6,8] -> 7歳以上1名のみ
    加算 -> bedding_count=2(6歳は布団人数から除外、実人数は変えない)。"""
    assert compute_bedding_guest_count("Booking.com", 1, 2, 1, True, 2) == 2


def test_bedding_count_booking_com_partial_age_data_never_guesses():
    """要件16: numChild=2で年齢が1名分しか取れない場合、未知の子供の年齢を推測せず
    そのまま布団人数へ含める(安全側)。7歳以上と分かっている子は通常どおり加算。"""
    # ages=[10] のみ判明 -> adults2 + 7歳以上1 + 年齢不明1 = 4
    assert compute_bedding_guest_count("Booking.com", 2, 2, 1, True, 1) == 4
    # ages=[3] のみ判明 -> adults2 + 7歳以上0 + 年齢不明1 = 3
    assert compute_bedding_guest_count("Booking.com", 2, 2, 0, True, 1) == 3


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


# ---------------- guest_notice: Guest name / 電話番号 のfield-label型metadata ----------------

def test_guest_notice_drops_booking_com_guest_name_metadata_line():
    """実データ59予約(全てBooking.com)。本番帳票に「客: Guest name: U…」として
    出ていた行。氏名は清掃・客室準備の要望ではないため表示しない。"""
    assert extract_guest_notice({"comments": "Guest name: TEST USER"}) is None


def test_guest_notice_keeps_real_request_when_guest_name_metadata_precedes_it():
    raw = {"comments": "Guest name: TEST USER\nPlease prepare two pillows"}
    assert extract_guest_notice(raw) == "Please prepare two pillows"


def test_guest_notice_guest_name_match_is_prefix_exact_not_word_contains():
    """「name」という単語を含むだけのゲスト文章は絶対に落とさない(過剰削除防止)。"""
    for line in (
        "My name is Tanaka and I would like a quiet room",
        "Please write our names on the welcome card",
        "guest names: we are 2 adults",   # "guest name:" ではない(sが入る)
    ):
        assert extract_guest_notice({"comments": line}) == line, line


def test_guest_notice_drops_ota_collected_phone_number_label_line():
    """実データ3予約。OTAが収集した連絡先fieldで、要望ではない(半角/全角コロン両方)。"""
    assert extract_guest_notice({"comments": "電話番号：090-0000-0000"}) is None
    assert extract_guest_notice({"comments": "電話番号:090-0000-0000"}) is None


# ---------------- Booking.com 到着予定時間帯 (comments -> 到着列fallback) ----------------

def test_arrival_window_parses_the_only_real_form():
    """実データに存在する唯一の形式(46予約)。返り値は "HH:MM-HH:MM"。"""
    raw = {"comments": "Approximate time of arrival: between 17:00 and 18:00"}
    assert extract_booking_comment_arrival_window(raw) == "17:00-18:00"


def test_arrival_window_parses_every_real_window_observed():
    """実データで観測された8つのwindowすべて(14:00〜22:00の毎正時)。"""
    for start, end in [("14:00", "15:00"), ("15:00", "16:00"), ("16:00", "17:00"),
                       ("17:00", "18:00"), ("18:00", "19:00"), ("19:00", "20:00"),
                       ("20:00", "21:00"), ("21:00", "22:00")]:
        raw = {"comments": f"Approximate time of arrival: between {start} and {end}"}
        assert extract_booking_comment_arrival_window(raw) == f"{start}-{end}"


def test_arrival_window_found_inside_a_full_mixed_comments_block():
    raw = {"comments": ("1 child aged 10\n"
                        "Approximate time of arrival: between 17:00 and 18:00\n"
                        "Guest name: TEST USER\n"
                        "Please prepare two pillows")}
    assert extract_booking_comment_arrival_window(raw) == "17:00-18:00"


def test_arrival_window_returns_none_for_absent_or_unknown_forms():
    """実データに無い書式は解釈しない(推測しない)。"""
    assert extract_booking_comment_arrival_window({}) is None
    assert extract_booking_comment_arrival_window({"comments": ""}) is None
    assert extract_booking_comment_arrival_window(
        {"comments": "We will arrive around 18:00"}) is None
    assert extract_booking_comment_arrival_window(
        {"comments": "Approximate time of arrival: 17:00"}) is None


def test_arrival_window_line_is_never_shown_as_guest_notice():
    raw = {"comments": "Approximate time of arrival: between 17:00 and 18:00"}
    assert extract_guest_notice(raw) is None


def test_extract_cleaning_extra_requirement_12_full_mixed_comments():
    """要件12: child age / arrival window / Guest name / 実要望 が同居するcomments。"""
    raw = _raw(refererEditable="Booking.com", numAdult=2, numChild=1,
               comments=("1 child aged 10\n"
                         "Approximate time of arrival: between 17:00 and 18:00\n"
                         "Guest name: TEST USER\n"
                         "Please prepare two pillows"))
    extra = extract_cleaning_extra(raw)
    assert extra["bedding_guest_count"] == 3
    assert extra["arrival_time_fallback"] == "17:00-18:00"
    assert extra["guest_notice"] == "Please prepare two pillows"


def test_extract_cleaning_extra_arrival_fallback_is_none_without_the_metadata():
    extra = extract_cleaning_extra(_raw(refererEditable="Booking.com", comments="静かな部屋希望"))
    assert extra["arrival_time_fallback"] is None
    assert extra["guest_notice"] == "静かな部屋希望"


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

def test_extract_cleaning_extra_bundles_all_fields_with_new_names():
    raw = _raw(price=18000, invoice_items=[
        {"type": "charge", "description": "[ROOMNAME1]", "lineTotal": 18000},
    ], comments="到着が遅くなります", numAdult=2, numChild=0)
    extra = extract_cleaning_extra(raw)
    assert extra == {
        "guest_notice": "到着が遅くなります",
        "arrival_time_fallback": None,
        "children_age_7plus_count": None,
        "children_age_known_count": 0,
        "children_age_data_available": False,
        "bedding_guest_count": 2,
        "payment_due_at_property": True,
        "amount_due_at_property": 18000,
    }


def test_extract_cleaning_extra_end_to_end_real_anchor_booking_91673623():
    """実データ booking 91673623(Beds24 UI「ゲストからのコメント」= "1 child aged 10")
    のraw dict -> bedding_guest_count=3 / guest_notice=Noneまで一気通貫。
    OTA判定は既存normalize_booking_source()経由(refererEditable)。"""
    raw = _raw(
        price=16000,
        invoice_items=[{"type": "charge", "description": "[ROOMNAME1]", "lineTotal": 16000}],
        refererEditable="Booking.com", apiSource="Booking.com",
        numAdult=2, numChild=1,
        comments=("1 child aged 10\n\n** THIS RESERVATION HAS BEEN PRE-PAID **\n"
                  "BOOKING NOTE : Payment charge is JPY 563.753\nNonSmoke\nNonSmoke\n"
                  "Non Smoking Requested\n"),
    )
    extra = extract_cleaning_extra(raw)
    assert extra["bedding_guest_count"] == 3  # adults2 + age10(7歳以上)1
    assert extra["children_age_7plus_count"] == 1
    assert extra["children_age_known_count"] == 1
    assert extra["children_age_data_available"] is True
    assert extra["guest_notice"] is None  # 全行がmetadata/OTA自動生成行


def test_extract_cleaning_extra_end_to_end_child_age_with_guest_text():
    raw = _raw(
        refererEditable="Booking.com", numAdult=2, numChild=1,
        comments="1 child aged 10\nWe will arrive around 18:00",
    )
    extra = extract_cleaning_extra(raw)
    assert extra["bedding_guest_count"] == 3
    assert extra["guest_notice"] == "We will arrive around 18:00"


def test_extract_cleaning_extra_domestic_ota_bedding_count_ignores_age():
    raw = _raw(refererEditable="楽天トラベル", numAdult=2, numChild=2)
    extra = extract_cleaning_extra(raw)
    assert extra["bedding_guest_count"] == 4
