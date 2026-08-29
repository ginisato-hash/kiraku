"""ops.cleaning_classifier.classify_cleaning_for_date() の状態分類テスト。

Beds24は部屋タイプ(タイプ単位のqty)のみを公開し、物理部屋番号を持たないため、
room_numberは常にNoneのまま(既知の制約。room_type_metrics側とは無関係)。
"""
from yuge_finance.ops.cleaning_classifier import classify_cleaning_for_date
from yuge_finance.ops.schema import StaffBookingRecord

ROOM_TYPES = {
    "single": {"label": "シングル", "capacity_rooms": 2, "match": {"room_ids": ["100"]}},
    "twin": {"label": "ツイン", "capacity_rooms": 3, "match": {"room_ids": ["200"]}},
    "unknown": {"label": "未分類", "capacity_rooms": 0, "match": {"room_ids": []}},
}


def _sb(bid, room_type_key, checkin, checkout, status="confirmed", adults=2, children=0):
    return StaffBookingRecord(
        booking_id=bid, room_type_key=room_type_key,
        room_type_label=ROOM_TYPES.get(room_type_key, {}).get("label", room_type_key),
        checkin_date=checkin, checkout_date=checkout, status=status,
        adults=adults, children=children, total_guests=adults + children,
    )


def _rows_for(room_type_key, rows):
    return [r for r in rows if r.room_type_key == room_type_key]


# ---------------- checkout-only ----------------
def test_checkout_only_produces_checkout_state():
    bookings = [_sb("1", "single", "2026-09-01", "2026-09-03")]
    rows = classify_cleaning_for_date(bookings, "2026-09-03", ROOM_TYPES)
    single_rows = _rows_for("single", rows)
    assert len(single_rows) == 1
    assert single_rows[0].state == "CHECKOUT"
    assert single_rows[0].checkout_booking_id == "1"
    assert single_rows[0].checkin_booking_id is None


# ---------------- checkin-only ----------------
def test_checkin_only_produces_checkin_state():
    bookings = [_sb("2", "single", "2026-09-03", "2026-09-05")]
    rows = classify_cleaning_for_date(bookings, "2026-09-03", ROOM_TYPES)
    single_rows = _rows_for("single", rows)
    assert len(single_rows) == 1
    assert single_rows[0].state == "CHECKIN"
    assert single_rows[0].checkin_booking_id == "2"
    assert single_rows[0].checkout_booking_id is None


# ---------------- stayover-only ----------------
def test_stayover_only_produces_stayover_state():
    bookings = [_sb("3", "single", "2026-09-01", "2026-09-05")]
    rows = classify_cleaning_for_date(bookings, "2026-09-03", ROOM_TYPES)
    single_rows = _rows_for("single", rows)
    assert len(single_rows) == 1
    assert single_rows[0].state == "STAYOVER"
    assert single_rows[0].adults == 2


# ---------------- same-day checkout + checkin => TURNOVER ----------------
def test_same_day_checkout_and_checkin_produces_turnover():
    bookings = [
        _sb("4", "single", "2026-09-01", "2026-09-03"),  # checkout on 09-03
        _sb("5", "single", "2026-09-03", "2026-09-06"),  # checkin on 09-03
    ]
    rows = classify_cleaning_for_date(bookings, "2026-09-03", ROOM_TYPES)
    single_rows = _rows_for("single", rows)
    assert len(single_rows) == 1
    assert single_rows[0].state == "TURNOVER"
    assert single_rows[0].checkout_booking_id == "4"
    assert single_rows[0].checkin_booking_id == "5"
    # 清掃準備の対象は「これから入居する」チェックイン側の人数を使う。
    assert single_rows[0].adults == 2


# ---------------- vacant (何もイベントが無い) ----------------
def test_vacant_when_no_bookings_touch_the_date():
    bookings = [_sb("6", "single", "2026-09-10", "2026-09-12")]
    rows = classify_cleaning_for_date(bookings, "2026-09-03", ROOM_TYPES)
    single_rows = _rows_for("single", rows)
    assert len(single_rows) == 1
    assert single_rows[0].state == "VACANT"


# ---------------- cancelled-only-for-that-slot => CANCELLED (not VACANT) ----------------
def test_cancelled_only_produces_cancelled_not_vacant():
    bookings = [_sb("7", "single", "2026-09-01", "2026-09-03", status="cancelled")]
    rows = classify_cleaning_for_date(bookings, "2026-09-03", ROOM_TYPES)
    single_rows = _rows_for("single", rows)
    assert len(single_rows) == 1
    assert single_rows[0].state == "CANCELLED"
    assert "7" in single_rows[0].notes


def test_cancelled_coexisting_with_real_activity_is_ignored_not_double_counted():
    """同タイプに実稼働(チェックアウト)とキャンセルが共存する場合、キャンセルは
    無視され、実稼働のみが行として出力される(どの物理室がキャンセルの影響を
    受けたか判別できないため。known limitation)。"""
    bookings = [
        _sb("8", "twin", "2026-09-01", "2026-09-03"),
        _sb("9", "twin", "2026-09-01", "2026-09-03", status="cancelled"),
    ]
    rows = classify_cleaning_for_date(bookings, "2026-09-03", ROOM_TYPES)
    twin_rows = _rows_for("twin", rows)
    assert len(twin_rows) == 1
    assert twin_rows[0].state == "CHECKOUT"
    assert twin_rows[0].checkout_booking_id == "8"


# ---------------- room_type unresolvable => UNASSIGNED ----------------
def test_unknown_room_type_produces_unassigned():
    bookings = [_sb("10", "unknown", "2026-09-01", "2026-09-03")]
    rows = classify_cleaning_for_date(bookings, "2026-09-03", ROOM_TYPES)
    unknown_rows = _rows_for("unknown", rows)
    assert len(unknown_rows) == 1
    assert unknown_rows[0].state == "UNASSIGNED"
    assert unknown_rows[0].checkout_booking_id == "10"


# ---------------- capacity>1: 複数同時イベントは個別行に分離される ----------------
def test_multiple_simultaneous_bookings_same_type_produce_distinct_rows():
    bookings = [
        _sb("11", "twin", "2026-09-01", "2026-09-03"),   # checkout
        _sb("12", "twin", "2026-09-02", "2026-09-03"),   # checkout
        _sb("13", "twin", "2026-09-03", "2026-09-05"),   # checkin
        _sb("14", "twin", "2026-08-30", "2026-09-06"),   # stayover
    ]
    rows = classify_cleaning_for_date(bookings, "2026-09-03", ROOM_TYPES)
    twin_rows = _rows_for("twin", rows)
    # 2 checkouts + 1 checkin => 1 TURNOVER(ペア化) + 1 CHECKOUT(余り) + 1 STAYOVER = 3行
    assert len(twin_rows) == 3
    states = sorted(r.state for r in twin_rows)
    assert states == ["CHECKOUT", "STAYOVER", "TURNOVER"]
    # 全ての物理部屋番号はNoneのまま(判別不能な既知の制約)。
    assert all(r.room_number is None for r in twin_rows)


# ---------------- unconfigured/zero-capacity types don't appear when nothing touches them ----------------
def test_zero_capacity_type_with_no_events_is_not_emitted():
    bookings = [_sb("15", "single", "2026-09-01", "2026-09-03")]
    rows = classify_cleaning_for_date(bookings, "2026-09-03", ROOM_TYPES)
    assert _rows_for("unknown", rows) == []
