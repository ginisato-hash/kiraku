"""ops.cleaning_classifier.classify_cleaning_for_date() の状態分類テスト。

2026-08-31: 物理客室番号(KIRAKU_ROOM_ORDERの18室)ベースの新classifierへ全面更新。
「予約のある客室だけ」ではなく「実在する客室すべて(18室)」が対象日1件ごとに
必ず1行ずつ出る。room_numberがKIRAKU_ROOM_ORDERで解決できない予約は18室表に
混ぜずUNASSIGNEDとして別途保持する。
"""
from yuge_finance.ops.cleaning_classifier import classify_cleaning_for_date, compute_night_progress
from yuge_finance.ops.room_master import KIRAKU_ROOM_ORDER
from yuge_finance.ops.schema import StaffBookingRecord


def _sb(bid, room_number, checkin, checkout, status="confirmed", adults=2, children=0,
       ota_name="じゃらん", arrival_time=None, guest_name="宿泊者"):
    return StaffBookingRecord(
        booking_id=bid, room_number=room_number, guest_name=guest_name, ota_name=ota_name,
        checkin_date=checkin, checkout_date=checkout, status=status,
        adults=adults, children=children, total_guests=adults + children,
        arrival_time=arrival_time,
    )


def _row_for(room_number, rows):
    matches = [r for r in rows if r.room_number == room_number]
    assert len(matches) == 1, f"expected exactly 1 row for room {room_number}, got {len(matches)}"
    return matches[0]


# ---------------- room master: always 18 rows, in KIRAKU_ROOM_ORDER order ----------------
def test_always_emits_exactly_18_room_rows_in_canonical_order():
    rows = classify_cleaning_for_date([], "2026-09-03")
    room_number_rows = [r for r in rows if r.status != "UNASSIGNED"]
    assert len(room_number_rows) == 18
    assert [r.room_number for r in room_number_rows] == KIRAKU_ROOM_ORDER


def test_room_with_no_bookings_is_vacant():
    rows = classify_cleaning_for_date([_sb("1", "401", "2026-09-10", "2026-09-12")], "2026-09-03")
    assert _row_for("402", rows).status == "VACANT"


# ---------------- checkout-only ----------------
def test_checkout_only_produces_checkout_state():
    rows = classify_cleaning_for_date([_sb("1", "401", "2026-09-01", "2026-09-03")], "2026-09-03")
    row = _row_for("401", rows)
    assert row.status == "CHECKOUT"
    assert row.departing_guest.booking_id == "1"
    assert row.arriving_guest is None
    assert row.staying_guest is None


# ---------------- checkin-only ----------------
def test_checkin_only_produces_checkin_state():
    rows = classify_cleaning_for_date([_sb("2", "401", "2026-09-03", "2026-09-05")], "2026-09-03")
    row = _row_for("401", rows)
    assert row.status == "CHECKIN"
    assert row.arriving_guest.booking_id == "2"
    assert row.departing_guest is None


# ---------------- stayover-only ----------------
def test_stayover_only_produces_stayover_state():
    rows = classify_cleaning_for_date([_sb("3", "401", "2026-09-01", "2026-09-05", adults=2)], "2026-09-03")
    row = _row_for("401", rows)
    assert row.status == "STAYOVER"
    assert row.staying_guest.booking_id == "3"
    assert row.staying_guest.adults == 2


# ---------------- same physical room, checkout + checkin => TURNOVER (1 row) ----------------
def test_same_room_checkout_and_checkin_produces_turnover_as_one_row():
    bookings = [
        _sb("4", "401", "2026-09-01", "2026-09-03"),  # checkout on 09-03
        _sb("5", "401", "2026-09-03", "2026-09-06"),  # checkin on 09-03
    ]
    rows = classify_cleaning_for_date(bookings, "2026-09-03")
    row = _row_for("401", rows)
    assert row.status == "TURNOVER"
    assert row.departing_guest.booking_id == "4"
    assert row.arriving_guest.booking_id == "5"
    # 2026-09撤回: TURNOVERの自動instruction「入替」は生成しない(ステータス列で
    # IN表示すれば十分。「指: 入替」は冗長だった)。手動overrideのみ表示する。
    assert row.source_instruction == ""


def test_turnover_is_not_split_into_separate_checkout_and_checkin_rows():
    bookings = [
        _sb("4", "401", "2026-09-01", "2026-09-03"),
        _sb("5", "401", "2026-09-03", "2026-09-06"),
    ]
    rows = classify_cleaning_for_date(bookings, "2026-09-03")
    matches = [r for r in rows if r.room_number == "401"]
    assert len(matches) == 1


def test_different_rooms_do_not_turn_into_turnover():
    """checkout at room 401 and checkin at room 402 on the same day must stay two
    separate CHECKOUT/CHECKIN rows, never merge into a TURNOVER for either room."""
    bookings = [
        _sb("4", "401", "2026-09-01", "2026-09-03"),  # checkout at 401
        _sb("5", "402", "2026-09-03", "2026-09-06"),  # checkin at 402 (different room)
    ]
    rows = classify_cleaning_for_date(bookings, "2026-09-03")
    assert _row_for("401", rows).status == "CHECKOUT"
    assert _row_for("402", rows).status == "CHECKIN"


# ---------------- cancelled exclusion ----------------
def test_cancelled_booking_does_not_occupy_the_room_leaves_it_vacant():
    rows = classify_cleaning_for_date(
        [_sb("7", "401", "2026-09-01", "2026-09-03", status="cancelled")], "2026-09-03")
    row = _row_for("401", rows)
    assert row.status == "VACANT"


def test_cancelled_coexisting_with_real_activity_in_a_different_room_is_independent():
    bookings = [
        _sb("8", "401", "2026-09-01", "2026-09-03"),
        _sb("9", "402", "2026-09-01", "2026-09-03", status="cancelled"),
    ]
    rows = classify_cleaning_for_date(bookings, "2026-09-03")
    assert _row_for("401", rows).status == "CHECKOUT"
    assert _row_for("402", rows).status == "VACANT"


# ---------------- room_number unresolved => UNASSIGNED, never guessed into the 18-room grid ----------------
def test_booking_with_no_room_number_is_unassigned_not_placed_in_any_room_row():
    bookings = [_sb("10", None, "2026-09-01", "2026-09-03")]
    rows = classify_cleaning_for_date(bookings, "2026-09-03")
    unassigned = [r for r in rows if r.status == "UNASSIGNED"]
    assert len(unassigned) == 1
    assert unassigned[0].departing_guest.booking_id == "10"
    assert unassigned[0].room_number is None
    # UNASSIGNED行があっても18室の通常行数は変わらない(混ぜない)。
    assert len([r for r in rows if r.status != "UNASSIGNED"]) == 18


def test_booking_with_room_number_outside_the_canonical_18_is_unassigned():
    """Beds24側の値が誤って旧客室番号(301等)を指していても、正しく認識しない。"""
    bookings = [_sb("11", "301", "2026-09-01", "2026-09-03")]
    rows = classify_cleaning_for_date(bookings, "2026-09-03")
    assert any(r.status == "UNASSIGNED" and r.departing_guest.booking_id == "11" for r in rows)
    assert "301" not in [r.room_number for r in rows if r.status != "UNASSIGNED"]


# ---------------- JST boundary: date-string comparisons, no off-by-one ----------------
def test_jst_boundary_checkin_date_exactly_on_target_is_checkin_not_stayover():
    rows = classify_cleaning_for_date([_sb("12", "401", "2026-09-03", "2026-09-04")], "2026-09-03")
    assert _row_for("401", rows).status == "CHECKIN"


def test_jst_boundary_day_before_checkin_is_vacant_not_checkin():
    rows = classify_cleaning_for_date([_sb("13", "401", "2026-09-04", "2026-09-06")], "2026-09-03")
    assert _row_for("401", rows).status == "VACANT"


# ---------------- night progress (泊数) ----------------
def test_compute_night_progress_examples_from_spec():
    assert compute_night_progress("2026-08-29", "2026-08-31", "2026-08-29") == (1, 2)
    assert compute_night_progress("2026-08-29", "2026-08-31", "2026-08-30") == (2, 2)
    assert compute_night_progress("2026-08-29", "2026-08-31", "2026-08-31") == (2, 2)  # checkout日


def test_compute_night_progress_three_night_stay():
    assert compute_night_progress("2026-08-29", "2026-09-01", "2026-08-29") == (1, 3)
    assert compute_night_progress("2026-08-29", "2026-09-01", "2026-08-30") == (2, 3)
    assert compute_night_progress("2026-08-29", "2026-09-01", "2026-08-31") == (3, 3)
    assert compute_night_progress("2026-08-29", "2026-09-01", "2026-09-01") == (3, 3)  # checkout日


def test_compute_night_progress_date_outside_range_is_none():
    assert compute_night_progress("2026-08-29", "2026-08-31", "2026-09-05") == (None, 2)


def test_compute_night_progress_bad_input_is_none():
    assert compute_night_progress("not-a-date", "2026-08-31", "2026-08-30") == (None, None)


def test_turnover_row_night_progress_reflects_the_arriving_guest_first_night():
    bookings = [
        _sb("4", "401", "2026-08-28", "2026-08-30"),  # departing (2 nights, ends today)
        _sb("5", "401", "2026-08-30", "2026-09-02"),  # arriving (3 nights, starts today)
    ]
    rows = classify_cleaning_for_date(bookings, "2026-08-30")
    row = _row_for("401", rows)
    assert row.current_night_index == 1
    assert row.total_nights == 3


# ---------------- room master coverage: no bookings at all -> all 18 VACANT ----------------
def test_no_bookings_at_all_yields_18_vacant_rooms():
    rows = classify_cleaning_for_date([], "2026-09-03")
    assert len(rows) == 18
    assert all(r.status == "VACANT" for r in rows)


# ---------------- 2026-09: cleaning_extra_by_booking_id threading ----------------
# (guest_notice/children_age_*/onsite_payment_*はStaffBookingRecordではなく別経路
# extract_cleaning_extra()から来るため、_guest_info()が正しく反映することを確認する)

def test_guest_info_defaults_new_fields_when_no_extra_map_given():
    bookings = [_sb("1", "401", "2026-08-30", "2026-09-01")]  # default adults=2, children=0
    rows = classify_cleaning_for_date(bookings, "2026-08-30")
    row = _row_for("401", rows)
    guest = row.arriving_guest
    assert guest.guest_notice is None
    assert guest.children_age_7plus_count is None
    assert guest.children_age_data_available is False
    # extraが無い場合のfallbackはadults+children(布団不足より多めが安全)。
    assert guest.bedding_guest_count == 2
    assert guest.payment_due_at_property is False
    assert guest.amount_due_at_property is None


def test_guest_info_picks_up_cleaning_extra_by_booking_id():
    bookings = [_sb("1", "401", "2026-08-30", "2026-09-01")]
    extra = {"1": {
        "guest_notice": "静かな部屋希望",
        "children_age_7plus_count": None,
        "children_age_data_available": False,
        "bedding_guest_count": 3,
        "payment_due_at_property": True,
        "amount_due_at_property": 18000,
    }}
    rows = classify_cleaning_for_date(bookings, "2026-08-30", extra)
    guest = _row_for("401", rows).arriving_guest
    assert guest.guest_notice == "静かな部屋希望"
    assert guest.bedding_guest_count == 3
    assert guest.payment_due_at_property is True
    assert guest.amount_due_at_property == 18000


def test_guest_info_extra_lookup_is_scoped_to_the_matching_booking_id_only():
    """複数予約が混在しても、無関係なbooking_idのextraを誤って拾わない。"""
    bookings = [
        _sb("1", "401", "2026-08-30", "2026-09-01"),
        _sb("2", "402", "2026-08-30", "2026-09-01"),
    ]
    extra = {"1": {
        "guest_notice": "401専用のお知らせ",
        "children_age_7plus_count": None, "children_age_data_available": False,
        "bedding_guest_count": 2,
        "payment_due_at_property": False, "amount_due_at_property": None,
    }}
    rows = classify_cleaning_for_date(bookings, "2026-08-30", extra)
    assert _row_for("401", rows).arriving_guest.guest_notice == "401専用のお知らせ"
    assert _row_for("402", rows).arriving_guest.guest_notice is None


# ---------------- 到着時刻: 明示値優先 + commentsからのfallback (要件9・13) ----------------
# Cleaning DTO専用の解決。StaffBookingRecord.arrival_time(Daily Ops/宿泊者名簿が
# 使う共有schema)は一切変更していない。

def _extra(**overrides):
    base = {
        "guest_notice": None, "arrival_time_fallback": None,
        "children_age_7plus_count": None, "children_age_known_count": 0,
        "children_age_data_available": False, "bedding_guest_count": 2,
        "payment_due_at_property": False, "amount_due_at_property": None,
    }
    base.update(overrides)
    return base


def test_arrival_time_falls_back_to_comment_window_when_explicit_is_empty():
    """実データ46予約が該当(comments に時間帯があり、明示arrivalTimeは全件空)。"""
    bookings = [_sb("1", "401", "2026-09-03", "2026-09-04", arrival_time=None)]
    extra = {"1": _extra(arrival_time_fallback="17:00-18:00")}
    rows = classify_cleaning_for_date(bookings, "2026-09-03", extra)
    assert _row_for("401", rows).arriving_guest.arrival_time == "17:00-18:00"


def test_explicit_arrival_time_always_wins_over_the_comment_window():
    """要件13: 明示値をcommentsで上書きしない。"""
    bookings = [_sb("1", "401", "2026-09-03", "2026-09-04", arrival_time="17:30")]
    extra = {"1": _extra(arrival_time_fallback="17:00-18:00")}
    rows = classify_cleaning_for_date(bookings, "2026-09-03", extra)
    assert _row_for("401", rows).arriving_guest.arrival_time == "17:30"


def test_arrival_time_stays_none_when_neither_source_has_a_value():
    bookings = [_sb("1", "401", "2026-09-03", "2026-09-04", arrival_time=None)]
    rows = classify_cleaning_for_date(bookings, "2026-09-03", {"1": _extra()})
    assert _row_for("401", rows).arriving_guest.arrival_time is None


def test_missing_extra_entry_falls_back_safely_without_arrival_or_crash():
    """extraにbooking_idが無い取りこぼしケースでも例外にならず、明示値だけを使う。"""
    bookings = [_sb("1", "401", "2026-09-03", "2026-09-04", arrival_time="16:00")]
    rows = classify_cleaning_for_date(bookings, "2026-09-03", {})
    assert _row_for("401", rows).arriving_guest.arrival_time == "16:00"
