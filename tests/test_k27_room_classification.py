"""K27冬季5室の分類・期間別capacity(旧18室/休館0室/K27 14室)・物理unit mappingの固定テスト。

正本: zao-kiraku-website Issue #31 / catalog.ts。T06(ドミトリー)は対象外で未登録。
"""
import json
from datetime import date

from yuge_finance import db, monthly
from yuge_finance.accounting import beds24_revenue_logic as brl
from yuge_finance.accounting import room_type_metrics as rtm
from yuge_finance.normalize.schema import BookingRecord
from yuge_finance.ops.extract import load_room_unit_mapping
from yuge_finance.reports import bi_export

EXCLUDE = ["cancelled", "canceled", "black"]
K27 = {
    "737046": ("k27_t01", "1ベッドルーム アパートメント", 4),
    "737050": ("k27_t02", "2ベッドルーム アパートメント", 3),
    "737051": ("k27_t03", "ツインルーム（ソファベッド付）", 3),
    "737052": ("k27_t04", "シングルルーム（ソファベッド付）", 2),
    "737053": ("k27_t05", "広め和洋室", 2),
}
LEGACY_KEYS = {"single_toilet", "twin_toilet", "twin_bath", "family_washitsu"}


def _b(bid, checkin, checkout, room_id, gross=30000, created="", rooms=1):
    r = BookingRecord(booking_id=bid, checkin_date=checkin, checkout_date=checkout,
                      room_id=room_id, rooms=rooms, gross_revenue=gross, status="confirmed")
    r.created_at_raw = created
    return r.finalize()


def _metrics(bookings, month):
    return rtm.calculate_room_type_metrics(bookings, month, rtm.load_room_type_config(), EXCLUDE)


def _active_keys(m):
    return {r["room_type"] for r in m["room_type_daily_occupancy"]}


# A/B classification + exact labels
def test_k27_room_ids_classify_to_keys_and_exact_labels():
    cfg = rtm.load_room_type_config()
    for rid, (key, label, _cap) in K27.items():
        assert rtm.classify_room_type(_b("1", "2027-01-10", "2027-01-11", rid), cfg) == key
        assert cfg[key]["label"] == label


def test_unknown_room_id_stays_unknown_and_legacy_unchanged():
    cfg = rtm.load_room_type_config()
    assert rtm.classify_room_type(_b("1", "2027-01-10", "2027-01-11", "999999"), cfg) == "unknown"
    assert rtm.classify_room_type(_b("2", "2026-10-10", "2026-10-11", "686762"), cfg) == "twin_toilet"


def test_t06_not_registered():
    cfg = rtm.load_room_type_config()
    assert set(cfg) == LEGACY_KEYS | {v[0] for v in K27.values()} | {"unknown"}


# D legacy / E closure / F K27 months
def test_legacy_month_is_18_rooms_and_only_legacy_types():
    m = _metrics([], "2026-10")
    assert m["available_room_nights"] == 18 * 31
    assert _active_keys(m) == LEGACY_KEYS
    assert m["room_type_metrics_warnings"] == []


def test_closure_months_have_zero_capacity_and_no_active_types():
    for month, days in (("2026-11", 30), ("2026-12", 31)):
        m = _metrics([], month)
        assert m["available_room_nights"] == 0, days
        assert _active_keys(m) == set()
        assert m["room_type_metrics_warnings"] == []


def test_k27_month_is_14_rooms_and_only_k27_types():
    m = _metrics([], "2027-01")
    assert m["available_room_nights"] == 14 * 31 == 434
    assert _active_keys(m) == {v[0] for v in K27.values()}
    assert m["room_type_metrics_warnings"] == []
    day1 = [r for r in m["room_type_daily_occupancy"] if r["date"] == "2027-01-01"]
    assert sum(r["available_rooms"] for r in day1) == 14


def test_boundary_days_are_half_open():
    cfg = rtm.load_room_type_config()
    assert rtm.capacity_on(cfg["twin_toilet"], "2026-10-31") == 10
    assert rtm.capacity_on(cfg["twin_toilet"], "2026-11-01") == 0
    assert rtm.capacity_on(cfg["k27_t01"], "2026-12-31") == 0
    assert rtm.capacity_on(cfg["k27_t01"], "2027-01-01") == 4


# G revenue mix
def test_k27_revenue_goes_to_k27_type_not_unknown():
    bookings = [_b("1", "2027-01-10", "2027-01-12", "737046", gross=60000),
                _b("2", "2027-01-10", "2027-01-11", "737053", gross=20000)]
    m = _metrics(bookings, "2027-01")
    mix = {r["room_type"]: r for r in m["room_type_revenue_mix"]}
    assert "unknown" not in mix
    assert mix["k27_t01"]["revenue"] == 60000 and mix["k27_t01"]["sold_room_nights"] == 2
    assert mix["k27_t05"]["revenue"] == 20000
    assert abs(sum(r["share"] for r in mix.values()) - 100.0) < 0.5
    assert not any("未分類" in w for w in m["room_type_metrics_warnings"])


def test_unknown_with_real_booking_still_warns_and_shows():
    m = _metrics([_b("1", "2027-01-10", "2027-01-11", "999999")], "2027-01")
    assert any("未分類のroomId" in w for w in m["room_type_metrics_warnings"])
    assert "unknown" in {r["room_type"] for r in m["room_type_revenue_mix"]}


# C daily summary regression
def test_daily_global_summary_details_carry_k27_labels(tmp_path, monkeypatch):
    monkeypatch.setattr(brl, "jst_today", lambda: date(2026, 10, 7))
    conn = db.connect(tmp_path / "t.sqlite")
    db.upsert(conn, "beds24_bookings", [
        _b("1", "2027-01-10", "2027-01-11", "737046", created="2026-10-07T01:00:00Z"),
        _b("2", "2027-01-20", "2027-01-21", "737052", created="2026-10-07T02:00:00Z"),
    ])
    ctx = monthly.assemble("2026-10", conn)
    sev = {"all_ok": True, "critical": [], "warnings": []}
    bi_export.write_all("2026-10", ctx, checks=[], wb_checks=[], severity=sev, out_dir=tmp_path)
    snap = json.loads((tmp_path / "bi" / "bi_snapshot.json").read_text(encoding="utf-8"))
    details = {d["booking_id"]: d for d in snap["daily_global_summary"]["today_new_bookings"]["details"]}
    assert details["1"]["room_type"] == "1ベッドルーム アパートメント"
    assert details["1"]["room_type_key"] == "k27_t01"
    assert details["2"]["room_type"] == "シングルルーム（ソファベッド付）"
    conn.close()


# H unit mapping
def test_k27_unit_mapping_14_units_complete_and_unique():
    mapping = load_room_unit_mapping()
    expected = {
        "k27_t01": {"1": "601", "2": "602", "3": "403", "4": "303"},
        "k27_t02": {"1": "502", "2": "402", "3": "302"},
        "k27_t03": {"1": "501", "2": "401", "3": "301"},
        "k27_t04": {"1": "604", "2": "504"},
        "k27_t05": {"1": "603", "2": "503"},
    }
    for key, units in expected.items():
        assert mapping[key] == units
    rooms = [n for k in expected for n in mapping[k].values()]
    assert len(rooms) == 14 == len(set(rooms))
    cfg = rtm.load_room_type_config()
    for key, units in expected.items():
        assert len(units) == cfg[key]["capacity_rooms"]
