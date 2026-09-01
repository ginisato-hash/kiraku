"""ops.build.build_staff_ops_snapshot() の統合テスト（会計/売上とは完全に独立）。

実configの部屋タイプroom_id(config/kiraku_room_types.yml)をそのまま使う
(既存のtest_today_new_bookings.py等と同じ規約: 実室IDは公開情報であり、
テストで使っても実データそのものではない)。氏名は架空の一般的な日本語名のみ使用する。
"""
import json
import re
from datetime import date

from yuge_finance.ops import build as ops_build
from yuge_finance.ops.schema import assert_no_financial_keys

# config/kiraku_room_types.yml 実データ(2026-07-10確認済み)
SINGLE_TOILET_ROOM_ID = "685761"
TWIN_TOILET_ROOM_ID = "686762"


def _write_month(tmp_path, month, bookings):
    d = tmp_path / "raw" / "beds24" / month
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{month}.json").write_text(json.dumps(bookings, ensure_ascii=False), encoding="utf-8")


def _raw(bid, room_id, arrival, departure, status="confirmed", first="花子", last="架空",
         adult=2, child=0, unit_id=None):
    raw = {
        "id": bid, "roomId": room_id, "arrival": arrival, "departure": departure,
        "status": status, "firstName": first, "lastName": last,
        "numAdult": adult, "numChild": child, "refererEditable": "Booking.com",
    }
    if unit_id is not None:
        raw["unitId"] = unit_id
    return raw


def test_build_staff_ops_snapshot_groups_arrivals_departures_stayovers(tmp_path):
    bookings = [
        _raw("A", SINGLE_TOILET_ROOM_ID, "2026-09-01", "2026-09-02"),
        _raw("B", SINGLE_TOILET_ROOM_ID, "2026-09-02", "2026-09-04"),
        _raw("C", TWIN_TOILET_ROOM_ID, "2026-09-01", "2026-09-02", status="cancelled"),
    ]
    _write_month(tmp_path, "2026-09", bookings)

    target_dates = ["2026-09-01", "2026-09-02", "2026-09-03"]
    snapshot = ops_build.build_staff_ops_snapshot(target_dates, data_root=tmp_path)

    d1 = snapshot["dates"]["2026-09-01"]
    assert [b["booking_id"] for b in d1["arrivals"]] == ["A"]
    assert d1["departures"] == []
    assert d1["stayovers"] == []

    d2 = snapshot["dates"]["2026-09-02"]
    assert [b["booking_id"] for b in d2["departures"]] == ["A"]
    assert [b["booking_id"] for b in d2["arrivals"]] == ["B"]

    d3 = snapshot["dates"]["2026-09-03"]
    assert d3["arrivals"] == []
    assert d3["departures"] == []
    assert [b["booking_id"] for b in d3["stayovers"]] == ["B"]

    # キャンセル済み(C)はarrivals/departuresには一切出ない。
    all_ids_seen = set()
    for bucket in ("arrivals", "departures", "stayovers"):
        for d in snapshot["dates"].values():
            all_ids_seen |= {b["booking_id"] for b in d[bucket]}
    assert "C" not in all_ids_seen


def test_build_staff_ops_snapshot_cleaning_rooms_present_for_each_date(tmp_path):
    # single_toilet unit "1" -> 実物理客室番号"607"(config/kiraku_room_unit_mapping.yml、
    # 2026-08-30にBeds24 API実データで確認済み)。
    bookings = [_raw("A", SINGLE_TOILET_ROOM_ID, "2026-09-01", "2026-09-02", unit_id=1)]
    _write_month(tmp_path, "2026-09", bookings)
    snapshot = ops_build.build_staff_ops_snapshot(["2026-09-02"], data_root=tmp_path)
    rooms = snapshot["dates"]["2026-09-02"]["cleaning"]["rooms"]
    assert len(rooms) == 18  # KIRAKU_ROOM_ORDER全室分、必ず1日18行
    room_607 = [r for r in rooms if r["room_number"] == "607"]
    assert len(room_607) == 1
    assert room_607[0]["status"] == "CHECKOUT"
    assert room_607[0]["departing_guest"]["booking_id"] == "A"


def test_build_staff_ops_snapshot_top_level_shape(tmp_path):
    snapshot = ops_build.build_staff_ops_snapshot(["2026-09-01"], data_root=tmp_path)
    assert snapshot["property_name"] == "喜らく"
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+09:00$", snapshot["generated_at_jst"])
    assert set(snapshot["dates"]["2026-09-01"].keys()) == {
        "arrivals", "departures", "stayovers", "cleaning"}


def test_build_staff_ops_snapshot_cleaning_extra_flows_through_end_to_end(tmp_path):
    """raw Beds24 dict(guestComments/invoiceItems) -> cleaning DTOのguest_notice/
    onsite_payment_*まで、実際のbuild_staff_ops_snapshot()経路で一気通貫することを確認する。"""
    booking = _raw("A", SINGLE_TOILET_ROOM_ID, "2026-09-01", "2026-09-02", unit_id=1)
    booking["guestComments"] = "到着が遅くなります"
    booking["price"] = 13000
    booking["invoiceItems"] = [
        {"type": "charge", "description": "[ROOMNAME1]", "lineTotal": 13000},
        {"type": "payment", "description": "現地支払い", "lineTotal": 0},
    ]
    _write_month(tmp_path, "2026-09", [booking])
    snapshot = ops_build.build_staff_ops_snapshot(["2026-09-01"], data_root=tmp_path)
    rooms = snapshot["dates"]["2026-09-01"]["cleaning"]["rooms"]
    room_607 = [r for r in rooms if r["room_number"] == "607"][0]
    guest = room_607["arriving_guest"]
    assert guest["guest_notice"] == "到着が遅くなります"
    assert guest["payment_due_at_property"] is True
    assert guest["amount_due_at_property"] == 13000


def test_daily_ops_arrivals_never_carry_the_cleaning_only_extra_fields(tmp_path):
    """guest_notice/onsite_payment_*はCleaning DTO専用 — StaffBookingRecord自体には
    追加していないため、Daily Ops(arrivals/departures/stayovers、宿泊者名簿の元データ)
    の出力にはそもそも一切現れない(構造的な分離の回帰確認)。"""
    booking = _raw("A", SINGLE_TOILET_ROOM_ID, "2026-09-01", "2026-09-02", unit_id=1)
    booking["guestComments"] = "到着が遅くなります"
    booking["price"] = 13000
    booking["invoiceItems"] = [
        {"type": "charge", "description": "[ROOMNAME1]", "lineTotal": 13000},
        {"type": "payment", "description": "現地支払い", "lineTotal": 0},
    ]
    _write_month(tmp_path, "2026-09", [booking])
    snapshot = ops_build.build_staff_ops_snapshot(["2026-09-01"], data_root=tmp_path)
    arrival = snapshot["dates"]["2026-09-01"]["arrivals"][0]
    for forbidden_key in ("guest_notice", "payment_due_at_property", "amount_due_at_property",
                          "children_age_7plus_count", "children_age_data_available"):
        assert forbidden_key not in arrival, f"{forbidden_key} must not leak into Daily Ops arrivals"


def test_build_staff_ops_snapshot_recursively_passes_financial_key_guard(tmp_path):
    bookings = [_raw("A", SINGLE_TOILET_ROOM_ID, "2026-09-01", "2026-09-02")]
    _write_month(tmp_path, "2026-09", bookings)
    snapshot = ops_build.build_staff_ops_snapshot(["2026-09-01", "2026-09-02"], data_root=tmp_path)
    assert_no_financial_keys(snapshot)  # 例外が出なければOK


def test_missing_raw_file_yields_empty_but_valid_snapshot(tmp_path):
    """該当月のraw JSONがまだ存在しない(未取得)場合でもエラーにせず空のバケットを返す。
    予約が1件も無いため、清掃状態はKIRAKU_ROOM_ORDERの18室すべてがVACANTになる。"""
    snapshot = ops_build.build_staff_ops_snapshot(["2026-12-25"], data_root=tmp_path)
    d = snapshot["dates"]["2026-12-25"]
    assert d["arrivals"] == d["departures"] == d["stayovers"] == []
    rooms = d["cleaning"]["rooms"]
    assert len(rooms) == 18
    assert all(r["status"] == "VACANT" for r in rooms)


# ---------------- write_staff_ops_snapshot atomic write ----------------
def test_write_staff_ops_snapshot_writes_valid_json(tmp_path):
    snapshot = {"generated_at_jst": "2026-09-01T00:00:00+09:00", "property_name": "喜らく",
                "dates": {}}
    out_path = tmp_path / "ops" / "staff_ops_snapshot.json"
    ops_build.write_staff_ops_snapshot(snapshot, out_path)
    assert out_path.exists()
    loaded = json.loads(out_path.read_text(encoding="utf-8"))
    assert loaded == snapshot
    assert not (out_path.parent / (out_path.name + ".tmp")).exists()


def test_write_staff_ops_snapshot_overwrites_existing_file(tmp_path):
    out_path = tmp_path / "ops" / "staff_ops_snapshot.json"
    ops_build.write_staff_ops_snapshot({"v": 1}, out_path)
    ops_build.write_staff_ops_snapshot({"v": 2}, out_path)
    assert json.loads(out_path.read_text(encoding="utf-8")) == {"v": 2}


# ---------------- JST 日付境界 ----------------
def test_default_target_dates_is_4_day_window_yesterday_through_day_after_tomorrow():
    today = date(2026, 9, 15)
    dates = ops_build.default_target_dates(today)
    assert dates == ["2026-09-14", "2026-09-15", "2026-09-16", "2026-09-17"]


def test_default_target_dates_crosses_month_boundary_correctly():
    today = date(2026, 7, 31)
    dates = ops_build.default_target_dates(today)
    assert dates == ["2026-07-30", "2026-07-31", "2026-08-01", "2026-08-02"]


def test_default_target_dates_crosses_year_boundary_correctly():
    today = date(2026, 12, 31)
    dates = ops_build.default_target_dates(today)
    assert dates == ["2026-12-30", "2026-12-31", "2027-01-01", "2027-01-02"]


def test_jst_now_iso_has_plus_nine_offset():
    """generated_at_jstロジックが常に+09:00オフセットを付与することを固定する
    (accounting/beds24_revenue_logic.pyのJST定義パターンと同じ独立ローカル定義)。"""
    iso = ops_build.jst_now_iso()
    assert iso.endswith("+09:00") or "+09:00" in iso


def test_jst_today_uses_jst_not_utc_near_midnight_boundary():
    """UTC/JSTの日付跨ぎ境界を、実際の壁時計に依存せずタイムスタンプ構築で検証する。
    UTC 15:30 は JST(UTC+9) では翌日00:30になるため、日付が繰り上がることを確認する。"""
    from datetime import datetime, timedelta, timezone
    utc_dt = datetime(2026, 7, 31, 15, 30, tzinfo=timezone.utc)
    jst_dt = utc_dt.astimezone(ops_build.JST)
    assert jst_dt.date() == date(2026, 8, 1)

    utc_dt_before = datetime(2026, 7, 31, 14, 30, tzinfo=timezone.utc)
    jst_dt_before = utc_dt_before.astimezone(ops_build.JST)
    assert jst_dt_before.date() == date(2026, 7, 31)
